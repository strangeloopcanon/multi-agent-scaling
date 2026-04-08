from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from agent_economy.engine import (
    AssignmentDecision,
    BidResult,
    ClearinghouseEngine,
    EngineSettings,
    ExecutionOutcome,
    ReadyTask,
    RouterSelection,
    _CachedBids,
    _InflightBid,
    _InflightExecution,
)
from agent_economy.ledger import HashChainedLedger
from agent_economy.schemas import (
    Bid,
    CommandSpec,
    EventType,
    PaymentRule,
    SubmissionKind,
    TaskSpec,
    VerifyStatus,
    WorkerRuntime,
)
from agent_economy.state import SettlementPolicy, replay_ledger


class ScriptedBidder:
    def __init__(self, scripted: dict[tuple[int, str], Sequence[Bid]]) -> None:
        self._scripted = scripted

    def get_bids(
        self,
        *,
        worker: WorkerRuntime,
        ready_tasks: Sequence[ReadyTask],
        round_id: int,
        discussion_history: Sequence[Any],
    ) -> BidResult:
        _ = ready_tasks, discussion_history
        return BidResult(bids=list(self._scripted.get((round_id, worker.worker_id), ())))


class ScriptedExecutor:
    def __init__(self, *, fail_task_ids: set[str] | None = None) -> None:
        self._fail_task_ids = fail_task_ids or set()

    def execute(
        self,
        *,
        worker: WorkerRuntime,
        task: TaskSpec,
        bid: Bid,
        round_id: int,
        discussion_history: Sequence[Any],
    ) -> ExecutionOutcome:
        _ = worker, bid, round_id, discussion_history
        if task.id in self._fail_task_ids:
            return ExecutionOutcome(status=VerifyStatus.FAIL, notes="scripted fail")
        return ExecutionOutcome(status=VerifyStatus.PASS, notes="scripted pass")


class StatusExecutor:
    def __init__(self, *, status_by_task_id: dict[str, VerifyStatus]) -> None:
        self._status_by_task_id = dict(status_by_task_id)

    def execute(
        self,
        *,
        worker: WorkerRuntime,
        task: TaskSpec,
        bid: Bid,
        round_id: int,
        discussion_history: Sequence[Any],
    ) -> ExecutionOutcome:
        _ = worker, bid, round_id, discussion_history
        status = self._status_by_task_id.get(task.id, VerifyStatus.PASS)
        return ExecutionOutcome(status=status, notes=f"scripted {status.value}")


class TextSubmissionExecutor:
    def execute(
        self,
        *,
        worker: WorkerRuntime,
        task: TaskSpec,
        bid: Bid,
        round_id: int,
        discussion_history: Sequence[Any],
    ) -> ExecutionOutcome:
        _ = worker, task, bid, round_id, discussion_history
        return ExecutionOutcome(
            status=VerifyStatus.PASS,
            notes="text submission",
            submission_kind=SubmissionKind.TEXT,
            submission_preview="Diagnosis complete: null pointer in parser.",
        )


class RaisingBidder:
    def __init__(self, *, exc: Exception) -> None:
        self._exc = exc

    def get_bids(
        self,
        *,
        worker: WorkerRuntime,
        ready_tasks: Sequence[ReadyTask],
        round_id: int,
        discussion_history: Sequence[Any],
    ) -> BidResult:
        _ = worker, ready_tasks, round_id, discussion_history
        raise self._exc


class RaisingExecutor:
    def __init__(self, *, exc: Exception) -> None:
        self._exc = exc

    def execute(
        self,
        *,
        worker: WorkerRuntime,
        task: TaskSpec,
        bid: Bid,
        round_id: int,
        discussion_history: Sequence[Any],
    ) -> ExecutionOutcome:
        _ = worker, task, bid, round_id, discussion_history
        raise self._exc


class StaticAssignmentPolicy:
    def __init__(self, *, selections: Sequence[RouterSelection]) -> None:
        self._selections = list(selections)

    def choose(
        self,
        *,
        round_id: int,
        ready_tasks: Sequence[ReadyTask],
        available_workers: Sequence[WorkerRuntime],
        discussion_history: Sequence[Any],
        cost_estimator: Any,
        excluded_pairs: set[tuple[str, str]],
    ) -> AssignmentDecision:
        _ = (
            round_id,
            ready_tasks,
            available_workers,
            discussion_history,
            cost_estimator,
            excluded_pairs,
        )
        return AssignmentDecision(
            selections=list(self._selections),
            model_ref="openai:gpt-5.2-pro-2025-12-11",
            llm_usage={"calls": 1, "input_tokens": 11, "output_tokens": 7},
            payload={"policy": "central_router"},
        )


def test_engine_runs_multiple_assignments_concurrently(tmp_path) -> None:
    import threading

    ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
    engine = ClearinghouseEngine(ledger=ledger, settings=EngineSettings(max_concurrency=2))

    tasks = [
        TaskSpec(id="T1", title="t1", bounty=10, deps=[], acceptance=[CommandSpec(cmd="true")]),
        TaskSpec(id="T2", title="t2", bounty=10, deps=[], acceptance=[CommandSpec(cmd="true")]),
    ]
    workers = [
        WorkerRuntime(worker_id="w1", reputation=1.0),
        WorkerRuntime(worker_id="w2", reputation=1.0),
    ]
    engine.create_run(run_id="run-1", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    bidder = ScriptedBidder(
        {
            (0, "w1"): [Bid(task_id="T1", ask=1, self_assessed_p_success=1.0, eta_minutes=10)],
            (0, "w2"): [Bid(task_id="T2", ask=1, self_assessed_p_success=1.0, eta_minutes=10)],
        }
    )

    class BarrierExecutor:
        def __init__(self) -> None:
            self._barrier = threading.Barrier(2)

        def execute(
            self,
            *,
            worker: WorkerRuntime,
            task: TaskSpec,
            bid: Bid,
            round_id: int,
            discussion_history: Sequence[Any],
        ) -> ExecutionOutcome:
            _ = worker, task, bid, round_id, discussion_history
            try:
                self._barrier.wait(timeout=2.0)
            except threading.BrokenBarrierError as e:
                raise RuntimeError("executor did not run concurrently") from e
            return ExecutionOutcome(status=VerifyStatus.PASS, notes="ok")

    # Step 1 assigns + starts work; step 2 records completions.
    engine.step(bidder=bidder, executor=BarrierExecutor())
    engine.step(bidder=bidder, executor=BarrierExecutor())
    state = replay_ledger(events=list(ledger.iter_events()))
    assert state.tasks["T1"].status == "DONE"
    assert state.tasks["T2"].status == "DONE"


def test_engine_rounds_settlement_and_bounty_bumps(tmp_path) -> None:
    ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
    engine = ClearinghouseEngine(
        ledger=ledger,
        settings=EngineSettings(max_concurrency=1, exclude_failed_workers=False),
    )

    tasks = [
        TaskSpec(
            id="T1",
            title="t1",
            bounty=100,
            deps=[],
            acceptance=[CommandSpec(cmd="true")],
        ),
        TaskSpec(
            id="T2",
            title="t2",
            bounty=100,
            deps=["T1"],
            acceptance=[CommandSpec(cmd="true")],
        ),
    ]
    workers = [
        WorkerRuntime(worker_id="w1", reputation=1.0),
        WorkerRuntime(worker_id="w2", reputation=1.0),
    ]
    engine.create_run(run_id="run-1", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    class BiddingPolicy:
        def get_bids(
            self,
            *,
            worker: WorkerRuntime,
            ready_tasks: Sequence[ReadyTask],
            round_id: int,
            discussion_history: Sequence[Any],
        ) -> BidResult:
            _ = round_id, discussion_history
            ready_ids = {t.spec.id for t in ready_tasks}
            if worker.worker_id == "w1":
                if "T2" in ready_ids:
                    return BidResult(
                        bids=[
                            Bid(
                                task_id="T2",
                                ask=10,
                                self_assessed_p_success=1.0,
                                eta_minutes=10,
                            )
                        ]
                    )
                if "T1" in ready_ids:
                    return BidResult(
                        bids=[
                            Bid(
                                task_id="T1",
                                ask=15,
                                self_assessed_p_success=1.0,
                                eta_minutes=10,
                            )
                        ]
                    )
                return BidResult()

            if worker.worker_id == "w2" and "T1" in ready_ids:
                return BidResult(
                    bids=[Bid(task_id="T1", ask=10, self_assessed_p_success=1.0, eta_minutes=10)]
                )
            return BidResult()

    bidder = BiddingPolicy()
    executor = ScriptedExecutor(fail_task_ids={"T2"})

    # T1 clears to w2 (lower ask), passes, paid ask=10.
    for _ in range(5):
        engine.step(bidder=bidder, executor=executor)
        state = replay_ledger(events=list(ledger.iter_events()))
        if state.tasks["T1"].status == "DONE":
            break
    assert state.tasks["T1"].status == "DONE"
    assert state.tasks["T2"].status == "TODO"
    assert state.workers["w2"].balance == 10.0
    assert state.workers["w2"].reputation > 1.0

    # T2 clears to w1, fails, base penalty 10 + confidence penalty 10 (p_success=1.0).
    for _ in range(5):
        engine.step(bidder=bidder, executor=executor)
        state = replay_ledger(events=list(ledger.iter_events()))
        if state.tasks["T2"].fail_count >= 1:
            break
    assert state.tasks["T2"].status == "TODO"
    assert state.tasks["T2"].fail_count == 1
    assert state.workers["w1"].balance == -20.0
    assert state.workers["w1"].reputation < 1.0

    # T2 fails again; after 2nd failure bounty bumps by 10% (100 -> 110).
    for _ in range(10):
        engine.step(bidder=bidder, executor=executor)
        state = replay_ledger(events=list(ledger.iter_events()))
        if state.tasks["T2"].fail_count >= 2:
            break
    assert state.tasks["T2"].fail_count == 2
    assert state.tasks["T2"].bounty_current == 110
    assert state.workers["w1"].balance == -40.0

    penalty_events = [e for e in ledger.iter_events() if e.type == EventType.PENALTY_APPLIED]
    fail_penalties = [e for e in penalty_events if e.payload.get("reason") == "verification_fail"]
    assert fail_penalties
    assert fail_penalties[0].payload.get("base_penalty") == 10
    assert fail_penalties[0].payload.get("confidence_penalty") == 10


def test_engine_manual_review_does_not_settle(tmp_path) -> None:
    ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
    engine = ClearinghouseEngine(ledger=ledger, settings=EngineSettings(max_concurrency=1))

    tasks = [
        TaskSpec(
            id="T1",
            title="t1",
            bounty=100,
            deps=[],
            acceptance=[CommandSpec(cmd="true")],
        ),
    ]
    workers = [WorkerRuntime(worker_id="w1", reputation=1.0)]
    engine.create_run(run_id="run-1", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    bidder = ScriptedBidder(
        {(0, "w1"): [Bid(task_id="T1", ask=10, self_assessed_p_success=1.0, eta_minutes=10)]}
    )
    executor = StatusExecutor(status_by_task_id={"T1": VerifyStatus.MANUAL_REVIEW})

    engine.step(bidder=bidder, executor=executor)
    engine.step(bidder=bidder, executor=executor)
    events = list(ledger.iter_events())
    state = replay_ledger(events=events)

    assert state.tasks["T1"].status == "REVIEW"
    assert state.workers["w1"].balance == 0.0
    assert all(e.type not in {EventType.PAYMENT_MADE, EventType.PENALTY_APPLIED} for e in events)


def test_engine_skip_integrate_on_pass_keeps_success(tmp_path) -> None:
    ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
    engine = ClearinghouseEngine(
        ledger=ledger,
        settings=EngineSettings(max_concurrency=1, integrate_on_pass=False),
    )

    tasks = [
        TaskSpec(
            id="T1",
            title="t1",
            bounty=100,
            deps=[],
            acceptance=[CommandSpec(cmd="true")],
        ),
    ]
    workers = [WorkerRuntime(worker_id="w1", reputation=1.0)]
    engine.create_run(run_id="run-1", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    bidder = ScriptedBidder(
        {(0, "w1"): [Bid(task_id="T1", ask=10, self_assessed_p_success=1.0, eta_minutes=10)]}
    )

    class IntegratingExecutor:
        def execute(
            self,
            *,
            worker: WorkerRuntime,
            task: TaskSpec,
            bid: Bid,
            round_id: int,
            discussion_history: Sequence[Any],
        ) -> ExecutionOutcome:
            _ = worker, task, bid, round_id, discussion_history
            return ExecutionOutcome(status=VerifyStatus.PASS, notes="ok")

        def integrate(
            self,
            *,
            worker: WorkerRuntime,
            task: TaskSpec,
            bid: Bid,
            round_id: int,
            outcome: ExecutionOutcome,
        ) -> ExecutionOutcome:
            _ = worker, task, bid, round_id
            return outcome.with_status(VerifyStatus.INFRA, notes="forced_integrate_failure")

    executor = IntegratingExecutor()
    engine.step(bidder=bidder, executor=executor)
    engine.step(bidder=bidder, executor=executor)

    events = list(ledger.iter_events())
    state = replay_ledger(events=events)
    assert state.tasks["T1"].status == "DONE"
    assert any(e.type == EventType.VERIFICATION_PASSED for e in events)
    assert not any(e.type == EventType.VERIFICATION_FAILED for e in events)


def test_engine_respects_max_attempts_and_does_not_reassign_exhausted_task(tmp_path) -> None:
    ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
    engine = ClearinghouseEngine(ledger=ledger, settings=EngineSettings(max_concurrency=1))

    tasks = [
        TaskSpec(
            id="T1",
            title="fails once",
            bounty=10,
            max_attempts=1,
            deps=[],
            acceptance=[CommandSpec(cmd="true")],
        )
    ]
    workers = [WorkerRuntime(worker_id="w1", reputation=1.0)]
    engine.create_run(run_id="run-1", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    bidder = ScriptedBidder(
        {(0, "w1"): [Bid(task_id="T1", ask=1, self_assessed_p_success=1.0, eta_minutes=10)]}
    )
    executor = ScriptedExecutor(fail_task_ids={"T1"})

    engine.step(bidder=bidder, executor=executor)
    engine.step(bidder=bidder, executor=executor)
    # Additional steps should not reassign an exhausted task.
    for _ in range(3):
        engine.step(bidder=bidder, executor=executor)

    events = list(ledger.iter_events())
    state = replay_ledger(events=events)
    assert state.tasks["T1"].fail_count == 1
    assigned_events = [e for e in events if e.type == EventType.TASK_ASSIGNED]
    assert len(assigned_events) == 1


def test_engine_posts_successful_text_submission_to_discussion_when_enabled(tmp_path) -> None:
    ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
    engine = ClearinghouseEngine(
        ledger=ledger,
        settings=EngineSettings(
            max_concurrency=1,
            publish_successful_submission_to_discussion=True,
        ),
    )

    tasks = [
        TaskSpec(
            id="T1",
            title="text task",
            bounty=20,
            deps=[],
            verify_mode="judges",
            submission_kind=SubmissionKind.TEXT,
            acceptance=[],
        )
    ]
    workers = [WorkerRuntime(worker_id="w1", reputation=1.0)]
    engine.create_run(run_id="run-1", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    bidder = ScriptedBidder(
        {(0, "w1"): [Bid(task_id="T1", ask=2, self_assessed_p_success=0.9, eta_minutes=5)]}
    )
    executor = TextSubmissionExecutor()

    engine.step(bidder=bidder, executor=executor)
    engine.step(bidder=bidder, executor=executor)
    posts = [e for e in ledger.iter_events() if e.type == EventType.DISCUSSION_POST]
    assert any("Diagnosis complete" in str(e.payload.get("message") or "") for e in posts)


def test_engine_bidder_exception_does_not_crash_or_stall(tmp_path) -> None:
    ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
    engine = ClearinghouseEngine(ledger=ledger, settings=EngineSettings(max_concurrency=1))

    tasks = [
        TaskSpec(id="T1", title="t1", bounty=100, deps=[], acceptance=[CommandSpec(cmd="true")])
    ]
    workers = [WorkerRuntime(worker_id="w1", reputation=1.0)]
    engine.create_run(run_id="run-1", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    bidder = RaisingBidder(exc=RuntimeError("boom"))
    executor = ScriptedExecutor()

    engine.step(bidder=bidder, executor=executor)
    state = replay_ledger(events=list(ledger.iter_events()))
    assert state.tasks["T1"].status == "TODO"
    assert state.tasks["T1"].bounty_current > 100
    assert state.workers["w1"].assigned_task is None


def test_engine_executor_exception_is_recorded_and_task_released(tmp_path) -> None:
    ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
    engine = ClearinghouseEngine(ledger=ledger, settings=EngineSettings(max_concurrency=1))

    tasks = [
        TaskSpec(id="T1", title="t1", bounty=100, deps=[], acceptance=[CommandSpec(cmd="true")])
    ]
    workers = [WorkerRuntime(worker_id="w1", reputation=1.0)]
    engine.create_run(run_id="run-1", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    bidder = ScriptedBidder(
        {(0, "w1"): [Bid(task_id="T1", ask=10, self_assessed_p_success=1.0, eta_minutes=10)]}
    )
    executor = RaisingExecutor(exc=RuntimeError("boom"))

    engine.step(bidder=bidder, executor=executor)
    engine.step(bidder=bidder, executor=executor)
    events = list(ledger.iter_events())
    state = replay_ledger(events=events)

    assert state.tasks["T1"].status == "TODO"
    assert state.tasks["T1"].fail_count == 0
    assert state.workers["w1"].balance == 0.0
    assert state.workers["w1"].failures == 0
    assert state.workers["w1"].reputation == 1.0
    assert state.workers["w1"].assigned_task is None

    assert any(e.type == EventType.PATCH_SUBMITTED for e in events)
    assert any(e.type == EventType.TASK_COMPLETED for e in events)


def test_engine_create_run_clears_inflight_state(tmp_path) -> None:
    from concurrent.futures import Future

    ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
    engine = ClearinghouseEngine(ledger=ledger, settings=EngineSettings(max_concurrency=2))

    tasks = [
        TaskSpec(id="T1", title="t1", bounty=10, deps=[], acceptance=[CommandSpec(cmd="true")])
    ]
    workers = [
        WorkerRuntime(worker_id="fast", reputation=1.0),
        WorkerRuntime(worker_id="slow", reputation=1.0),
    ]
    engine.create_run(run_id="run-1", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    inflight_bid = Future()
    inflight_exec = Future()
    engine._inflight_bids["slow"] = _InflightBid(
        future=inflight_bid,
        started_at_monotonic=time.monotonic(),
    )
    engine._inflight_exec["T1"] = _InflightExecution(
        worker_id="fast",
        bid=Bid(task_id="T1", ask=1, self_assessed_p_success=1.0, eta_minutes=10),
        score=1.0,
        expected_cost=0.0,
        score_breakdown=None,
        future=inflight_exec,
        started_at_monotonic=time.monotonic(),
    )
    engine._bid_cache["fast"] = _CachedBids(
        bids=[Bid(task_id="T1", ask=1, self_assessed_p_success=1.0, eta_minutes=10)]
    )

    engine.create_run(run_id="run-2", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    assert engine._inflight_bids == {}
    assert engine._inflight_exec == {}
    assert engine._bid_cache == {}


def test_engine_require_bid_barrier_waits_before_market_clear(tmp_path) -> None:
    import threading

    ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
    engine = ClearinghouseEngine(
        ledger=ledger,
        settings=EngineSettings(
            max_concurrency=2, require_bid_barrier=True, bid_timeout_seconds=None
        ),
    )

    tasks = [
        TaskSpec(id="T1", title="t1", bounty=20, deps=[], acceptance=[CommandSpec(cmd="true")])
    ]
    workers = [
        WorkerRuntime(worker_id="fast", reputation=1.0),
        WorkerRuntime(worker_id="slow", reputation=1.0),
    ]
    engine.create_run(run_id="run-1", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    release_slow = threading.Event()

    class MixedBidder:
        def get_bids(
            self,
            *,
            worker: WorkerRuntime,
            ready_tasks: Sequence[ReadyTask],
            round_id: int,
            discussion_history: Sequence[Any],
        ) -> BidResult:
            _ = ready_tasks, round_id, discussion_history
            if worker.worker_id == "slow":
                release_slow.wait()
                return BidResult(
                    bids=[Bid(task_id="T1", ask=2, self_assessed_p_success=1.0, eta_minutes=10)]
                )
            return BidResult(
                bids=[Bid(task_id="T1", ask=10, self_assessed_p_success=1.0, eta_minutes=10)]
            )

    engine.step(bidder=MixedBidder(), executor=ScriptedExecutor())
    events = list(ledger.iter_events())
    assert not any(e.type == EventType.TASK_ASSIGNED for e in events)
    assert "slow" in engine._inflight_bids

    release_slow.set()
    for _ in range(10):
        engine.step(bidder=MixedBidder(), executor=ScriptedExecutor())
        events = list(ledger.iter_events())
        if any(e.type == EventType.TASK_ASSIGNED for e in events):
            break

    assigned = [e for e in events if e.type == EventType.TASK_ASSIGNED]
    assert assigned
    assert assigned[-1].payload.get("worker_id") == "slow"


def test_engine_round_advance_clears_stale_inflight_bids(tmp_path) -> None:
    import threading

    ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
    engine = ClearinghouseEngine(
        ledger=ledger,
        settings=EngineSettings(
            max_concurrency=2, require_bid_barrier=True, bid_timeout_seconds=None
        ),
    )
    tasks = [
        TaskSpec(id="T1", title="t1", bounty=20, deps=[], acceptance=[CommandSpec(cmd="true")]),
        TaskSpec(id="T2", title="t2", bounty=20, deps=["T1"], acceptance=[CommandSpec(cmd="true")]),
    ]
    workers = [
        WorkerRuntime(worker_id="fast", reputation=1.0),
        WorkerRuntime(worker_id="slow", reputation=1.0),
    ]
    engine.create_run(run_id="run-1", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    block_slow_t2 = threading.Event()

    class MixedBidder:
        def get_bids(
            self,
            *,
            worker: WorkerRuntime,
            ready_tasks: Sequence[ReadyTask],
            round_id: int,
            discussion_history: Sequence[Any],
        ) -> BidResult:
            _ = round_id, discussion_history
            ready_ids = {rt.spec.id for rt in ready_tasks}
            if "T1" in ready_ids:
                ask = 1 if worker.worker_id == "fast" else 5
                return BidResult(
                    bids=[Bid(task_id="T1", ask=ask, self_assessed_p_success=1.0, eta_minutes=10)]
                )
            if "T2" in ready_ids:
                if worker.worker_id == "slow":
                    block_slow_t2.wait(timeout=2.0)
                ask = 1 if worker.worker_id == "slow" else 2
                return BidResult(
                    bids=[Bid(task_id="T2", ask=ask, self_assessed_p_success=1.0, eta_minutes=10)]
                )
            return BidResult()

    bidder = MixedBidder()
    executor = ScriptedExecutor()

    # Step 1 assigns T1 to fast.
    engine.step(bidder=bidder, executor=executor)
    # Step 2 settles T1 and advances the round.
    engine.step(bidder=bidder, executor=executor)

    state = replay_ledger(events=list(ledger.iter_events()))
    assert state.tasks["T1"].status == "DONE"
    # In-flight bids created while settling T1 should not leak into the next round.
    assert engine._inflight_bids == {}
    block_slow_t2.set()


def test_engine_bidder_timeout_records_error_and_releases_worker(tmp_path) -> None:
    import threading

    ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
    engine = ClearinghouseEngine(
        ledger=ledger,
        settings=EngineSettings(
            max_concurrency=1, bid_timeout_seconds=0.05, execution_timeout_seconds=1.0
        ),
    )

    tasks = [
        TaskSpec(id="T1", title="t1", bounty=50, deps=[], acceptance=[CommandSpec(cmd="true")]),
    ]
    workers = [WorkerRuntime(worker_id="w1", reputation=1.0)]
    engine.create_run(run_id="run-1", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    block = threading.Event()

    class SlowBidder:
        def get_bids(
            self,
            *,
            worker: WorkerRuntime,
            ready_tasks: Sequence[ReadyTask],
            round_id: int,
            discussion_history: Sequence[Any],
        ) -> BidResult:
            _ = worker, ready_tasks, round_id, discussion_history
            block.wait(timeout=1.0)
            return BidResult()

    engine.step(bidder=SlowBidder(), executor=ScriptedExecutor())
    block.set()

    events = list(ledger.iter_events())
    bid_events = [e for e in events if e.type == EventType.BID_SUBMITTED]
    assert bid_events
    assert "bidder_timeout_after_s=0.05" in str(bid_events[-1].payload.get("error") or "")

    state = replay_ledger(events=events)
    assert state.tasks["T1"].status == "TODO"
    assert state.workers["w1"].assigned_task is None


def test_engine_executor_timeout_counts_as_failure_for_retry_budget(tmp_path) -> None:
    import threading

    ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
    engine = ClearinghouseEngine(
        ledger=ledger,
        settings=EngineSettings(
            max_concurrency=1, bid_timeout_seconds=1.0, execution_timeout_seconds=0.05
        ),
    )

    tasks = [
        TaskSpec(id="T1", title="t1", bounty=100, deps=[], acceptance=[CommandSpec(cmd="true")]),
    ]
    workers = [WorkerRuntime(worker_id="w1", reputation=1.0)]
    engine.create_run(run_id="run-1", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    bidder = ScriptedBidder(
        {(0, "w1"): [Bid(task_id="T1", ask=10, self_assessed_p_success=1.0, eta_minutes=10)]}
    )
    block = threading.Event()

    class SlowExecutor:
        def execute(
            self,
            *,
            worker: WorkerRuntime,
            task: TaskSpec,
            bid: Bid,
            round_id: int,
            discussion_history: Sequence[Any],
        ) -> ExecutionOutcome:
            _ = worker, task, bid, round_id, discussion_history
            block.wait(timeout=1.0)
            return ExecutionOutcome(status=VerifyStatus.PASS, notes="late pass")

    engine.step(bidder=bidder, executor=SlowExecutor())
    time.sleep(0.08)
    engine.step(bidder=bidder, executor=SlowExecutor())
    block.set()

    events = list(ledger.iter_events())
    completed = [e for e in events if e.type == EventType.TASK_COMPLETED]
    assert completed
    assert str(completed[-1].payload.get("verify_status") or "") == VerifyStatus.FAIL.value

    state = replay_ledger(events=events)
    assert state.tasks["T1"].status == "TODO"
    assert state.tasks["T1"].fail_count == 1
    assert state.workers["w1"].failures == 1
    assert state.workers["w1"].reputation < 1.0


def test_engine_emits_scoring_snapshots_for_market_and_assignment(tmp_path) -> None:
    ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
    engine = ClearinghouseEngine(ledger=ledger, settings=EngineSettings(max_concurrency=1))

    tasks = [
        TaskSpec(id="T1", title="t1", bounty=100, deps=[], acceptance=[CommandSpec(cmd="true")])
    ]
    workers = [WorkerRuntime(worker_id="w1", reputation=1.0)]
    engine.create_run(run_id="run-1", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    bidder = ScriptedBidder(
        {(0, "w1"): [Bid(task_id="T1", ask=20, self_assessed_p_success=0.9, eta_minutes=10)]}
    )
    executor = ScriptedExecutor()

    engine.step(bidder=bidder, executor=executor)
    events = list(ledger.iter_events())
    market = [e for e in events if e.type == EventType.MARKET_CLEARED]
    assigned = [e for e in events if e.type == EventType.TASK_ASSIGNED]
    assert market and assigned

    market_snapshot = ((market[-1].payload.get("assignments") or [])[0] or {}).get("score_snapshot")
    assigned_snapshot = assigned[-1].payload.get("score_snapshot")
    assert isinstance(market_snapshot, dict)
    assert isinstance(assigned_snapshot, dict)
    assert market_snapshot.get("components", {}).get("score") is not None
    assert assigned_snapshot.get("components", {}).get("score") is not None


def test_engine_assignment_policy_routes_selected_worker_only(tmp_path) -> None:
    ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
    engine = ClearinghouseEngine(
        ledger=ledger,
        settings=EngineSettings(max_concurrency=1, deterministic=True),
        assignment_policy=StaticAssignmentPolicy(
            selections=[RouterSelection(task_id="T1", worker_id="w2")]
        ),
    )

    tasks = [
        TaskSpec(id="T1", title="t1", bounty=100, deps=[], acceptance=[CommandSpec(cmd="true")])
    ]
    workers = [
        WorkerRuntime(worker_id="w1", reputation=1.0),
        WorkerRuntime(worker_id="w2", reputation=1.0),
    ]
    engine.create_run(run_id="run-1", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    bidder = ScriptedBidder(
        {
            (0, "w1"): [Bid(task_id="T1", ask=25, self_assessed_p_success=0.7, eta_minutes=20)],
            (0, "w2"): [Bid(task_id="T1", ask=15, self_assessed_p_success=0.9, eta_minutes=10)],
        }
    )
    executor = ScriptedExecutor()

    engine.step(bidder=bidder, executor=executor)
    engine.step(bidder=bidder, executor=executor)

    events = list(ledger.iter_events())
    router_events = [event for event in events if event.type == EventType.ROUTER_DECISION]
    bid_events = [event for event in events if event.type == EventType.BID_SUBMITTED]
    assigned = [event for event in events if event.type == EventType.TASK_ASSIGNED]

    assert len(router_events) == 1
    assert len(bid_events) == 1
    assert bid_events[0].payload["worker_id"] == "w2"
    assert assigned[-1].payload["worker_id"] == "w2"

    state = replay_ledger(events=events)
    assert state.tasks["T1"].status == "DONE"


def test_engine_assignment_policy_respects_exclusion_after_infra(tmp_path) -> None:
    ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
    engine = ClearinghouseEngine(
        ledger=ledger,
        settings=EngineSettings(
            max_concurrency=1,
            deterministic=True,
            exclude_failed_workers=True,
        ),
        assignment_policy=StaticAssignmentPolicy(
            selections=[RouterSelection(task_id="T1", worker_id="w1")]
        ),
    )

    tasks = [
        TaskSpec(
            id="T1",
            title="t1",
            bounty=100,
            max_attempts=2,
            deps=[],
            acceptance=[CommandSpec(cmd="true")],
        )
    ]
    workers = [WorkerRuntime(worker_id="w1", reputation=1.0)]
    engine.create_run(run_id="run-1", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    bidder = ScriptedBidder(
        {
            (0, "w1"): [Bid(task_id="T1", ask=15, self_assessed_p_success=0.9, eta_minutes=10)],
            (1, "w1"): [Bid(task_id="T1", ask=15, self_assessed_p_success=0.9, eta_minutes=10)],
        }
    )

    class InfraExecutor:
        def execute(
            self,
            *,
            worker: WorkerRuntime,
            task: TaskSpec,
            bid: Bid,
            round_id: int,
            discussion_history: Sequence[Any],
        ) -> ExecutionOutcome:
            _ = worker, task, bid, round_id, discussion_history
            return ExecutionOutcome(status=VerifyStatus.INFRA, notes="infra")

    engine.step(bidder=bidder, executor=InfraExecutor())
    engine.step(bidder=bidder, executor=InfraExecutor())

    events = list(ledger.iter_events())
    assignments = [event for event in events if event.type == EventType.TASK_ASSIGNED]
    assert len(assignments) == 1


def test_engine_posts_verification_feedback_to_discussion_on_failure(tmp_path) -> None:
    ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
    engine = ClearinghouseEngine(
        ledger=ledger,
        settings=EngineSettings(max_concurrency=1, deterministic=True),
    )

    tasks = [
        TaskSpec(id="T1", title="t1", bounty=100, deps=[], acceptance=[CommandSpec(cmd="true")]),
    ]
    workers = [WorkerRuntime(worker_id="w1", reputation=1.0)]
    engine.create_run(run_id="run-1", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    bidder = ScriptedBidder(
        {(0, "w1"): [Bid(task_id="T1", ask=10, self_assessed_p_success=0.8, eta_minutes=10)]}
    )

    class FailingExecutor:
        def execute(
            self,
            *,
            worker: WorkerRuntime,
            task: TaskSpec,
            bid: Bid,
            round_id: int,
            discussion_history: Sequence[Any],
        ) -> ExecutionOutcome:
            _ = worker, task, bid, round_id, discussion_history
            return ExecutionOutcome(
                status=VerifyStatus.FAIL,
                notes="verify_failed",
                verification_summary="[public] rc=1 :: pytest -q",
            )

    engine.step(bidder=bidder, executor=FailingExecutor())
    engine.step(bidder=bidder, executor=FailingExecutor())

    state = replay_ledger(events=list(ledger.iter_events()))
    assert state.discussion_history
    msg = state.discussion_history[-1].message
    assert "Prior attempt context for T1" in msg
    assert "pytest -q" in msg


def test_engine_force_bid_fallback_synthesizes_non_empty_bid(tmp_path) -> None:
    ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
    engine = ClearinghouseEngine(
        ledger=ledger,
        settings=EngineSettings(
            max_concurrency=1, deterministic=True, force_bid_for_ready_tasks=True
        ),
    )

    tasks = [
        TaskSpec(id="T1", title="t1", bounty=90, deps=[], acceptance=[CommandSpec(cmd="true")]),
    ]
    workers = [WorkerRuntime(worker_id="w1", reputation=1.0)]
    engine.create_run(run_id="run-1", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    bidder = ScriptedBidder({})
    executor = ScriptedExecutor()

    for _ in range(4):
        engine.step(bidder=bidder, executor=executor)
        state = replay_ledger(events=list(ledger.iter_events()))
        if state.tasks["T1"].status == "DONE":
            break

    events = list(ledger.iter_events())
    bid_events = [e for e in events if e.type == EventType.BID_SUBMITTED]
    assert bid_events
    bids_payload = list(bid_events[-1].payload.get("bids") or [])
    assert bids_payload
    assert str((bids_payload[0] or {}).get("notes") or "") == "engine_fallback_forced_bid"


def test_engine_exclusion_prevents_failed_worker_from_rebidding(tmp_path) -> None:
    ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
    engine = ClearinghouseEngine(
        ledger=ledger,
        settings=EngineSettings(
            max_concurrency=1,
            deterministic=True,
            exclude_failed_workers=True,
        ),
        settlement=SettlementPolicy(penalty_mode="direct_penalty", penalty_fraction=0.1),
    )

    tasks = [
        TaskSpec(
            id="T1",
            title="t1",
            bounty=90,
            max_attempts=2,
            deps=[],
            acceptance=[CommandSpec(cmd="true")],
        ),
    ]
    workers = [
        WorkerRuntime(worker_id="w1", reputation=1.0),
        WorkerRuntime(worker_id="w2", reputation=1.0),
    ]
    engine.create_run(run_id="run-1", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    class ConstantBidder:
        def get_bids(
            self,
            *,
            worker: WorkerRuntime,
            ready_tasks: Sequence[ReadyTask],
            round_id: int,
            discussion_history: Sequence[Any],
        ) -> BidResult:
            _ = round_id, discussion_history
            if not ready_tasks:
                return BidResult()
            tid = ready_tasks[0].spec.id
            return BidResult(
                bids=[
                    Bid(
                        task_id=tid,
                        ask=18,
                        self_assessed_p_success=0.90,
                        eta_minutes=20,
                    )
                ]
            )

    class FailOnceExecutor:
        def __init__(self) -> None:
            self._calls = 0

        def execute(
            self,
            *,
            worker: WorkerRuntime,
            task: TaskSpec,
            bid: Bid,
            round_id: int,
            discussion_history: Sequence[Any],
        ) -> ExecutionOutcome:
            _ = task, bid, round_id, discussion_history
            self._calls += 1
            if self._calls == 1:
                return ExecutionOutcome(status=VerifyStatus.FAIL, notes="first failure")
            return ExecutionOutcome(status=VerifyStatus.PASS, notes="pass")

    bidder = ConstantBidder()
    executor = FailOnceExecutor()

    for _ in range(10):
        engine.step(bidder=bidder, executor=executor)
        state = replay_ledger(events=list(ledger.iter_events()))
        if state.tasks["T1"].status == "DONE":
            break

    assigned_events = [e for e in ledger.iter_events() if e.type == EventType.TASK_ASSIGNED]
    assert len(assigned_events) >= 2
    first_worker = assigned_events[0].payload.get("worker_id")
    second_worker = assigned_events[1].payload.get("worker_id")
    assert first_worker != second_worker


def test_engine_exclusion_skips_forced_bids_for_failed_worker(tmp_path) -> None:
    ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
    engine = ClearinghouseEngine(
        ledger=ledger,
        settings=EngineSettings(
            max_concurrency=1,
            deterministic=True,
            force_bid_for_ready_tasks=True,
            exclude_failed_workers=True,
        ),
        settlement=SettlementPolicy(penalty_mode="direct_penalty", penalty_fraction=0.1),
    )

    tasks = [
        TaskSpec(
            id="T1",
            title="t1",
            bounty=90,
            max_attempts=2,
            deps=[],
            acceptance=[CommandSpec(cmd="true")],
        ),
    ]
    workers = [
        WorkerRuntime(worker_id="w1", reputation=1.0),
        WorkerRuntime(worker_id="w2", reputation=1.0),
    ]
    engine.create_run(run_id="run-1", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    class EmptyBidder:
        def get_bids(
            self,
            *,
            worker: WorkerRuntime,
            ready_tasks: Sequence[ReadyTask],
            round_id: int,
            discussion_history: Sequence[Any],
        ) -> BidResult:
            _ = ready_tasks, round_id, discussion_history
            return BidResult()

    class AlwaysFailExecutor:
        def execute(
            self,
            *,
            worker: WorkerRuntime,
            task: TaskSpec,
            bid: Bid,
            round_id: int,
            discussion_history: Sequence[Any],
        ) -> ExecutionOutcome:
            _ = worker, task, bid, round_id, discussion_history
            return ExecutionOutcome(status=VerifyStatus.FAIL, notes="always fail")

    bidder = EmptyBidder()
    executor = AlwaysFailExecutor()

    for _ in range(12):
        engine.step(bidder=bidder, executor=executor)
        state = replay_ledger(events=list(ledger.iter_events()))
        if state.tasks["T1"].fail_count >= 2:
            break

    assigned_events = [e for e in ledger.iter_events() if e.type == EventType.TASK_ASSIGNED]
    assert len(assigned_events) == 2
    workers_assigned = [e.payload.get("worker_id") for e in assigned_events]
    assert workers_assigned[0] != workers_assigned[1]


def test_engine_exclusion_prevents_infra_worker_from_rebidding(tmp_path) -> None:
    ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
    engine = ClearinghouseEngine(
        ledger=ledger,
        settings=EngineSettings(
            max_concurrency=1,
            deterministic=True,
            exclude_failed_workers=True,
        ),
        settlement=SettlementPolicy(penalty_mode="direct_penalty", penalty_fraction=0.1),
    )

    tasks = [
        TaskSpec(
            id="T1",
            title="t1",
            bounty=90,
            max_attempts=2,
            deps=[],
            acceptance=[CommandSpec(cmd="true")],
        ),
    ]
    workers = [
        WorkerRuntime(worker_id="w1", reputation=1.0),
        WorkerRuntime(worker_id="w2", reputation=1.0),
    ]
    engine.create_run(run_id="run-1", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    class ConstantBidder:
        def get_bids(
            self,
            *,
            worker: WorkerRuntime,
            ready_tasks: Sequence[ReadyTask],
            round_id: int,
            discussion_history: Sequence[Any],
        ) -> BidResult:
            _ = round_id, discussion_history
            if not ready_tasks:
                return BidResult()
            tid = ready_tasks[0].spec.id
            return BidResult(
                bids=[
                    Bid(
                        task_id=tid,
                        ask=20,
                        self_assessed_p_success=0.8,
                        eta_minutes=20,
                    )
                ]
            )

    class InfraThenPassExecutor:
        def __init__(self) -> None:
            self._calls = 0

        def execute(
            self,
            *,
            worker: WorkerRuntime,
            task: TaskSpec,
            bid: Bid,
            round_id: int,
            discussion_history: Sequence[Any],
        ) -> ExecutionOutcome:
            _ = worker, task, bid, round_id, discussion_history
            self._calls += 1
            if self._calls == 1:
                return ExecutionOutcome(status=VerifyStatus.INFRA, notes="infra")
            return ExecutionOutcome(status=VerifyStatus.PASS, notes="pass")

    bidder = ConstantBidder()
    executor = InfraThenPassExecutor()

    for _ in range(12):
        engine.step(bidder=bidder, executor=executor)
        state = replay_ledger(events=list(ledger.iter_events()))
        if state.tasks["T1"].status == "DONE":
            break

    assigned_events = [e for e in ledger.iter_events() if e.type == EventType.TASK_ASSIGNED]
    assert len(assigned_events) >= 2
    first_worker = assigned_events[0].payload.get("worker_id")
    second_worker = assigned_events[1].payload.get("worker_id")
    assert first_worker != second_worker


def test_engine_retry_penalty_changes_retry_winner_for_failed_worker(tmp_path) -> None:
    ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
    engine = ClearinghouseEngine(
        ledger=ledger,
        settings=EngineSettings(max_concurrency=1, deterministic=True),
        settlement=SettlementPolicy(
            penalty_mode="direct_penalty",
            retry_score_penalty_fraction=0.10,
        ),
    )

    tasks = [
        TaskSpec(id="T1", title="t1", bounty=90, deps=[], acceptance=[CommandSpec(cmd="true")]),
    ]
    workers = [
        WorkerRuntime(worker_id="w1", reputation=1.0),
        WorkerRuntime(worker_id="w2", reputation=1.0),
    ]
    engine.create_run(run_id="run-1", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    class ConstantBidder:
        def get_bids(
            self,
            *,
            worker: WorkerRuntime,
            ready_tasks: Sequence[ReadyTask],
            round_id: int,
            discussion_history: Sequence[Any],
        ) -> BidResult:
            _ = round_id, discussion_history
            if not ready_tasks:
                return BidResult()
            tid = ready_tasks[0].spec.id
            return BidResult(
                bids=[
                    Bid(
                        task_id=tid,
                        ask=18,
                        self_assessed_p_success=0.90,
                        eta_minutes=20,
                    )
                ]
            )

    class FailW2OnceExecutor:
        def __init__(self) -> None:
            self._failed_once = False

        def execute(
            self,
            *,
            worker: WorkerRuntime,
            task: TaskSpec,
            bid: Bid,
            round_id: int,
            discussion_history: Sequence[Any],
        ) -> ExecutionOutcome:
            _ = task, bid, round_id, discussion_history
            if worker.worker_id == "w2" and not self._failed_once:
                self._failed_once = True
                return ExecutionOutcome(status=VerifyStatus.FAIL, notes="first failure")
            return ExecutionOutcome(status=VerifyStatus.PASS, notes="pass")

    bidder = ConstantBidder()
    executor = FailW2OnceExecutor()

    for _ in range(8):
        engine.step(bidder=bidder, executor=executor)
        state = replay_ledger(events=list(ledger.iter_events()))
        if state.tasks["T1"].status == "DONE":
            break

    assigned_events = [e for e in ledger.iter_events() if e.type == EventType.TASK_ASSIGNED]
    assert len(assigned_events) >= 2
    assert assigned_events[0].payload.get("worker_id") == "w2"
    assert assigned_events[1].payload.get("worker_id") == "w1"
