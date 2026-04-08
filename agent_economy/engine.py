from __future__ import annotations

import threading
import time
import weakref
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from concurrent.futures import thread as _futures_thread
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from agent_economy.clearing import (
    Assignment,
    BidSubmission,
    choose_assignments,
    score_bid_breakdown,
)
from agent_economy.ledger import Ledger
from agent_economy.schemas import (
    ArtifactRef,
    Bid,
    DerivedState,
    DiscussionMessage,
    EventType,
    PaymentRule,
    TaskRuntime,
    TaskSpec,
    SubmissionKind,
    VerifyMode,
    VerifyStatus,
    WorkerRuntime,
)
from agent_economy.state import SettlementPolicy, replay_ledger


@dataclass(frozen=True)
class ReadyTask:
    spec: TaskSpec
    runtime: TaskRuntime


@dataclass(frozen=True)
class BidResult:
    bids: list[Bid] = field(default_factory=list)
    llm_usage: dict[str, int] | None = None
    model_ref: str | None = None
    discussion: str | None = None


class Bidder(Protocol):
    def get_bids(
        self,
        *,
        worker: WorkerRuntime,
        ready_tasks: Sequence[ReadyTask],
        round_id: int,
        discussion_history: Sequence[DiscussionMessage],
    ) -> BidResult: ...


@dataclass(frozen=True)
class RouterSelection:
    task_id: str
    worker_id: str
    notes: str | None = None


@dataclass(frozen=True)
class AssignmentDecision:
    selections: list[RouterSelection] = field(default_factory=list)
    llm_usage: dict[str, int] | None = None
    model_ref: str | None = None
    payload: dict[str, object] | None = None


class AssignmentPolicy(Protocol):
    def choose(
        self,
        *,
        round_id: int,
        ready_tasks: Sequence[ReadyTask],
        available_workers: Sequence[WorkerRuntime],
        discussion_history: Sequence[DiscussionMessage],
        cost_estimator: CostEstimator | None,
        excluded_pairs: set[tuple[str, str]],
    ) -> AssignmentDecision: ...


@dataclass(frozen=True)
class ExecutionOutcome:
    status: VerifyStatus
    notes: str | None = None
    patch_artifacts: list[ArtifactRef] = field(default_factory=list)
    submission_artifacts: list[ArtifactRef] = field(default_factory=list)
    verification_artifacts: list[ArtifactRef] = field(default_factory=list)
    sandbox_rel: str | None = None
    patch_kind: str | None = None
    submission_kind: SubmissionKind | None = None
    submission_preview: str | None = None
    llm_usage: dict[str, int] | None = None
    verification_summary: str | None = None

    def with_status(self, status: VerifyStatus, notes: str | None = None) -> ExecutionOutcome:
        """Return a copy with a different status (and optionally notes)."""
        return ExecutionOutcome(
            status=status,
            notes=notes if notes is not None else self.notes,
            patch_artifacts=list(self.patch_artifacts),
            submission_artifacts=list(self.submission_artifacts),
            verification_artifacts=list(self.verification_artifacts),
            sandbox_rel=self.sandbox_rel,
            patch_kind=self.patch_kind,
            submission_kind=self.submission_kind,
            submission_preview=self.submission_preview,
            llm_usage=self.llm_usage,
            verification_summary=self.verification_summary,
        )

    @property
    def success(self) -> bool:
        return self.status == VerifyStatus.PASS


class Executor(Protocol):
    def execute(
        self,
        *,
        worker: WorkerRuntime,
        task: TaskSpec,
        bid: Bid,
        round_id: int,
        discussion_history: Sequence[DiscussionMessage],
    ) -> ExecutionOutcome: ...


class CostEstimator(Protocol):
    def expected_cost(
        self,
        *,
        worker: WorkerRuntime,
        task: TaskSpec,
        bid: Bid,
        round_id: int,
    ) -> float: ...

    def actual_cost(
        self,
        *,
        worker: WorkerRuntime,
        llm_usage: dict[str, int] | None,
    ) -> float: ...


class Verifier(Protocol):
    """Programmatic verification adapter.

    When provided to ClearinghouseEngine, called after execution to
    determine the final verify status -- overriding whatever the executor
    returned.  Intended for reward models, Prime Intellect evaluators,
    or any callable verification logic.
    """

    def verify(
        self,
        *,
        task: TaskSpec,
        worker: WorkerRuntime,
        outcome: ExecutionOutcome,
    ) -> VerifyStatus: ...


def _ready_task_ids(*, task_specs: dict[str, TaskSpec], tasks: dict[str, TaskRuntime]) -> list[str]:
    done = {tid for tid, t in tasks.items() if t.status == "DONE"}

    ready: list[str] = []
    for tid, rt in tasks.items():
        if rt.status != "TODO":
            continue
        spec = task_specs.get(tid)
        if spec is None:
            continue
        if int(rt.fail_count) >= int(spec.max_attempts):
            continue
        if all(dep in done for dep in spec.deps):
            ready.append(tid)
    return sorted(ready)


def _bump_bounty(*, task: TaskRuntime) -> int:
    cap = task.bounty_original * 2
    bumped = round(task.bounty_current * 1.15)
    bumped = max(task.bounty_current + 1, bumped)
    return min(cap, bumped)


def _penalty_amount(*, bounty: int, policy: SettlementPolicy) -> int:
    return min(policy.max_penalty, max(1, round(bounty * policy.penalty_fraction)))


def _confidence_penalty_amount(
    *,
    base_penalty: int,
    p_success: float,
    policy: SettlementPolicy,
) -> int:
    floor = float(getattr(policy, "confidence_penalty_floor", 0.5))
    floor = max(0.0, min(1.0, floor))
    max_multiplier = float(getattr(policy, "confidence_penalty_max_multiplier", 0.0))
    max_multiplier = max(0.0, max_multiplier)
    p = max(0.0, min(1.0, float(p_success)))
    if max_multiplier <= 0 or p <= floor or floor >= 1.0:
        return 0
    slope = (p - floor) / (1.0 - floor)
    return max(0, round(float(base_penalty) * max_multiplier * slope))


def _direct_penalty_amount(
    *,
    bounty: int,
    p_success: float,
    policy: SettlementPolicy,
) -> float:
    p = max(0.0, min(1.0, float(p_success)))
    return max(0.0, float(policy.penalty_fraction) * float(bounty) * (0.5 + p))


def _score_snapshot_payload(*, breakdown: dict[str, float] | None) -> dict[str, object] | None:
    if not breakdown:
        return None
    is_direct = bool(float(breakdown.get("mode_direct_penalty", 0.0)) > 0.0)
    has_retry_penalty = float(breakdown.get("retry_score_penalty", 0.0)) > 0.0
    formula = (
        "p_success*bounty - ask - expected_cost - (1-p_success)*expected_fail_penalty"
        if is_direct
        else "rep*p_success*bounty - ask - expected_cost - (1-p_success)*failure_penalty"
    )
    if has_retry_penalty:
        formula = f"{formula} - retry_score_penalty"
    return {
        "formula": formula,
        "penalty_mode": "direct_penalty" if is_direct else "reputation",
        "components": {
            "bounty": float(breakdown.get("bounty", 0.0)),
            "reputation": float(breakdown.get("reputation", 0.0)),
            "p_success": float(breakdown.get("p_success", 0.0)),
            "ask": float(breakdown.get("ask", 0.0)),
            "expected_cost": float(breakdown.get("expected_cost", 0.0)),
            "failure_penalty": float(breakdown.get("failure_penalty", 0.0)),
            "expected_fail_penalty": float(breakdown.get("expected_fail_penalty", 0.0)),
            "penalty_fraction": float(breakdown.get("penalty_fraction", 0.0)),
            "score_before_retry_penalty": float(breakdown.get("score_before_retry_penalty", 0.0)),
            "retry_score_penalty": float(breakdown.get("retry_score_penalty", 0.0)),
            "score": float(breakdown.get("score", 0.0)),
        },
    }


def _clamp_probability(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _default_forced_bid(*, task_id: str, bounty: int) -> Bid:
    return Bid(
        task_id=str(task_id),
        ask=max(1, int(round(float(bounty) * 0.40))),
        self_assessed_p_success=0.50,
        eta_minutes=90,
        notes="engine_fallback_forced_bid",
    )


def _retry_penalty_by_pair(
    *,
    events: Sequence,
    policy: SettlementPolicy,
) -> dict[tuple[str, str], float]:
    fraction = max(0.0, float(getattr(policy, "retry_score_penalty_fraction", 0.0) or 0.0))
    if fraction <= 0.0:
        return {}

    penalties: dict[tuple[str, str], float] = {}
    for event in events:
        if getattr(event, "type", None) != EventType.TASK_COMPLETED:
            continue
        payload = getattr(event, "payload", {}) or {}
        if str(payload.get("verify_status") or "") != VerifyStatus.FAIL.value:
            continue

        task_id = str(payload.get("task_id") or "").strip()
        worker_id = str(payload.get("worker_id") or "").strip()
        if not task_id or not worker_id:
            continue

        bid_payload = payload.get("bid") if isinstance(payload.get("bid"), dict) else {}
        p_success = _clamp_probability(
            float((bid_payload or {}).get("self_assessed_p_success", 0.0) or 0.0)
        )
        bounty = max(0.0, float(payload.get("bounty_current") or 0.0))
        if bounty <= 0.0:
            continue

        key = (task_id, worker_id)
        penalties[key] = float(penalties.get(key, 0.0) + (fraction * bounty * p_success))

    return penalties


def _failed_task_worker_pairs(*, events: Sequence) -> set[tuple[str, str]]:
    excluded_statuses = {
        VerifyStatus.FAIL.value,
        VerifyStatus.INFRA.value,
        VerifyStatus.TIMEOUT.value,
        VerifyStatus.FLAKE_SUSPECTED.value,
    }
    pairs: set[tuple[str, str]] = set()
    for event in events:
        if getattr(event, "type", None) != EventType.TASK_COMPLETED:
            continue
        payload = getattr(event, "payload", {}) or {}
        if str(payload.get("verify_status") or "").strip() not in excluded_statuses:
            continue
        task_id = str(payload.get("task_id") or "").strip()
        worker_id = str(payload.get("worker_id") or "").strip()
        if task_id and worker_id:
            pairs.add((task_id, worker_id))
    return pairs


def _worker_market_context_lines(
    *,
    worker: WorkerRuntime,
    ready_views: Sequence[ReadyTask],
    round_id: int,
    cost_estimator: CostEstimator | None,
) -> list[str]:
    lines: list[str] = []
    for ready in ready_views:
        task_id = str(ready.spec.id)
        expected_cost_hint = 0.0
        if cost_estimator is not None:
            try:
                expected_cost_hint = float(
                    cost_estimator.expected_cost(
                        worker=worker,
                        task=ready.spec,
                        bid=_default_forced_bid(
                            task_id=task_id, bounty=int(ready.runtime.bounty_current)
                        ),
                        round_id=int(round_id),
                    )
                )
            except Exception:
                expected_cost_hint = 0.0
        lines.append(f"- {task_id}: expected_cost_hint≈{expected_cost_hint:.2f}")
    return lines


def _infra_outcome(notes: str) -> ExecutionOutcome:
    return ExecutionOutcome(status=VerifyStatus.INFRA, notes=notes)


def _timeout_outcome(notes: str) -> ExecutionOutcome:
    return ExecutionOutcome(status=VerifyStatus.FAIL, notes=notes)


def _load_task_specs_from_events(*, events: Iterable) -> dict[str, TaskSpec]:
    specs: dict[str, TaskSpec] = {}
    for e in events:
        if getattr(e, "type", None) != EventType.TASK_CREATED:
            continue
        p = getattr(e, "payload", {}) or {}
        task_id = str(p.get("task_id") or "")
        if not task_id or task_id in specs:
            continue
        try:
            # TaskSpec expects acceptance as list[CommandSpec], so we allow a simplified
            # payload format and upgrade it here.
            acceptance = p.get("acceptance") or []
            hidden_acceptance = p.get("hidden_acceptance") or []
            spec = TaskSpec.model_validate(
                {
                    "id": task_id,
                    "title": p.get("title", task_id),
                    "description": p.get("description", ""),
                    "deps": p.get("deps", []),
                    "bounty": int(p.get("bounty", 1)),
                    "max_attempts": int(p.get("max_attempts", 3)),
                    "verify_mode": p.get("verify_mode", "commands"),
                    "submission_kind": p.get("submission_kind", "patch"),
                    "judges": p.get("judges"),
                    "acceptance": acceptance,
                    "hidden_acceptance": hidden_acceptance,
                    "allowed_paths": p.get("allowed_paths", ["./"]),
                    "files_hint": p.get("files_hint", []),
                    "context": p.get("context"),
                }
            )
        except Exception:
            continue
        specs[task_id] = spec
    return specs


@dataclass(frozen=True)
class EngineSettings:
    max_concurrency: int = 1
    max_bids_per_worker: int = 2
    bid_timeout_seconds: float | None = 30.0
    execution_timeout_seconds: float | None = 300.0
    # Apply passing patches into the shared run workspace.
    # Disable for isolated single-task runs that do not require downstream state.
    integrate_on_pass: bool = True
    # If enabled, successful text/json submissions are posted to the public
    # discussion board so downstream subtasks can reuse the output.
    publish_successful_submission_to_discussion: bool = False
    # When True, wait for all in-flight bidder calls (or timeout) before
    # clearing the market for the current ready task set.
    require_bid_barrier: bool = False
    # When True, synthesize conservative fallback bids for ready tasks when
    # a worker returns no valid bids.
    force_bid_for_ready_tasks: bool = False
    # When True, workers that previously FAILED a task are excluded from
    # rebidding on that same task. Guarantees a different model on retry.
    # Keep default off for core engine compatibility; research runners opt in.
    exclude_failed_workers: bool = False

    # Cost can be added later; keep API stable now.
    cost_weight: float = 0.0

    # When True, bypasses ThreadPoolExecutor and calls bidder/executor
    # synchronously.  Eliminates thread overhead and non-determinism for
    # RL training loops where max_concurrency=1.
    deterministic: bool = False


class _SynchronousExecutor:
    """Executor that calls functions immediately, returning resolved futures.

    Used in deterministic mode to eliminate thread overhead and ensure
    fully reproducible event sequences for RL training.
    """

    def submit(self, fn: object, /, *args: object, **kwargs: object) -> Future:
        future: Future = Future()
        try:
            result = fn(*args, **kwargs)  # type: ignore[operator]
            future.set_result(result)
        except Exception as exc:
            future.set_exception(exc)
        return future


class _DaemonThreadPoolExecutor(ThreadPoolExecutor):
    """ThreadPoolExecutor variant whose worker threads are daemon threads."""

    def _adjust_thread_count(self) -> None:
        if self._idle_semaphore.acquire(timeout=0):
            return

        def weakref_cb(_ref: object, q: object = self._work_queue) -> None:
            # Wake up workers when the executor is GC'd.
            q.put(None)

        num_threads = len(self._threads)
        if num_threads >= self._max_workers:
            return

        thread_name = f"{self._thread_name_prefix}_{num_threads}"
        t = threading.Thread(
            name=thread_name,
            target=_futures_thread._worker,
            args=(
                weakref.ref(self, weakref_cb),
                self._work_queue,
                self._initializer,
                self._initargs,
            ),
            daemon=True,
        )
        t.start()
        self._threads.add(t)


@dataclass(frozen=True)
class _InflightBid:
    future: Future[tuple[BidResult, str | None]]
    started_at_monotonic: float


@dataclass(frozen=True)
class _InflightExecution:
    worker_id: str
    bid: Bid
    score: float
    expected_cost: float
    score_breakdown: dict[str, float] | None
    future: Future[ExecutionOutcome]
    started_at_monotonic: float


@dataclass(frozen=True)
class _CachedBids:
    bids: list[Bid] = field(default_factory=list)


class ClearinghouseEngine:
    def __init__(
        self,
        *,
        ledger: Ledger,
        settlement: SettlementPolicy | None = None,
        settings: EngineSettings | None = None,
        verifier: Verifier | None = None,
        assignment_policy: AssignmentPolicy | None = None,
    ) -> None:
        self._ledger = ledger
        self._settlement = settlement or SettlementPolicy()
        self._settings = settings or EngineSettings()
        self._verifier = verifier
        self._assignment_policy = assignment_policy
        if self._settings.deterministic:
            self._bid_pool: _DaemonThreadPoolExecutor | _SynchronousExecutor = (
                _SynchronousExecutor()
            )
            self._exec_pool: _DaemonThreadPoolExecutor | _SynchronousExecutor = (
                _SynchronousExecutor()
            )
        else:
            max_workers = max(1, int(self._settings.max_concurrency))
            self._bid_pool = _DaemonThreadPoolExecutor(
                max_workers=max_workers, thread_name_prefix="inst_bid"
            )
            self._exec_pool = _DaemonThreadPoolExecutor(
                max_workers=max_workers, thread_name_prefix="inst_exec"
            )
        self._inflight_bids: dict[str, _InflightBid] = {}
        self._bid_cache: dict[str, _CachedBids] = {}
        self._inflight_exec: dict[str, _InflightExecution] = {}

    def create_run(
        self,
        *,
        run_id: str,
        payment_rule: PaymentRule = PaymentRule.ASK,
        workers: Sequence[WorkerRuntime],
        tasks: Sequence[TaskSpec],
    ) -> None:
        # Per-run state: if an engine instance is reused across runs, ensure we
        # don't carry in-flight futures or cached bids into the next ledger.
        for inflight in self._inflight_bids.values():
            inflight.future.cancel()
        for inflight in self._inflight_exec.values():
            inflight.future.cancel()
        self._inflight_bids.clear()
        self._inflight_exec.clear()
        self._bid_cache.clear()

        self._ledger.reset()
        self._ledger.append(
            EventType.RUN_CREATED,
            run_id=run_id,
            round_id=0,
            payload={"payment_rule": payment_rule.value},
        )

        for w in workers:
            self._ledger.append(
                EventType.WORKER_REGISTERED,
                run_id=run_id,
                round_id=0,
                payload={
                    "worker_id": w.worker_id,
                    "worker_type": w.worker_type.value,
                    "model_ref": w.model_ref,
                    "balance": w.balance,
                    "reputation": w.reputation,
                },
            )

        for t in tasks:
            self._ledger.append(
                EventType.TASK_CREATED,
                run_id=run_id,
                round_id=0,
                payload={
                    "task_id": t.id,
                    "title": t.title,
                    "description": t.description,
                    "deps": t.deps,
                    "bounty": t.bounty,
                    "max_attempts": t.max_attempts,
                    "verify_mode": t.verify_mode.value,
                    "submission_kind": t.submission_kind.value,
                    "judges": None if t.judges is None else t.judges.model_dump(),
                    # Store as dict so the ledger is tool-agnostic JSON.
                    "acceptance": [c.model_dump() for c in t.acceptance],
                    "hidden_acceptance": [c.model_dump() for c in t.hidden_acceptance],
                    "allowed_paths": t.allowed_paths,
                    "files_hint": t.files_hint,
                    "context": None if t.context is None else t.context.model_dump(),
                },
            )

        self._ledger.verify_chain()

    def _record_bid_submission(
        self,
        *,
        run_id: str,
        round_id: int,
        state: DerivedState,
        task_specs: dict[str, TaskSpec],
        ready_ids: Sequence[str],
        excluded_pairs: set[tuple[str, str]],
        worker: WorkerRuntime,
        resp: BidResult,
        bidder_error: str | None,
        cost_estimator: CostEstimator | None,
        cache_result: bool,
    ) -> list[Bid]:
        bids: list[Bid] = []
        for raw in list(resp.bids or []):
            if isinstance(raw, Bid):
                bids.append(raw)
                continue
            try:
                bids.append(Bid.model_validate(raw))
            except Exception:
                continue

        bids = list(bids)[: self._settings.max_bids_per_worker]
        if self._settings.force_bid_for_ready_tasks:
            seen_task_ids = {b.task_id for b in bids}
            for task_id in ready_ids:
                if len(bids) >= self._settings.max_bids_per_worker:
                    break
                if task_id in seen_task_ids:
                    continue
                if (task_id, worker.worker_id) in excluded_pairs:
                    continue
                task_rt = state.tasks.get(task_id)
                if task_rt is None:
                    continue
                bids.append(
                    _default_forced_bid(
                        task_id=task_id,
                        bounty=int(task_rt.bounty_current),
                    )
                )
                seen_task_ids.add(task_id)

        self._ledger.append(
            EventType.BID_SUBMITTED,
            run_id=run_id,
            round_id=round_id,
            payload={
                "worker_id": worker.worker_id,
                "model_ref": resp.model_ref or worker.model_ref,
                "llm_usage": resp.llm_usage,
                "bids": [b.model_dump() for b in bids],
                **({} if bidder_error is None else {"error": bidder_error}),
            },
        )

        if resp.discussion:
            self._ledger.append(
                EventType.DISCUSSION_POST,
                run_id=run_id,
                round_id=round_id,
                payload={
                    "sender": worker.worker_id,
                    "message": resp.discussion,
                },
            )

        bid_usage_cost = 0.0
        if cost_estimator is not None:
            try:
                bid_usage_cost = float(
                    cost_estimator.actual_cost(worker=worker, llm_usage=resp.llm_usage)
                )
            except Exception:
                bid_usage_cost = 0.0
        if bid_usage_cost > 0:
            self._ledger.append(
                EventType.PENALTY_APPLIED,
                run_id=run_id,
                round_id=round_id,
                payload={
                    "worker_id": worker.worker_id,
                    "amount": bid_usage_cost,
                    "reason": "bid_usage_cost",
                    "model_ref": resp.model_ref or worker.model_ref,
                    "llm_usage": resp.llm_usage,
                },
            )

        if cache_result:
            self._bid_cache[worker.worker_id] = _CachedBids(bids=bids)

        return bids

    def inject_task(self, *, run_id: str, round_id: int, task: TaskSpec) -> None:
        """
        Injects a new task into an existing run.
        """
        self._ledger.append(
            EventType.TASK_CREATED,
            run_id=run_id,
            round_id=round_id,
            payload={
                "task_id": task.id,
                "title": task.title,
                "description": task.description,
                "deps": task.deps,
                "bounty": task.bounty,
                "max_attempts": task.max_attempts,
                "verify_mode": task.verify_mode.value,
                "submission_kind": task.submission_kind.value,
                "judges": None if task.judges is None else task.judges.model_dump(),
                "acceptance": [c.model_dump() for c in task.acceptance],
                "hidden_acceptance": [c.model_dump() for c in task.hidden_acceptance],
                "allowed_paths": task.allowed_paths,
                "files_hint": task.files_hint,
                "context": None if task.context is None else task.context.model_dump(),
            },
        )

    def _settle_attempt(
        self,
        *,
        state: DerivedState,
        task_specs: dict[str, TaskSpec],
        run_id: str,
        round_id: int,
        executor: Executor,
        cost_estimator: CostEstimator | None,
        task_id: str,
        worker_id: str,
        bid: Bid,
        award_score: float | None,
        award_expected_cost: float | None,
        award_score_breakdown: dict[str, float] | None,
        outcome: ExecutionOutcome,
    ) -> DerivedState:
        task_spec = task_specs.get(task_id)
        if task_spec is None:
            return state

        cur_task = state.tasks.get(task_id)
        cur_worker = state.workers.get(worker_id)
        if (
            cur_task is None
            or cur_worker is None
            or cur_task.status != "ASSIGNED"
            or cur_task.assigned_worker != worker_id
            or cur_worker.assigned_task != task_id
        ):
            return state

        bounty_before = cur_task.bounty_current
        base_amount_before = (
            int(bounty_before) if state.payment_rule == PaymentRule.BOUNTY else int(bid.ask)
        )

        if outcome.status == VerifyStatus.PASS and self._settings.integrate_on_pass:
            integrate_fn = getattr(executor, "integrate", None)
            if callable(integrate_fn):
                try:
                    outcome = integrate_fn(
                        worker=cur_worker,
                        task=task_spec,
                        bid=bid,
                        round_id=round_id,
                        outcome=outcome,
                    )
                except Exception as e:
                    outcome = outcome.with_status(
                        VerifyStatus.INFRA,
                        notes=f"integrate_exception={type(e).__name__}: {e}",
                    )

        # Verifier override: when an external verifier is injected, it gets
        # the final say on the verification status.  This lets reward models,
        # Prime Intellect evaluators, or any callable replace the executor's
        # built-in verification.
        if self._verifier is not None:
            try:
                overridden = self._verifier.verify(
                    task=task_spec,
                    worker=cur_worker,
                    outcome=outcome,
                )
                if not isinstance(overridden, VerifyStatus):
                    overridden = VerifyStatus(str(overridden))
                if overridden != outcome.status:
                    outcome = outcome.with_status(overridden)
            except Exception as e:
                outcome = outcome.with_status(
                    VerifyStatus.INFRA,
                    notes=f"verifier_exception={type(e).__name__}: {e}",
                )

        holdback_amount = 0.0
        if outcome.status == VerifyStatus.PASS and task_spec.verify_mode == VerifyMode.JUDGES:
            frac = float(self._settlement.judges_holdback_fraction)
            frac = max(0.0, min(1.0, frac))
            holdback_amount = round(float(base_amount_before) * frac, 2)
            holdback_amount = min(float(base_amount_before), max(0.0, holdback_amount))

        submitted_artifacts = (
            list(outcome.submission_artifacts)
            if outcome.submission_artifacts
            else list(outcome.patch_artifacts)
        )

        self._ledger.append(
            EventType.PATCH_SUBMITTED,
            run_id=run_id,
            round_id=round_id,
            payload={
                "task_id": task_id,
                "worker_id": worker_id,
                "notes": outcome.notes,
                "model_ref": cur_worker.model_ref,
                "submission_kind": (
                    None if outcome.submission_kind is None else outcome.submission_kind.value
                ),
                "llm_usage": outcome.llm_usage,
            },
            artifacts=submitted_artifacts,
        )

        usage_cost = 0.0
        if cost_estimator is not None:
            try:
                usage_cost = float(
                    cost_estimator.actual_cost(worker=cur_worker, llm_usage=outcome.llm_usage)
                )
            except Exception:
                usage_cost = 0.0
        if usage_cost > 0:
            self._ledger.append(
                EventType.PENALTY_APPLIED,
                run_id=run_id,
                round_id=round_id,
                payload={
                    "task_id": task_id,
                    "worker_id": worker_id,
                    "amount": usage_cost,
                    "reason": "usage_cost",
                    "model_ref": cur_worker.model_ref,
                    "llm_usage": outcome.llm_usage,
                },
            )

        verify_event_type = (
            EventType.VERIFICATION_PASSED
            if outcome.status == VerifyStatus.PASS
            else (
                EventType.MANUAL_REVIEW_REQUIRED
                if outcome.status == VerifyStatus.MANUAL_REVIEW
                else EventType.VERIFICATION_FAILED
            )
        )
        self._ledger.append(
            verify_event_type,
            run_id=run_id,
            round_id=round_id,
            payload={
                "task_id": task_id,
                "worker_id": worker_id,
                "status": outcome.status.value,
            },
            artifacts=outcome.verification_artifacts,
        )
        self._ledger.append(
            EventType.TASK_COMPLETED,
            run_id=run_id,
            round_id=round_id,
            payload={
                "task_id": task_id,
                "worker_id": worker_id,
                "success": outcome.success,
                "verify_status": outcome.status.value,
                "bid": bid.model_dump(),
                "bounty_current": bounty_before,
                "holdback_amount": holdback_amount,
                "sandbox": outcome.sandbox_rel,
                "patch_kind": outcome.patch_kind,
                "submission_kind": (
                    None if outcome.submission_kind is None else outcome.submission_kind.value
                ),
                "award_score": None if award_score is None else float(award_score),
                "award_expected_cost": (
                    None if award_expected_cost is None else float(award_expected_cost)
                ),
                "award_score_snapshot": _score_snapshot_payload(breakdown=award_score_breakdown),
                "verification_summary": outcome.verification_summary,
            },
        )

        if (
            outcome.status == VerifyStatus.PASS
            and self._settings.publish_successful_submission_to_discussion
            and outcome.submission_kind in {SubmissionKind.TEXT, SubmissionKind.JSON}
        ):
            submission_text = str(outcome.submission_preview or "").strip()
            if len(submission_text) > 2000:
                submission_text = submission_text[:1985] + "...[truncated]"
            if submission_text:
                self._ledger.append(
                    EventType.DISCUSSION_POST,
                    run_id=run_id,
                    round_id=round_id,
                    payload={
                        "sender": worker_id,
                        "message": (
                            f"[Task {task_id} completed] {outcome.submission_kind.value} output:\n"
                            f"{submission_text}"
                        ),
                    },
                )

        feedback = str(outcome.verification_summary or "").strip()
        if len(feedback) > 2000:
            feedback = feedback[:1985] + "...[truncated]"
        if (not feedback) and outcome.status in {
            VerifyStatus.FAIL,
            VerifyStatus.TIMEOUT,
            VerifyStatus.INFRA,
        }:
            feedback = str(outcome.notes or "").strip()
        if feedback and outcome.status != VerifyStatus.PASS:
            self._ledger.append(
                EventType.DISCUSSION_POST,
                run_id=run_id,
                round_id=round_id,
                payload={
                    "sender": "system",
                    "message": (
                        f"[Prior attempt context for {task_id}] "
                        f"Issues observed during verification:\n{feedback}"
                    ),
                },
            )

        if outcome.status == VerifyStatus.PASS:
            bounty = bounty_before
            base_amount = (
                float(bounty) if state.payment_rule == PaymentRule.BOUNTY else float(bid.ask)
            )
            holdback = min(base_amount, float(holdback_amount))
            amount = max(0.0, base_amount - holdback)
            if amount > 0.0:
                self._ledger.append(
                    EventType.PAYMENT_MADE,
                    run_id=run_id,
                    round_id=round_id,
                    payload={
                        "task_id": task_id,
                        "worker_id": worker_id,
                        "amount": amount,
                        "payment_rule": state.payment_rule.value,
                        "bounty": bounty,
                        "ask": bid.ask,
                        "holdback": holdback,
                    },
                )
        elif outcome.status == VerifyStatus.FAIL:
            bounty = bounty_before
            if self._settlement.penalty_mode == "direct_penalty":
                penalty = float(
                    _direct_penalty_amount(
                        bounty=bounty,
                        p_success=float(bid.self_assessed_p_success),
                        policy=self._settlement,
                    )
                )
                base_penalty = penalty
                confidence_penalty = 0.0
            else:
                base_penalty = _penalty_amount(bounty=bounty, policy=self._settlement)
                confidence_penalty = _confidence_penalty_amount(
                    base_penalty=base_penalty,
                    p_success=float(bid.self_assessed_p_success),
                    policy=self._settlement,
                )
                penalty = int(base_penalty + confidence_penalty)
            self._ledger.append(
                EventType.PENALTY_APPLIED,
                run_id=run_id,
                round_id=round_id,
                payload={
                    "task_id": task_id,
                    "worker_id": worker_id,
                    "amount": penalty,
                    "reason": "verification_fail",
                    "base_penalty": base_penalty,
                    "confidence_penalty": confidence_penalty,
                    "reported_p_success": float(bid.self_assessed_p_success),
                    "confidence_penalty_floor": float(
                        getattr(self._settlement, "confidence_penalty_floor", 0.5)
                    ),
                    "penalty_mode": str(self._settlement.penalty_mode),
                },
            )

            next_fail_count = int(cur_task.fail_count) + 1
            replan_threshold = int(getattr(self._settlement, "replan_fail_threshold", 0) or 0)
            if replan_threshold > 0 and next_fail_count == replan_threshold:
                self._ledger.append(
                    EventType.PLAN_REVISION_REQUESTED,
                    run_id=run_id,
                    round_id=round_id,
                    payload={
                        "task_id": task_id,
                        "fail_count": next_fail_count,
                        "reason": "repeated_failures",
                    },
                )
                self._ledger.append(
                    EventType.DISCUSSION_POST,
                    run_id=run_id,
                    round_id=round_id,
                    payload={
                        "sender": "system",
                        "message": f"Task {task_id} has failed {next_fail_count} times; consider revising the plan or splitting the task.",
                    },
                )
            if next_fail_count > 0 and next_fail_count % 2 == 0:
                new_bounty = min(
                    cur_task.bounty_original * 2,
                    max(bounty + 1, round(bounty * 1.10)),
                )
                if new_bounty != bounty:
                    self._ledger.append(
                        EventType.BOUNTY_ADJUSTED,
                        run_id=run_id,
                        round_id=round_id,
                        payload={
                            "task_id": task_id,
                            "bounty_current": new_bounty,
                            "reason": "repeated_failures",
                        },
                    )

        return replay_ledger(events=list(self._ledger.iter_events()), settlement=self._settlement)

    def step(
        self,
        *,
        bidder: Bidder,
        executor: Executor,
        cost_estimator: CostEstimator | None = None,
    ) -> None:
        def _timed_out(*, started_at: float, timeout_seconds: float | None) -> bool:
            if timeout_seconds is None or timeout_seconds <= 0:
                return False
            return (time.monotonic() - started_at) >= timeout_seconds

        while True:
            events = list(self._ledger.iter_events())
            self._ledger.verify_chain()

            state = replay_ledger(events=events, settlement=self._settlement)
            task_specs = _load_task_specs_from_events(events=events)
            excluded_pairs: set[tuple[str, str]] = (
                _failed_task_worker_pairs(events=events)
                if self._settings.exclude_failed_workers
                else set()
            )

            round_id = state.round_id
            run_id = state.run_id

            did_any = False
            # In bid-barrier mode we intentionally keep the round open while
            # collecting bids from all workers. In legacy mode, preserve the
            # previous behavior where any activity advances the round.
            did_round_advance = not self._settings.require_bid_barrier

            # Finalize any completed executions.
            for task_id, inflight in list(self._inflight_exec.items()):
                timed_out = _timed_out(
                    started_at=inflight.started_at_monotonic,
                    timeout_seconds=self._settings.execution_timeout_seconds,
                )
                if not inflight.future.done() and not timed_out:
                    continue
                self._inflight_exec.pop(task_id, None)
                if timed_out and not inflight.future.done():
                    inflight.future.cancel()
                    timeout_seconds = self._settings.execution_timeout_seconds
                    timeout_s = 0.0 if timeout_seconds is None else float(timeout_seconds)
                    outcome = _timeout_outcome(f"executor_timeout_after_s={timeout_s:g}")
                else:
                    try:
                        outcome = inflight.future.result()
                    except Exception as e:
                        outcome = _infra_outcome(f"executor_exception={type(e).__name__}: {e}")
                state = self._settle_attempt(
                    state=state,
                    task_specs=task_specs,
                    run_id=run_id,
                    round_id=round_id,
                    executor=executor,
                    cost_estimator=cost_estimator,
                    task_id=task_id,
                    worker_id=inflight.worker_id,
                    bid=inflight.bid,
                    award_score=inflight.score,
                    award_expected_cost=inflight.expected_cost,
                    award_score_breakdown=inflight.score_breakdown,
                    outcome=outcome,
                )
                did_any = True
                did_round_advance = True

            # Finalize any completed bid fetches.
            for worker_id, inflight in list(self._inflight_bids.items()):
                timed_out = _timed_out(
                    started_at=inflight.started_at_monotonic,
                    timeout_seconds=self._settings.bid_timeout_seconds,
                )
                if not inflight.future.done() and not timed_out:
                    continue
                self._inflight_bids.pop(worker_id, None)
                if timed_out and not inflight.future.done():
                    inflight.future.cancel()
                    timeout_seconds = self._settings.bid_timeout_seconds
                    timeout_s = 0.0 if timeout_seconds is None else float(timeout_seconds)
                    resp, bidder_error = BidResult(), f"bidder_timeout_after_s={timeout_s:g}"
                else:
                    try:
                        resp, bidder_error = inflight.future.result()
                    except Exception as e:
                        resp, bidder_error = BidResult(), f"{type(e).__name__}: {e}"

                worker = state.workers.get(worker_id)
                if worker is None or worker.assigned_task is not None:
                    continue

                self._record_bid_submission(
                    run_id=run_id,
                    round_id=round_id,
                    state=state,
                    task_specs=task_specs,
                    ready_ids=_ready_task_ids(task_specs=task_specs, tasks=state.tasks),
                    excluded_pairs=excluded_pairs,
                    worker=worker,
                    resp=resp,
                    bidder_error=bidder_error,
                    cost_estimator=cost_estimator,
                    cache_result=True,
                )
                did_any = True

            if did_any:
                events = list(self._ledger.iter_events())
                state = replay_ledger(events=events, settlement=self._settlement)
                task_specs = _load_task_specs_from_events(events=events)

            ready_ids = _ready_task_ids(task_specs=task_specs, tasks=state.tasks)
            available_workers = [w for w in state.workers.values() if w.assigned_task is None]

            ready_views: list[ReadyTask] = [
                ReadyTask(spec=task_specs[tid], runtime=state.tasks[tid])
                for tid in ready_ids
                if tid in task_specs and tid in state.tasks
            ]
            worker_ready_views: dict[str, list[ReadyTask]] = {
                w.worker_id: [
                    rv
                    for rv in ready_views
                    if (str(rv.spec.id), str(w.worker_id)) not in excluded_pairs
                ]
                for w in available_workers
            }
            retry_penalty_pairs = _retry_penalty_by_pair(events=events, policy=self._settlement)

            set_prompt_context = getattr(bidder, "set_worker_prompt_context", None)
            if callable(set_prompt_context):
                for worker in available_workers:
                    try:
                        context_lines = _worker_market_context_lines(
                            worker=worker,
                            ready_views=worker_ready_views.get(worker.worker_id, []),
                            round_id=int(round_id),
                            cost_estimator=cost_estimator,
                        )
                        set_prompt_context(worker=worker, context_lines=context_lines)
                    except Exception:
                        continue

            if self._assignment_policy is None:
                # Start bid fetches for idle workers that don't have cached bids yet.
                #
                # In bid-barrier mode, once any worker has produced bids for the current
                # ready set, freeze new fetches until the round clears. This prevents a
                # perpetual "one cached + one in-flight" cycle that can starve market
                # clearing when workers have different response latencies.
                has_any_cached_for_ready = any(
                    w.worker_id in self._bid_cache for w in available_workers
                )
                can_start_fetches = True
                if self._settings.require_bid_barrier and has_any_cached_for_ready:
                    can_start_fetches = False
            else:
                can_start_fetches = False

            if ready_views and can_start_fetches:

                def _fetch_bids(worker: WorkerRuntime) -> tuple[BidResult, str | None]:
                    scoped_ready = worker_ready_views.get(worker.worker_id, [])
                    try:
                        resp = bidder.get_bids(
                            worker=worker,
                            ready_tasks=scoped_ready,
                            round_id=round_id,
                            discussion_history=state.discussion_history,
                        )
                        return resp, None
                    except Exception as e:
                        return BidResult(), f"{type(e).__name__}: {e}"

                for w in sorted(available_workers, key=lambda ww: ww.worker_id):
                    if w.worker_id in self._bid_cache:
                        continue
                    if w.worker_id in self._inflight_bids:
                        continue
                    if not worker_ready_views.get(w.worker_id):
                        self._bid_cache.pop(w.worker_id, None)
                        continue

                    fut = self._bid_pool.submit(_fetch_bids, w)
                    self._inflight_bids[w.worker_id] = _InflightBid(
                        future=fut,
                        started_at_monotonic=time.monotonic(),
                    )

            slots = max(0, int(self._settings.max_concurrency) - len(self._inflight_exec))
            has_any_cached = any(w.worker_id in self._bid_cache for w in available_workers)
            has_pending_inflight_bids = any(
                w.worker_id in self._inflight_bids for w in available_workers
            )
            can_clear_market = has_any_cached
            if self._settings.require_bid_barrier and has_pending_inflight_bids:
                can_clear_market = False

            assignments: list[Assignment] = []
            if self._assignment_policy is not None:
                can_clear_market = bool(slots > 0 and ready_ids and available_workers)

            if slots > 0 and ready_ids and available_workers and can_clear_market:
                bids_by_task: dict[str, list[BidSubmission]] = {tid: [] for tid in ready_ids}
                any_bid_for_task: dict[str, bool] = {tid: False for tid in ready_ids}

                if self._assignment_policy is None:
                    for w in available_workers:
                        cached = self._bid_cache.get(w.worker_id)
                        if cached is None:
                            continue
                        seen: set[str] = set()
                        for bid in list(cached.bids)[: self._settings.max_bids_per_worker]:
                            if bid.task_id in seen:
                                continue
                            seen.add(bid.task_id)
                            if bid.task_id not in bids_by_task:
                                continue
                            if (bid.task_id, w.worker_id) in excluded_pairs:
                                continue
                            expected_cost = 0.0
                            if cost_estimator is not None and bid.task_id in task_specs:
                                try:
                                    expected_cost = float(
                                        cost_estimator.expected_cost(
                                            worker=w,
                                            task=task_specs[bid.task_id],
                                            bid=bid,
                                            round_id=round_id,
                                        )
                                    )
                                except Exception:
                                    expected_cost = 0.0
                            bids_by_task[bid.task_id].append(
                                BidSubmission(
                                    worker_id=w.worker_id,
                                    bid=bid,
                                    expected_cost=expected_cost,
                                )
                            )
                            any_bid_for_task[bid.task_id] = True

                    ready_tasks = [state.tasks[tid] for tid in ready_ids if tid in state.tasks]
                    assignments = choose_assignments(
                        ready_tasks=ready_tasks,
                        available_workers=available_workers,
                        bids_by_task=bids_by_task,
                        penalty_mode=str(self._settlement.penalty_mode),
                        penalty_fraction=float(self._settlement.penalty_fraction),
                        retry_penalty_by_pair=retry_penalty_pairs,
                        excluded_pairs=excluded_pairs,
                    )
                else:
                    decision = self._assignment_policy.choose(
                        round_id=round_id,
                        ready_tasks=ready_views,
                        available_workers=available_workers,
                        discussion_history=state.discussion_history,
                        cost_estimator=cost_estimator,
                        excluded_pairs=excluded_pairs,
                    )
                    if decision.llm_usage or decision.payload:
                        self._ledger.append(
                            EventType.ROUTER_DECISION,
                            run_id=run_id,
                            round_id=round_id,
                            payload={
                                "model_ref": decision.model_ref,
                                "llm_usage": decision.llm_usage,
                                **({} if decision.payload is None else dict(decision.payload)),
                            },
                        )

                    ready_view_by_id = {str(ready.spec.id): ready for ready in ready_views}
                    workers_by_id = {worker.worker_id: worker for worker in available_workers}
                    selected_tasks: set[str] = set()
                    selected_workers: set[str] = set()
                    for selection in list(decision.selections):
                        task_id = str(selection.task_id)
                        worker_id = str(selection.worker_id)
                        if not task_id or not worker_id:
                            continue
                        if task_id in selected_tasks or worker_id in selected_workers:
                            continue
                        if task_id not in ready_view_by_id:
                            continue
                        if worker_id not in workers_by_id:
                            continue
                        if (task_id, worker_id) in excluded_pairs:
                            continue

                        worker = workers_by_id[worker_id]
                        ready_view = ready_view_by_id[task_id]
                        bidder_error: str | None = None
                        try:
                            resp = bidder.get_bids(
                                worker=worker,
                                ready_tasks=[ready_view],
                                round_id=round_id,
                                discussion_history=state.discussion_history,
                            )
                        except Exception as e:
                            resp = BidResult()
                            bidder_error = f"{type(e).__name__}: {e}"

                        normalized_bids = self._record_bid_submission(
                            run_id=run_id,
                            round_id=round_id,
                            state=state,
                            task_specs=task_specs,
                            ready_ids=[task_id],
                            excluded_pairs=excluded_pairs,
                            worker=worker,
                            resp=resp,
                            bidder_error=bidder_error,
                            cost_estimator=cost_estimator,
                            cache_result=False,
                        )
                        bid = next(
                            (
                                candidate
                                for candidate in normalized_bids
                                if candidate.task_id == task_id
                            ),
                            _default_forced_bid(
                                task_id=task_id,
                                bounty=int(ready_view.runtime.bounty_current),
                            ),
                        )
                        expected_cost = 0.0
                        if cost_estimator is not None and task_id in task_specs:
                            try:
                                expected_cost = float(
                                    cost_estimator.expected_cost(
                                        worker=worker,
                                        task=task_specs[task_id],
                                        bid=bid,
                                        round_id=round_id,
                                    )
                                )
                            except Exception:
                                expected_cost = 0.0
                        breakdown = score_bid_breakdown(
                            bounty=ready_view.runtime.bounty_current,
                            reputation=worker.reputation,
                            bid=bid,
                            expected_cost=expected_cost,
                            penalty_mode=str(self._settlement.penalty_mode),
                            penalty_fraction=float(self._settlement.penalty_fraction),
                        )
                        retry_penalty = float(retry_penalty_pairs.get((task_id, worker_id), 0.0))
                        score = float(breakdown["score"]) - retry_penalty
                        breakdown["score_before_retry_penalty"] = float(breakdown["score"])
                        breakdown["retry_score_penalty"] = float(retry_penalty)
                        breakdown["score"] = float(score)
                        assignments.append(
                            Assignment(
                                task_id=task_id,
                                worker_id=worker_id,
                                bid=bid,
                                score=float(score),
                                expected_cost=float(expected_cost),
                                score_breakdown=breakdown,
                            )
                        )
                        any_bid_for_task[task_id] = True
                        bids_by_task[task_id].append(
                            BidSubmission(
                                worker_id=worker_id,
                                bid=bid,
                                expected_cost=float(expected_cost),
                            )
                        )
                        selected_tasks.add(task_id)
                        selected_workers.add(worker_id)

                self._ledger.append(
                    EventType.MARKET_CLEARED,
                    run_id=run_id,
                    round_id=round_id,
                    payload={
                        "assignments": [
                            {
                                "task_id": a.task_id,
                                "worker_id": a.worker_id,
                                "bid": a.bid.model_dump(),
                                "score": a.score,
                                "expected_cost": a.expected_cost,
                                "score_snapshot": _score_snapshot_payload(
                                    breakdown=a.score_breakdown
                                ),
                            }
                            for a in assignments
                        ]
                    },
                )
                did_any = True
                did_round_advance = True

                assigned_task_ids = {a.task_id for a in assignments}
                for tid in ready_ids:
                    if tid in assigned_task_ids:
                        continue
                    task = state.tasks[tid]
                    had_any = any_bid_for_task[tid]
                    best_score = None
                    if had_any:
                        scores: list[float] = []
                        for sub in bids_by_task[tid]:
                            rep = state.workers[sub.worker_id].reputation
                            score_info = score_bid_breakdown(
                                bounty=task.bounty_current,
                                reputation=rep,
                                bid=sub.bid,
                                expected_cost=sub.expected_cost,
                                penalty_mode=str(self._settlement.penalty_mode),
                                penalty_fraction=float(self._settlement.penalty_fraction),
                            )
                            retry_penalty = float(
                                retry_penalty_pairs.get((tid, sub.worker_id), 0.0)
                            )
                            scores.append(float(score_info["score"]) - retry_penalty)
                        best_score = max(scores) if scores else None
                    no_router_assignment = self._assignment_policy is not None and tid not in {
                        a.task_id for a in assignments
                    }
                    if (
                        no_router_assignment
                        or (not had_any)
                        or (best_score is None)
                        or (best_score <= 0)
                    ):
                        new_bounty = _bump_bounty(task=task)
                        if new_bounty != task.bounty_current:
                            self._ledger.append(
                                EventType.BOUNTY_ADJUSTED,
                                run_id=run_id,
                                round_id=round_id,
                                payload={
                                    "task_id": tid,
                                    "bounty_current": new_bounty,
                                    "reason": (
                                        "no_router_assignment"
                                        if no_router_assignment
                                        else "no_winning_bids"
                                    ),
                                },
                            )
                            did_round_advance = True

                def _run_execute(
                    cur_worker: WorkerRuntime,
                    cur_task: TaskSpec,
                    cur_bid: Bid,
                    history: Sequence[DiscussionMessage],
                ) -> ExecutionOutcome:
                    return executor.execute(
                        worker=cur_worker,
                        task=cur_task,
                        bid=cur_bid,
                        round_id=round_id,
                        discussion_history=history,
                    )

                for assignment in list(assignments[:slots]):
                    task_id = assignment.task_id
                    worker_id = assignment.worker_id
                    task_spec = task_specs.get(task_id)
                    worker = state.workers.get(worker_id)
                    if task_spec is None or worker is None:
                        continue
                    if task_id in self._inflight_exec:
                        continue

                    self._ledger.append(
                        EventType.TASK_ASSIGNED,
                        run_id=run_id,
                        round_id=round_id,
                        payload={
                            "task_id": task_id,
                            "worker_id": worker_id,
                            "bid": assignment.bid.model_dump(),
                            "score": assignment.score,
                            "expected_cost": assignment.expected_cost,
                            "score_snapshot": _score_snapshot_payload(
                                breakdown=assignment.score_breakdown
                            ),
                        },
                    )
                    self._bid_cache.pop(worker_id, None)

                    fut = self._exec_pool.submit(
                        _run_execute,
                        worker,
                        task_spec,
                        assignment.bid,
                        state.discussion_history,
                    )
                    self._inflight_exec[task_id] = _InflightExecution(
                        worker_id=worker_id,
                        bid=assignment.bid,
                        score=float(assignment.score),
                        expected_cost=float(assignment.expected_cost),
                        score_breakdown=assignment.score_breakdown,
                        future=fut,
                        started_at_monotonic=time.monotonic(),
                    )

            if did_any:
                if did_round_advance:
                    self._ledger.append(
                        EventType.ROUND_ADVANCED,
                        run_id=run_id,
                        round_id=round_id + 1,
                        payload={"round_id": round_id + 1},
                    )
                    self._ledger.verify_chain()
                    # Keep bidding responsive to bounty adjustments / new ready tasks.
                    # Bids captured for the previous round should never leak into the next
                    # round's market clearing.
                    for inflight in self._inflight_bids.values():
                        inflight.future.cancel()
                    self._inflight_bids.clear()
                    self._bid_cache.clear()
                return

            inflight: list[Future[object]] = []
            inflight.extend([b.future for b in self._inflight_bids.values()])
            inflight.extend([e.future for e in self._inflight_exec.values()])
            if not inflight:
                return

            wait_timeout: float | None = None
            deadline_candidates: list[float] = []
            if (
                self._settings.bid_timeout_seconds is not None
                and self._settings.bid_timeout_seconds > 0
            ):
                for inflight_bid in self._inflight_bids.values():
                    if inflight_bid.future.done():
                        continue
                    deadline_candidates.append(
                        inflight_bid.started_at_monotonic + self._settings.bid_timeout_seconds
                    )
            if (
                self._settings.execution_timeout_seconds is not None
                and self._settings.execution_timeout_seconds > 0
            ):
                for inflight_exec in self._inflight_exec.values():
                    if inflight_exec.future.done():
                        continue
                    deadline_candidates.append(
                        inflight_exec.started_at_monotonic
                        + self._settings.execution_timeout_seconds
                    )
            if deadline_candidates:
                wait_timeout = max(0.0, min(deadline_candidates) - time.monotonic())

            wait(inflight, timeout=wait_timeout, return_when=FIRST_COMPLETED)
