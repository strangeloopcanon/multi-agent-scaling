from __future__ import annotations

import argparse
import csv
import json
import os
import shlex
import shutil
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agent_economy.clearing import BidSubmission, choose_assignments, score_bid
from agent_economy.command_workers import CommandBidder, CommandExecutor, CommandExecutorSettings
from agent_economy.config import load_settings
from agent_economy.cost_estimator import ExpectedCostEstimator
from agent_economy.costing import load_pricing_from_env
from agent_economy.engine import (
    AssignmentDecision,
    ClearinghouseEngine,
    ReadyTask,
    RouterSelection,
)
from agent_economy.finalize import release_judges_holdbacks
from agent_economy.ledger import HashChainedLedger
from agent_economy.main import (
    _engine_settings,
    _llm_supports_provider,
    _llm_router_for_workers,
    _list_repo_files,
    _planner_context_files,
    _planner_worker_ref_from_env,
)
from agent_economy.model_refs import split_provider_model
from agent_economy.openai_bidder import OpenAIBidder
from agent_economy.openai_executor import ExecutorSettings, OpenAIExecutor
from agent_economy.research.market_metrics import summarize_market_runs
from agent_economy.research.swebench import SwebenchInstance, phase2_planner_goal
from agent_economy.scenario import load_scenario
from agent_economy.schemas import (
    Bid,
    CommandSpec,
    EventType,
    PaymentRule,
    SubmissionKind,
    TaskRuntime,
    TaskSpec,
    VerifyStatus,
    VerifyMode,
    WorkerRuntime,
    WorkerType,
)
from agent_economy.state import SettlementPolicy, replay_ledger
from agent_economy.worker_specs import load_worker_pool_from_path
from agent_economy.planner import PlannedTask, toposort_plan
from agent_economy.planner_workers import decompose_with_worker, revise_with_worker
from agent_economy.worker_state import (
    default_state_path,
    load_state,
    save_state,
    update_state_from_run,
)
from agent_economy.worker_state import extract_patch_usage_samples
from agent_economy.worker_mux import MultiplexBidder, MultiplexExecutor


DEFAULT_MODELS = [
    "openai:gpt-5-mini-2025-08-07",
    "openai:gpt-5.2-2025-12-11",
    "openai:gpt-5.2-pro-2025-12-11",
    "anthropic:claude-sonnet-4-5-20250929",
    "anthropic:claude-opus-4-5-20251101",
    "google:models/gemini-3-pro-preview",
]

DEFAULT_SCENARIOS = {
    "swebench": Path("scenarios/swebench_pilot_v1.yaml"),
    "synthesis": Path("scenarios/synthesis_reasoning_pilot.yaml"),
}


@dataclass(frozen=True)
class RunSpec:
    benchmark: str
    mode: str  # solo|market|central_router
    settlement_mode: str  # reputation|direct_penalty
    repeat: int
    scenario_path: str
    model_ref: str | None = None


@dataclass(frozen=True)
class PreparedTaskSpec:
    instance_id: str
    scenario_path: Path


@dataclass(frozen=True)
class PlannerRunContext:
    planner_worker: WorkerRuntime
    goal: str
    file_list: list[str]
    allowed_paths: list[str]


class RouterAssignmentChoice(BaseModel):
    task_id: str
    worker_id: str
    notes: str | None = None


class RouterAssignmentEnvelope(BaseModel):
    assignments: list[RouterAssignmentChoice] = Field(default_factory=list)
    discussion: str | None = None


def _now_tag() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")


def _norm_csv(values: str) -> list[str]:
    return [v.strip() for v in str(values).split(",") if v.strip()]


def _provider(model_ref: str) -> str:
    if ":" in model_ref:
        return model_ref.split(":", 1)[0].strip().lower()
    return "openai"


def _safe_token(value: str) -> str:
    out = "".join(ch if (ch.isalnum() or ch in "-_.") else "_" for ch in str(value).strip())
    return out or "run"


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row.keys():
            if key in seen:
                continue
            seen.add(key)
            fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _require_credentials(model_refs: list[str]) -> None:
    settings = load_settings()
    providers = {_provider(m) for m in model_refs}

    missing: list[str] = []
    if "openai" in providers and not settings.openai_api_key:
        missing.append("OPENAI_API_KEY")
    if "anthropic" in providers and not settings.anthropic_api_key:
        missing.append("ANTHROPIC_API_KEY")
    if "google" in providers and not settings.google_api_key:
        missing.append("GOOGLE_API_KEY or GEMINI_API_KEY")

    if missing:
        raise SystemExit("missing provider credentials: " + ", ".join(missing))


def build_run_matrix(
    *,
    benchmarks: list[str],
    models: list[str],
    repeats: int,
    scenario_paths: dict[str, Path],
) -> list[RunSpec]:
    out: list[RunSpec] = []
    for benchmark in benchmarks:
        scenario = scenario_paths.get(benchmark)
        if scenario is None:
            raise ValueError(f"unknown benchmark: {benchmark}")

        for repeat in range(1, repeats + 1):
            for model_ref in models:
                out.append(
                    RunSpec(
                        benchmark=benchmark,
                        mode="solo",
                        settlement_mode="reputation",
                        repeat=repeat,
                        scenario_path=str(scenario),
                        model_ref=model_ref,
                    )
                )

            out.append(
                RunSpec(
                    benchmark=benchmark,
                    mode="market",
                    settlement_mode="reputation",
                    repeat=repeat,
                    scenario_path=str(scenario),
                    model_ref=None,
                )
            )
            out.append(
                RunSpec(
                    benchmark=benchmark,
                    mode="market",
                    settlement_mode="direct_penalty",
                    repeat=repeat,
                    scenario_path=str(scenario),
                    model_ref=None,
                )
            )
    return out


def _slice_prepared_rows(
    rows: list[dict[str, Any]], *, offset: int, limit: int
) -> list[dict[str, Any]]:
    start = max(0, int(offset))
    if int(limit) <= 0:
        return list(rows[start:])
    end = start + max(0, int(limit))
    return list(rows[start:end])


def load_prepared_task_specs(
    *,
    prepared_manifest: Path,
    task_offset: int,
    task_limit: int,
) -> list[PreparedTaskSpec]:
    payload = json.loads(Path(prepared_manifest).read_text(encoding="utf-8"))
    rows_raw = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows_raw, list) or not rows_raw:
        raise ValueError("prepared manifest must contain non-empty rows")

    rows = _slice_prepared_rows(rows_raw, offset=task_offset, limit=task_limit)
    out: list[PreparedTaskSpec] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        instance_id = str(row.get("instance_id") or "").strip()
        scenario_path = str(row.get("scenario_path") or "").strip()
        if not instance_id or not scenario_path:
            continue
        out.append(
            PreparedTaskSpec(
                instance_id=instance_id,
                scenario_path=Path(scenario_path),
            )
        )
    if not out:
        raise ValueError("no valid prepared tasks selected")
    return out


def _rewrite_swebench_eval_timeout_arg(*, cmd: str, execution_timeout_seconds: float | None) -> str:
    if execution_timeout_seconds is None:
        return cmd

    timeout_seconds = int(float(execution_timeout_seconds))
    if timeout_seconds <= 0:
        return cmd

    if "agent_economy.research.swebench_eval" not in cmd:
        return cmd

    try:
        parts = shlex.split(cmd, posix=True)
    except ValueError:
        return cmd

    rewritten: list[str] = []
    replaced = False
    idx = 0
    while idx < len(parts):
        part = parts[idx]
        if part == "--timeout-sec":
            rewritten.extend(["--timeout-sec", str(timeout_seconds)])
            replaced = True
            idx += 2
            continue
        if part.startswith("--timeout-sec="):
            rewritten.append(f"--timeout-sec={timeout_seconds}")
            replaced = True
            idx += 1
            continue
        rewritten.append(part)
        idx += 1

    if not replaced:
        rewritten.extend(["--timeout-sec", str(timeout_seconds)])

    return shlex.join(rewritten)


def _rewrite_swebench_eval_commands(
    *,
    commands: list[CommandSpec],
    execution_timeout_seconds: float | None,
) -> list[CommandSpec]:
    if execution_timeout_seconds is None or float(execution_timeout_seconds) <= 0:
        return list(commands)

    rewritten: list[CommandSpec] = []
    for command in commands:
        updated_cmd = _rewrite_swebench_eval_timeout_arg(
            cmd=command.cmd,
            execution_timeout_seconds=execution_timeout_seconds,
        )
        if updated_cmd == command.cmd:
            rewritten.append(command)
            continue
        rewritten.append(command.model_copy(update={"cmd": updated_cmd}))
    return rewritten


def _rewrite_task_execution_timeouts(
    *,
    tasks: list[TaskSpec],
    execution_timeout_seconds: float | None,
) -> list[TaskSpec]:
    if execution_timeout_seconds is None or float(execution_timeout_seconds) <= 0:
        return list(tasks)

    rewritten: list[TaskSpec] = []
    for task in tasks:
        acceptance = _rewrite_swebench_eval_commands(
            commands=list(task.acceptance),
            execution_timeout_seconds=execution_timeout_seconds,
        )
        hidden_acceptance = _rewrite_swebench_eval_commands(
            commands=list(task.hidden_acceptance),
            execution_timeout_seconds=execution_timeout_seconds,
        )
        if acceptance == list(task.acceptance) and hidden_acceptance == list(
            task.hidden_acceptance
        ):
            rewritten.append(task)
            continue
        rewritten.append(
            task.model_copy(
                update={
                    "acceptance": acceptance,
                    "hidden_acceptance": hidden_acceptance,
                }
            )
        )
    return rewritten


def _select_workers(*, spec: RunSpec, workers_path: Path):
    pool = load_worker_pool_from_path(workers_path)
    workers = list(pool.workers)
    command_specs = dict(pool.command_specs)

    if spec.mode == "solo":
        if not spec.model_ref:
            raise ValueError("solo run spec requires model_ref")
        selected = [w for w in workers if (w.model_ref or "") == spec.model_ref]
        if not selected:
            raise ValueError(f"model_ref not found in workers file: {spec.model_ref}")
        workers = selected

    return workers, command_specs


def _task_specs_from_events(*, events: list[Any]) -> dict[str, TaskSpec]:
    specs: dict[str, TaskSpec] = {}
    for event in events:
        if getattr(event, "type", None) != EventType.TASK_CREATED:
            continue
        payload = getattr(event, "payload", {}) or {}
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            continue
        try:
            specs[task_id] = TaskSpec.model_validate(
                {
                    "id": task_id,
                    "title": payload.get("title", task_id),
                    "description": payload.get("description", ""),
                    "deps": payload.get("deps", []),
                    "bounty": int(payload.get("bounty", 1)),
                    "max_attempts": int(payload.get("max_attempts", 3)),
                    "verify_mode": payload.get("verify_mode", "commands"),
                    "submission_kind": payload.get("submission_kind", "patch"),
                    "acceptance": payload.get("acceptance") or [],
                    "hidden_acceptance": payload.get("hidden_acceptance") or [],
                    "judges": payload.get("judges"),
                    "allowed_paths": payload.get("allowed_paths", ["./"]),
                    "files_hint": payload.get("files_hint", []),
                    "context": payload.get("context"),
                }
            )
        except Exception:
            continue
    return specs


def _all_tasks_terminal_or_exhausted(
    *,
    state: Any,
    task_specs: dict[str, TaskSpec],
    events: list[Any],
) -> bool:
    attempts_by_task: dict[str, int] = {}
    counted_statuses = {
        VerifyStatus.FAIL.value,
        VerifyStatus.INFRA.value,
        VerifyStatus.TIMEOUT.value,
        VerifyStatus.FLAKE_SUSPECTED.value,
    }
    for event in events:
        if getattr(event, "type", None) != EventType.TASK_COMPLETED:
            continue
        payload = getattr(event, "payload", {}) or {}
        status = str(payload.get("verify_status") or payload.get("status") or "").strip()
        if status not in counted_statuses:
            continue
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            continue
        attempts_by_task[task_id] = int(attempts_by_task.get(task_id, 0) + 1)

    if not state.tasks:
        return True
    for task_id, runtime in state.tasks.items():
        if runtime.status in {"DONE", "REVIEW"}:
            continue
        spec = task_specs.get(task_id)
        if spec is None:
            return False
        attempts_used = max(int(runtime.fail_count), int(attempts_by_task.get(task_id, 0)))
        if attempts_used < int(spec.max_attempts):
            return False
    return True


def _extract_header_value(*, text: str, prefix: str) -> str | None:
    needle = f"{prefix}:"
    for line in text.splitlines():
        if line.strip().startswith(needle):
            value = line.split(":", 1)[1].strip()
            return value or None
    return None


def _extract_problem_statement(text: str) -> str:
    lines = text.splitlines()
    idx = None
    for i, line in enumerate(lines):
        if line.strip().lower() == "problem statement:":
            idx = i + 1
            break
    if idx is None:
        return text.strip()
    out: list[str] = []
    for line in lines[idx:]:
        if line.strip().lower() == "hints:":
            break
        out.append(line)
    joined = "\n".join(out).strip()
    return joined or text.strip()


def _dedupe_strs(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        v = str(raw).strip()
        if not v or v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _resolve_worker_ref(*, ref: str, workers: list[WorkerRuntime]) -> WorkerRuntime | None:
    for worker in workers:
        if worker.worker_id == ref:
            return worker
    for worker in workers:
        if (worker.model_ref or "") == ref:
            return worker
    return None


def _planner_candidates(
    *,
    workers: list[WorkerRuntime],
    command_specs: dict[str, Any],
    llm: Any,
) -> list[WorkerRuntime]:
    candidates: list[WorkerRuntime] = []
    for worker in workers:
        if worker.worker_type == WorkerType.MODEL_AGENT:
            if not worker.model_ref:
                continue
            provider, _ = split_provider_model(worker.model_ref)
            if not _llm_supports_provider(llm=llm, provider=provider):
                continue
            candidates.append(worker)
            continue
        if worker.worker_type == WorkerType.EXTERNAL_WORKER:
            spec = command_specs.get(worker.worker_id)
            if spec is not None and spec.plan_cmd:
                candidates.append(worker)
    return candidates


def _router_hint_bid(*, task_id: str, bounty: int) -> Bid:
    return Bid(
        task_id=str(task_id),
        ask=max(1, int(round(float(bounty) * 0.40))),
        self_assessed_p_success=0.50,
        eta_minutes=90,
        notes="router_expected_cost_hint",
    )


def _router_prompt(
    *,
    ready_tasks: list[ReadyTask],
    available_workers: list[WorkerRuntime],
    discussion_history: list[Any],
    round_id: int,
    cost_estimator: ExpectedCostEstimator | None,
    excluded_pairs: set[tuple[str, str]],
) -> str:
    lines: list[str] = []
    lines.append("Choose workers for the current ready tasks.")
    lines.append("Pick the workers most likely to pass verification on this round.")
    lines.append("Use each worker at most once.")
    lines.append("Use each task at most once.")
    lines.append("Return an empty assignments list when nothing looks worth assigning.")
    lines.append("")
    lines.append(f"Round: {int(round_id)}")
    lines.append("")

    if discussion_history:
        lines.append("Recent discussion:")
        for message in list(discussion_history)[-10:]:
            sender = str(getattr(message, "sender", "")).strip()
            text = str(getattr(message, "message", "")).strip()
            if sender and text:
                lines.append(f"- {sender}: {text}")
        lines.append("")

    lines.append("Ready tasks:")
    for ready in ready_tasks:
        task_id = str(ready.spec.id)
        spec = ready.spec
        runtime = ready.runtime
        lines.append(
            f"- {task_id}: title={spec.title!r} bounty={int(runtime.bounty_current)} "
            f"fail_count={int(runtime.fail_count)}"
        )
        if str(spec.description or "").strip():
            lines.append(f"  description: {str(spec.description).strip()}")
        if spec.acceptance:
            lines.append("  public_acceptance:")
            for command in spec.acceptance:
                lines.append(f"    - {command.cmd}")
        blocked_workers = sorted(
            worker_id
            for blocked_task_id, worker_id in excluded_pairs
            if str(blocked_task_id) == task_id
        )
        if blocked_workers:
            lines.append(f"  excluded_workers: {', '.join(blocked_workers)}")
    lines.append("")

    lines.append("Available workers:")
    for worker in available_workers:
        lines.append(
            f"- {worker.worker_id}: model_ref={worker.model_ref} reputation={float(worker.reputation):.2f}"
        )
        for ready in ready_tasks:
            task_id = str(ready.spec.id)
            if (task_id, worker.worker_id) in excluded_pairs:
                continue
            expected_cost_hint = 0.0
            if cost_estimator is not None:
                try:
                    expected_cost_hint = float(
                        cost_estimator.expected_cost(
                            worker=worker,
                            task=ready.spec,
                            bid=_router_hint_bid(
                                task_id=task_id,
                                bounty=int(ready.runtime.bounty_current),
                            ),
                            round_id=int(round_id),
                        )
                    )
                except Exception:
                    expected_cost_hint = 0.0
            lines.append(f"  - task={task_id} expected_cost_hint≈{expected_cost_hint:.2f}")
    lines.append("")
    lines.append(
        """Return JSON:
{
  "assignments": [
    {"task_id":"T1","worker_id":"gpt-5.2-pro","notes":"optional"}
  ],
  "discussion": "optional short public message"
}"""
    )
    return "\n".join(lines)


class CentralRouterPolicy:
    def __init__(
        self,
        *,
        llm: Any,
        model_ref: str,
    ) -> None:
        self._llm = llm
        self._model_ref = str(model_ref)

    def choose(
        self,
        *,
        round_id: int,
        ready_tasks: list[ReadyTask],
        available_workers: list[WorkerRuntime],
        discussion_history: list[Any],
        cost_estimator: ExpectedCostEstimator | None,
        excluded_pairs: set[tuple[str, str]],
    ) -> AssignmentDecision:
        system = "\n".join(
            [
                "You are a centralized task router for a software engineering benchmark.",
                "Choose the worker-task assignments most likely to pass verification.",
                "Use only the information in the prompt.",
                "Return valid JSON only.",
            ]
        )
        user = _router_prompt(
            ready_tasks=ready_tasks,
            available_workers=available_workers,
            discussion_history=discussion_history,
            round_id=round_id,
            cost_estimator=cost_estimator,
            excluded_pairs=excluded_pairs,
        )
        response, usage, _raw = self._llm.call_json(
            model_ref=self._model_ref,
            system=system,
            user=user,
            schema=RouterAssignmentEnvelope,
            max_output_tokens=1200,
        )
        envelope = (
            response
            if isinstance(response, RouterAssignmentEnvelope)
            else RouterAssignmentEnvelope.model_validate(response)
        )
        selections = [
            RouterSelection(
                task_id=str(choice.task_id),
                worker_id=str(choice.worker_id),
                notes=choice.notes,
            )
            for choice in list(envelope.assignments or [])
        ]
        return AssignmentDecision(
            selections=selections,
            model_ref=self._model_ref,
            llm_usage={
                "calls": int(getattr(usage, "calls", 0) or 0),
                "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            },
            payload={
                "policy": "central_router",
                "discussion": envelope.discussion,
                "assignments": [
                    {
                        "task_id": choice.task_id,
                        "worker_id": choice.worker_id,
                        "notes": choice.notes,
                    }
                    for choice in list(envelope.assignments or [])
                ],
            },
        )


def _select_planner_via_market(
    *,
    workers: list[WorkerRuntime],
    command_specs: dict[str, Any],
    llm: Any,
    workspace_dir: Path,
    estimator: ExpectedCostEstimator,
    planner_goal: str,
    planner_max_tasks: int,
    allowed_paths: list[str],
) -> tuple[WorkerRuntime, dict[str, Any]]:
    planner_bounty = 20
    plan_task = TaskSpec(
        id="PLAN",
        title="Planner: decompose SWE task into market subtasks",
        description="\n".join(
            [
                "Produce a decomposition plan (task DAG) for the SWE-bench goal.",
                "",
                planner_goal,
                "",
                f"Constraints: max_tasks={int(planner_max_tasks)}",
            ]
        ).strip(),
        deps=[],
        bounty=planner_bounty,
        verify_mode=VerifyMode.MANUAL,
        submission_kind=SubmissionKind.TEXT,
        allowed_paths=list(allowed_paths),
    )
    plan_rt = TaskRuntime(
        task_id=plan_task.id,
        bounty_current=planner_bounty,
        bounty_original=planner_bounty,
    )
    plan_ready = ReadyTask(spec=plan_task, runtime=plan_rt)

    candidates = _planner_candidates(workers=workers, command_specs=command_specs, llm=llm)
    if not candidates:
        raise ValueError("no planner-capable workers available for planner_market mode")

    plan_model_bidder = (
        None
        if not any(w.worker_type == WorkerType.MODEL_AGENT for w in candidates)
        else OpenAIBidder(llm=llm, payment_rule=PaymentRule.ASK, max_bids=1)
    )
    plan_ext_bidder = (
        None
        if not any(w.worker_type == WorkerType.EXTERNAL_WORKER for w in candidates)
        else CommandBidder(
            workspace_dir=workspace_dir,
            payment_rule=PaymentRule.ASK,
            specs=command_specs,
            max_bids=1,
        )
    )
    plan_bidder = MultiplexBidder(model_bidder=plan_model_bidder, external_bidder=plan_ext_bidder)

    bids_by_task: dict[str, list[BidSubmission]] = {plan_task.id: []}
    bid_records: list[dict[str, Any]] = []
    for worker in sorted(candidates, key=lambda w: w.worker_id):
        err: str | None = None
        bids: list[Bid] = []
        try:
            resp = plan_bidder.get_bids(
                worker=worker,
                ready_tasks=[plan_ready],
                round_id=0,
                discussion_history=[],
            )
            for raw_bid in list(resp.bids or [])[:1]:
                if isinstance(raw_bid, Bid):
                    bids.append(raw_bid)
                else:
                    try:
                        bids.append(Bid.model_validate(raw_bid))
                    except Exception:
                        continue
        except Exception as e:
            err = f"{type(e).__name__}: {e}"

        if err is not None:
            bid_records.append({"worker_id": worker.worker_id, "error": err})
            continue
        if not bids:
            bid_records.append({"worker_id": worker.worker_id, "bids": []})
            continue

        bid = bids[0]
        if bid.task_id != plan_task.id:
            bid_records.append({"worker_id": worker.worker_id, "bids": [bid.model_dump()]})
            continue

        expected_cost = 0.0
        try:
            expected_cost = float(
                estimator.expected_cost(worker=worker, task=plan_task, bid=bid, round_id=0)
            )
        except Exception:
            expected_cost = 0.0
        score = float(
            score_bid(
                bounty=planner_bounty,
                reputation=float(worker.reputation),
                bid=bid,
                expected_cost=expected_cost,
            )
        )
        bid_records.append(
            {
                "worker_id": worker.worker_id,
                "bid": bid.model_dump(mode="json"),
                "score": round(score, 6),
                "expected_cost": round(float(expected_cost), 6),
            }
        )
        bids_by_task[plan_task.id].append(
            BidSubmission(worker_id=worker.worker_id, bid=bid, expected_cost=expected_cost)
        )

    assignments = choose_assignments(
        ready_tasks=[plan_rt],
        available_workers=candidates,
        bids_by_task=bids_by_task,
    )
    winner = assignments[0] if assignments else None
    planner_ref = (
        winner.worker_id
        if winner is not None
        else _planner_worker_ref_from_env(workers=workers, command_specs=command_specs)
    )
    planner_worker = _resolve_worker_ref(ref=planner_ref, workers=workers)
    if planner_worker is None:
        raise ValueError(f"unknown planner worker ref: {planner_ref}")

    planner_meta = {
        "selection": "market",
        "planner_ref": planner_ref,
        "planner_worker_id": planner_worker.worker_id,
        "planner_task": plan_task.model_dump(mode="json"),
        "bids": bid_records,
        "winner": (
            None
            if winner is None
            else {
                "worker_id": winner.worker_id,
                "bid": winner.bid.model_dump(mode="json"),
                "score": round(float(winner.score), 6),
                "expected_cost": round(float(winner.expected_cost), 6),
            }
        ),
    }
    return planner_worker, planner_meta


def _planner_subtasks_to_specs(
    *,
    plan_tasks: list[PlannedTask],
    goal: str,
    base_task: TaskSpec,
) -> list[TaskSpec]:
    if not plan_tasks:
        raise ValueError("planner returned empty task list")

    prefix = "DAG"
    id_map: dict[str, str] = {}
    for idx, planned in enumerate(plan_tasks, start=1):
        raw = str(planned.id).strip() or f"T{idx}"
        mapped = f"{prefix}_{idx:02d}_{_safe_token(raw)}"
        if mapped in id_map.values():
            mapped = f"{mapped}_{idx}"
        id_map[raw] = mapped

    incoming: dict[str, int] = {tid: 0 for tid in id_map.values()}
    for planned in plan_tasks:
        for dep in planned.deps:
            mapped = id_map.get(str(dep))
            if mapped:
                incoming[mapped] = incoming.get(mapped, 0) + 1
    sinks = [id_map[str(t.id)] for t in plan_tasks if incoming.get(id_map[str(t.id)], 0) == 0]
    planned_final_id: str | None = None
    for planned in plan_tasks:
        if any("swebench_eval" in str(cmd) for cmd in list(planned.acceptance)):
            planned_final_id = id_map[str(planned.id)]
            break

    total_bounty = int(base_task.bounty)
    has_planned_final = planned_final_id is not None
    final_bounty = max(1, int(round(total_bounty * (0.6 if has_planned_final else 0.7))))
    planning_bounty_total = max(0, total_bounty - final_bounty)
    planning_task_count = max(1, len(plan_tasks) - (1 if has_planned_final else 0))
    base_each = planning_bounty_total // planning_task_count if planning_bounty_total > 0 else 1
    bounty_remainder = planning_bounty_total - (base_each * planning_task_count)

    out: list[TaskSpec] = []
    planning_task_seen = 0
    for idx, planned in enumerate(plan_tasks, start=1):
        mapped_id = id_map[str(planned.id)]
        deps = [id_map[str(dep)] for dep in planned.deps if str(dep) in id_map]
        is_planned_final = mapped_id == planned_final_id
        desc_parts = [
            "Overall goal:",
            goal.strip(),
            "",
            "Subtask:",
            str(planned.description or planned.title).strip(),
        ]
        if planned.acceptance:
            desc_parts.extend(
                [
                    "",
                    "Planner-suggested checks (informational):",
                    *[f"- {cmd}" for cmd in list(planned.acceptance)],
                ]
            )
        if is_planned_final:
            bounty = final_bounty
        else:
            planning_task_seen += 1
            bounty = max(1, int(base_each + (1 if planning_task_seen <= bounty_remainder else 0)))
        if is_planned_final:
            out.append(
                TaskSpec(
                    id=base_task.id,
                    title=base_task.title,
                    description=base_task.description,
                    deps=deps,
                    bounty=bounty,
                    max_attempts=int(base_task.max_attempts),
                    verify_mode=VerifyMode.COMMANDS,
                    submission_kind=SubmissionKind.PATCH,
                    acceptance=list(base_task.acceptance),
                    hidden_acceptance=list(base_task.hidden_acceptance),
                    allowed_paths=list(base_task.allowed_paths),
                    files_hint=_dedupe_strs(
                        [*list(planned.files_hint), *list(base_task.files_hint)]
                    ),
                )
            )
        else:
            out.append(
                TaskSpec(
                    id=mapped_id,
                    title=str(planned.title or mapped_id),
                    description="\n".join(desc_parts).strip(),
                    deps=deps,
                    bounty=bounty,
                    max_attempts=max(1, min(2, int(base_task.max_attempts))),
                    verify_mode=VerifyMode.COMMANDS,
                    submission_kind=SubmissionKind.TEXT,
                    acceptance=[CommandSpec(cmd="test -f .agent_economy/submission.txt")],
                    hidden_acceptance=[],
                    allowed_paths=list(base_task.allowed_paths),
                    files_hint=_dedupe_strs(
                        [*list(planned.files_hint), *list(base_task.files_hint)]
                    ),
                )
            )

    if not any(t.id == base_task.id for t in out):
        final_task = TaskSpec(
            id=base_task.id,
            title=base_task.title,
            description=base_task.description,
            deps=sinks,
            bounty=max(1, total_bounty - sum(int(t.bounty) for t in out)),
            max_attempts=int(base_task.max_attempts),
            verify_mode=VerifyMode.COMMANDS,
            submission_kind=SubmissionKind.PATCH,
            acceptance=list(base_task.acceptance),
            hidden_acceptance=list(base_task.hidden_acceptance),
            allowed_paths=list(base_task.allowed_paths),
            files_hint=list(base_task.files_hint),
        )
        out.append(final_task)
    return out


def _revision_tasks_for_failed_task(
    *,
    revision_id: str,
    plan_tasks: list[PlannedTask],
    goal: str,
    failed_spec: TaskSpec,
) -> list[TaskSpec]:
    if not plan_tasks:
        raise ValueError("planner revision returned empty task list")
    id_map: dict[str, str] = {}
    for idx, planned in enumerate(plan_tasks, start=1):
        raw = str(planned.id).strip() or f"T{idx}"
        id_map[raw] = f"{revision_id}_{idx:02d}_{_safe_token(raw)}"

    incoming: dict[str, int] = {mapped: 0 for mapped in id_map.values()}
    for planned in plan_tasks:
        for dep in planned.deps:
            mapped = id_map.get(str(dep))
            if mapped:
                incoming[mapped] = incoming.get(mapped, 0) + 1
    sinks = [id_map[str(t.id)] for t in plan_tasks if incoming.get(id_map[str(t.id)], 0) == 0]

    out: list[TaskSpec] = []
    for planned in plan_tasks:
        mapped = id_map[str(planned.id)]
        deps: list[str] = []
        for dep in planned.deps:
            dep_s = str(dep)
            if dep_s in id_map:
                deps.append(id_map[dep_s])
            elif dep_s in failed_spec.deps:
                deps.append(dep_s)
        out.append(
            TaskSpec(
                id=mapped,
                title=str(planned.title or mapped),
                description="\n".join(
                    [
                        "Revision goal:",
                        goal.strip(),
                        "",
                        "Subtask:",
                        str(planned.description or planned.title).strip(),
                    ]
                ).strip(),
                deps=_dedupe_strs(deps),
                bounty=max(
                    1, int(round(float(failed_spec.bounty) * 0.5 / max(1, len(plan_tasks))))
                ),
                max_attempts=max(1, min(2, int(failed_spec.max_attempts))),
                verify_mode=VerifyMode.COMMANDS,
                submission_kind=SubmissionKind.TEXT,
                acceptance=[CommandSpec(cmd="test -f .agent_economy/submission.txt")],
                hidden_acceptance=[],
                allowed_paths=list(failed_spec.allowed_paths),
                files_hint=_dedupe_strs([*list(planned.files_hint), *list(failed_spec.files_hint)]),
            )
        )

    replacement = TaskSpec(
        id=f"{revision_id}_retry",
        title=f"{failed_spec.title} (revision retry)",
        description=failed_spec.description,
        deps=sinks,
        bounty=max(1, int(failed_spec.bounty)),
        max_attempts=int(failed_spec.max_attempts),
        verify_mode=failed_spec.verify_mode,
        submission_kind=failed_spec.submission_kind,
        acceptance=list(failed_spec.acceptance),
        hidden_acceptance=list(failed_spec.hidden_acceptance),
        judges=failed_spec.judges,
        allowed_paths=list(failed_spec.allowed_paths),
        files_hint=list(failed_spec.files_hint),
    )
    out.append(replacement)
    return out


def _run_one_spec(
    *,
    spec: RunSpec,
    run_dir: Path,
    workers_path: Path,
    rounds: int,
    concurrency: int,
    bid_timeout_seconds: float | None,
    execution_timeout_seconds: float | None,
    require_bid_barrier: bool,
    integrate_on_pass: bool,
    force_bids: bool,
    retry_score_penalty_fraction: float,
    exclude_failed_workers: bool,
    dag_mode: str,
    replan: bool,
    planner_max_tasks: int,
    assignment_policy: Any | None = None,
    extra_run_config: dict[str, Any] | None = None,
) -> Path:
    run_dir.mkdir(parents=True, exist_ok=False)

    scenario = load_scenario(Path(spec.scenario_path))
    if scenario.template_dir is None:
        raise ValueError(f"scenario has no template_dir: {spec.scenario_path}")

    workspace_dir = run_dir / "workspace"
    shutil.copytree(scenario.template_dir, workspace_dir)

    workers, command_specs = _select_workers(spec=spec, workers_path=workers_path)
    settings = load_settings()
    llm = _llm_router_for_workers(settings=settings, workers=workers)

    pricing = load_pricing_from_env()
    persisted_state = load_state(default_state_path())
    estimator = ExpectedCostEstimator(state=persisted_state, pricing=pricing)

    tasks = _rewrite_task_execution_timeouts(
        tasks=list(scenario.tasks),
        execution_timeout_seconds=execution_timeout_seconds,
    )
    planner_meta: dict[str, Any] | None = None
    planner_context: PlannerRunContext | None = None
    if dag_mode == "planner_market":
        if len(tasks) != 1:
            raise ValueError("planner_market mode requires a single root SWE task in the scenario")
        root_task = tasks[0]
        root_desc = str(root_task.description or "")
        instance = SwebenchInstance(
            instance_id=str(root_task.id),
            repo=_extract_header_value(text=root_desc, prefix="Repository") or "unknown/unknown",
            base_commit=_extract_header_value(text=root_desc, prefix="Base commit") or "unknown",
            problem_statement=_extract_problem_statement(root_desc),
            test_cmd=(root_task.acceptance[0].cmd if root_task.acceptance else ""),
            hints_text=_extract_header_value(text=root_desc, prefix="Hints"),
        )
        goal = phase2_planner_goal(
            instance=instance,
            task=root_task,
            max_tasks=int(planner_max_tasks),
        )
        planner_worker, planner_meta = _select_planner_via_market(
            workers=workers,
            command_specs=command_specs,
            llm=llm,
            workspace_dir=workspace_dir,
            estimator=estimator,
            planner_goal=goal,
            planner_max_tasks=int(planner_max_tasks),
            allowed_paths=list(root_task.allowed_paths),
        )
        (run_dir / "plan_market.json").write_text(
            json.dumps(planner_meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        file_list = _list_repo_files(root=workspace_dir)
        context_files = _planner_context_files(root=workspace_dir, file_list=file_list)
        plan_call = None
        plan_error: Exception | None = None
        planner_goals = [
            goal,
            (
                goal
                + "\n\nIMPORTANT: Return strictly valid JSON matching the requested schema. "
                + "Do not include markdown fences or commentary."
            ),
        ]
        for planner_goal_attempt in planner_goals:
            try:
                plan_call = decompose_with_worker(
                    llm=llm,
                    planner=planner_worker,
                    command_specs=command_specs,
                    goal=planner_goal_attempt,
                    max_tasks=int(planner_max_tasks),
                    file_list=file_list,
                    allowed_paths=list(root_task.allowed_paths),
                    context_files=context_files,
                    cwd=workspace_dir,
                )
                break
            except Exception as e:
                plan_error = e
        if plan_call is None:
            raise ValueError(
                f"planner decomposition failed: {type(plan_error).__name__}: {plan_error}"
            )
        (run_dir / "plan_raw.txt").write_text(plan_call.raw_text, encoding="utf-8")
        plan = toposort_plan(plan=plan_call.plan)
        (run_dir / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        tasks = _planner_subtasks_to_specs(
            plan_tasks=list(plan.tasks),
            goal=goal,
            base_task=root_task,
        )
        planner_context = PlannerRunContext(
            planner_worker=planner_worker,
            goal=goal,
            file_list=file_list,
            allowed_paths=list(root_task.allowed_paths),
        )

    settlement = SettlementPolicy(
        penalty_mode=spec.settlement_mode,
        retry_score_penalty_fraction=float(retry_score_penalty_fraction),
    )
    ledger = HashChainedLedger(run_dir / "ledger.jsonl")
    base_engine_settings = _engine_settings(max_concurrency=concurrency)
    engine_settings = replace(
        base_engine_settings,
        bid_timeout_seconds=(
            base_engine_settings.bid_timeout_seconds
            if bid_timeout_seconds is None
            else (None if float(bid_timeout_seconds) <= 0 else float(bid_timeout_seconds))
        ),
        execution_timeout_seconds=(
            base_engine_settings.execution_timeout_seconds
            if execution_timeout_seconds is None
            else (
                None if float(execution_timeout_seconds) <= 0 else float(execution_timeout_seconds)
            )
        ),
        require_bid_barrier=bool(require_bid_barrier),
        integrate_on_pass=bool(integrate_on_pass),
        force_bid_for_ready_tasks=bool(force_bids),
        exclude_failed_workers=bool(exclude_failed_workers),
        publish_successful_submission_to_discussion=bool(dag_mode == "planner_market"),
    )
    engine = ClearinghouseEngine(
        ledger=ledger,
        settings=engine_settings,
        settlement=settlement,
        assignment_policy=assignment_policy,
    )
    run_id = run_dir.name
    engine.create_run(run_id=run_id, workers=workers, tasks=tasks)

    has_external = any(w.worker_type.value == "external_worker" for w in workers)

    judge_workers = [w.worker_id for w in workers][:3]
    model_bidder = OpenAIBidder(
        llm=llm,
        payment_rule=replay_ledger(events=list(ledger.iter_events())).payment_rule,
        max_bids=2,
        penalty_mode=settlement.penalty_mode,
        penalty_fraction=settlement.penalty_fraction,
        force_bid_for_ready_tasks=bool(force_bids),
        retry_score_penalty_fraction=float(settlement.retry_score_penalty_fraction),
    )
    model_executor = OpenAIExecutor(
        llm=llm,
        workspace_dir=workspace_dir,
        run_dir=run_dir,
        workers=workers,
        command_specs=command_specs,
        settings=ExecutorSettings(judge_workers=judge_workers),
    )

    ext_bidder = (
        None
        if not has_external
        else CommandBidder(
            workspace_dir=workspace_dir,
            payment_rule=replay_ledger(events=list(ledger.iter_events())).payment_rule,
            specs=command_specs,
            max_bids=2,
        )
    )
    ext_executor = (
        None
        if not has_external
        else CommandExecutor(
            workspace_dir=workspace_dir,
            run_dir=run_dir,
            workers=workers,
            specs=command_specs,
            settings=CommandExecutorSettings(judge_workers=judge_workers),
            llm=llm,
        )
    )

    bidder = MultiplexBidder(model_bidder=model_bidder, external_bidder=ext_bidder)
    executor = MultiplexExecutor(model_executor=model_executor, external_executor=ext_executor)

    events = list(ledger.iter_events())
    state = replay_ledger(events=events, settlement=settlement)
    task_specs = _task_specs_from_events(events=events)
    handled_replans: set[str] = set()
    max_rounds = max(1, int(rounds))
    # A single logical round can require multiple engine.step() calls when
    # bid-barrier mode is enabled. Use a step cap to prevent hangs while
    # avoiding premature cutoffs.
    max_step_calls = max(max_rounds, max_rounds * 8)
    step_calls = 0
    while int(state.round_id) < max_rounds and step_calls < max_step_calls:
        engine.step(bidder=bidder, executor=executor, cost_estimator=estimator)
        step_calls += 1
        events = list(ledger.iter_events())
        state = replay_ledger(events=events, settlement=settlement)
        task_specs.update(_task_specs_from_events(events=events))

        if dag_mode == "planner_market" and replan and planner_context is not None:
            replan_log_path = run_dir / "replan_events.jsonl"
            for event in events:
                if event.type != EventType.PLAN_REVISION_REQUESTED:
                    continue
                if event.event_id in handled_replans:
                    continue
                handled_replans.add(event.event_id)

                failed_task_id = str(event.payload.get("task_id") or "").strip()
                failed_spec = task_specs.get(failed_task_id)
                failed_runtime = state.tasks.get(failed_task_id)
                if not failed_task_id or failed_spec is None or failed_runtime is None:
                    _append_jsonl(
                        replan_log_path,
                        {
                            "event_id": event.event_id,
                            "status": "skipped_missing_failed_task",
                            "task_id": failed_task_id,
                        },
                    )
                    continue

                completed_ids = sorted(
                    tid for tid, rt in state.tasks.items() if rt.status == "DONE"
                )
                remaining_ids = sorted(
                    tid for tid, rt in state.tasks.items() if rt.status != "DONE"
                )
                failure_notes = str(event.payload.get("reason") or "").strip() or None

                try:
                    revision_call = revise_with_worker(
                        llm=llm,
                        planner=planner_context.planner_worker,
                        command_specs=command_specs,
                        goal=planner_context.goal,
                        failed_task_id=failed_task_id,
                        failed_task_title=str(failed_spec.title),
                        failed_task_description=str(failed_spec.description),
                        fail_count=int(failed_runtime.fail_count),
                        completed_task_ids=completed_ids,
                        remaining_task_ids=remaining_ids,
                        file_list=list(planner_context.file_list),
                        allowed_paths=list(planner_context.allowed_paths),
                        discussion_history=list(state.discussion_history),
                        failure_notes=failure_notes,
                        cwd=workspace_dir,
                    )
                    revised = toposort_plan(plan=revision_call.plan)
                except Exception as e:
                    _append_jsonl(
                        replan_log_path,
                        {
                            "event_id": event.event_id,
                            "status": "revision_failed",
                            "task_id": failed_task_id,
                            "error": f"{type(e).__name__}: {e}",
                        },
                    )
                    continue

                revision_id = f"REV_{_safe_token(failed_task_id)}_{len(handled_replans):02d}"
                try:
                    new_tasks = _revision_tasks_for_failed_task(
                        revision_id=revision_id,
                        plan_tasks=list(revised.tasks),
                        goal=planner_context.goal,
                        failed_spec=failed_spec,
                    )
                    for task in new_tasks:
                        engine.inject_task(run_id=run_id, round_id=state.round_id, task=task)
                        task_specs[task.id] = task
                except Exception as e:
                    _append_jsonl(
                        replan_log_path,
                        {
                            "event_id": event.event_id,
                            "status": "inject_failed",
                            "task_id": failed_task_id,
                            "error": f"{type(e).__name__}: {e}",
                        },
                    )
                    continue

                _append_jsonl(
                    replan_log_path,
                    {
                        "event_id": event.event_id,
                        "status": "injected",
                        "task_id": failed_task_id,
                        "new_task_ids": [t.id for t in new_tasks],
                    },
                )

                events = list(ledger.iter_events())
                state = replay_ledger(events=events, settlement=settlement)
                task_specs.update(_task_specs_from_events(events=events))

        if _all_tasks_terminal_or_exhausted(state=state, task_specs=task_specs, events=events):
            break

    release_judges_holdbacks(ledger=ledger)
    events = list(ledger.iter_events())
    state = replay_ledger(events=events, settlement=settlement)

    (run_dir / "state.json").write_text(state.model_dump_json(indent=2), encoding="utf-8")

    persisted = load_state(default_state_path())
    persisted = update_state_from_run(
        state=persisted,
        run_workers=state.workers,
        patch_usages=extract_patch_usage_samples(events=events),
    )
    save_state(default_state_path(), persisted)

    run_config = {
        "run_id": run_id,
        "scenario_path": str(Path(spec.scenario_path).resolve()),
        "workspace_dir": str(workspace_dir.resolve()),
        "workers": [w.model_dump() for w in workers],
        "mode": spec.mode,
        "settlement_mode": spec.settlement_mode,
        "repeat": spec.repeat,
        "benchmark": spec.benchmark,
        "model_ref": spec.model_ref,
        "dag_mode": dag_mode,
        "replan": bool(replan),
        "planner_max_tasks": int(planner_max_tasks),
        "max_rounds": int(max_rounds),
        "step_calls": int(step_calls),
        "max_step_calls": int(max_step_calls),
        "planner": planner_meta,
        "engine_settings": {
            "max_concurrency": int(engine_settings.max_concurrency),
            "bid_timeout_seconds": engine_settings.bid_timeout_seconds,
            "execution_timeout_seconds": engine_settings.execution_timeout_seconds,
            "require_bid_barrier": bool(engine_settings.require_bid_barrier),
            "integrate_on_pass": bool(engine_settings.integrate_on_pass),
            "force_bid_for_ready_tasks": bool(engine_settings.force_bid_for_ready_tasks),
            "exclude_failed_workers": bool(engine_settings.exclude_failed_workers),
            "publish_successful_submission_to_discussion": bool(
                engine_settings.publish_successful_submission_to_discussion
            ),
        },
        "settlement": {
            "penalty_mode": str(settlement.penalty_mode),
            "penalty_fraction": float(settlement.penalty_fraction),
            "retry_score_penalty_fraction": float(settlement.retry_score_penalty_fraction),
        },
    }
    if extra_run_config:
        run_config.update(dict(extra_run_config))
    (run_dir / "run_config.json").write_text(json.dumps(run_config, indent=2), encoding="utf-8")

    return run_dir


def _aggregate_model_outcomes(*, summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_model: dict[str, dict[str, float]] = {}
    for run in summaries:
        for worker in list(run.get("workers") or []):
            model_ref = str(worker.get("model_ref") or "")
            if not model_ref:
                continue
            usage = worker.get("usage") if isinstance(worker.get("usage"), dict) else {}
            agg = by_model.setdefault(
                model_ref,
                {
                    "model_ref": model_ref,
                    "wins": 0.0,
                    "completions": 0.0,
                    "penalties": 0.0,
                    "input_tokens": 0.0,
                    "output_tokens": 0.0,
                    "calls": 0.0,
                    "runs": 0.0,
                },
            )
            agg["wins"] += float(worker.get("wins") or 0.0)
            agg["completions"] += float(worker.get("completions") or 0.0)
            agg["penalties"] += float(worker.get("penalties") or 0.0)
            agg["input_tokens"] += float((usage or {}).get("input_tokens") or 0.0)
            agg["output_tokens"] += float((usage or {}).get("output_tokens") or 0.0)
            agg["calls"] += float((usage or {}).get("calls") or 0.0)
            agg["runs"] += 1.0

    rows = []
    for model_ref in sorted(by_model.keys()):
        agg = by_model[model_ref]
        completions = float(agg["completions"])
        total_tokens = float(agg["input_tokens"] + agg["output_tokens"])
        rows.append(
            {
                "model_ref": model_ref,
                "wins": int(agg["wins"]),
                "completions": int(completions),
                "penalties": float(agg["penalties"]),
                "input_tokens": int(agg["input_tokens"]),
                "output_tokens": int(agg["output_tokens"]),
                "total_tokens": int(total_tokens),
                "calls": int(agg["calls"]),
                "tokens_per_completion": (total_tokens / completions) if completions > 0 else 0.0,
            }
        )
    return rows


def _run_prepared_mode(args: argparse.Namespace, models: list[str]) -> None:
    prepared_specs = load_prepared_task_specs(
        prepared_manifest=Path(args.task_manifest),
        task_offset=int(args.task_offset),
        task_limit=int(args.task_limit),
    )
    prepared_mode = str(getattr(args, "prepared_mode", "market") or "market").strip()
    if prepared_mode not in {"market", "central_router"}:
        raise ValueError(f"unsupported prepared mode: {prepared_mode}")

    if bool(args.isolate_state):
        os.environ["AE_WORKER_STATE_ISOLATION"] = "1"

    args.output_root.mkdir(parents=True, exist_ok=True)
    selected_rows = [
        {
            "instance_id": spec.instance_id,
            "scenario_path": str(spec.scenario_path),
        }
        for spec in prepared_specs
    ]
    (args.output_root / "selected_tasks.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in selected_rows),
        encoding="utf-8",
    )

    quality_path = args.output_root / "quality_checks.jsonl"
    task_runs_path = args.output_root / "task_runs.jsonl"

    if not args.execute:
        (args.output_root / "run_matrix.json").write_text(
            json.dumps(selected_rows, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"prepared_tasks={len(prepared_specs)}")
        print(f"run_matrix={args.output_root / 'run_matrix.json'}")
        return

    credential_models = list(models)
    if prepared_mode == "central_router" and str(args.router_model_ref) not in credential_models:
        credential_models.append(str(args.router_model_ref))
    _require_credentials(credential_models)

    router_policy_template = None
    if prepared_mode == "central_router":
        router_workers, _ = _select_workers(
            spec=RunSpec(
                benchmark="swebench",
                mode="market",
                settlement_mode=str(args.settlement_mode),
                repeat=1,
                scenario_path=str(prepared_specs[0].scenario_path),
                model_ref=None,
            ),
            workers_path=Path(args.workers),
        )
        router_provider = _provider(str(args.router_model_ref))
        if router_provider not in {_provider(worker.model_ref or "") for worker in router_workers}:
            router_workers = [
                *router_workers,
                WorkerRuntime(
                    worker_id="central-router",
                    worker_type=WorkerType.MODEL_AGENT,
                    model_ref=str(args.router_model_ref),
                ),
            ]
        router_policy_template = CentralRouterPolicy(
            llm=_llm_router_for_workers(settings=load_settings(), workers=router_workers),
            model_ref=str(args.router_model_ref),
        )

    completed_run_dirs: list[Path] = []
    task_rows: list[dict[str, Any]] = []
    skipped = 0
    failed = 0

    for idx, spec in enumerate(prepared_specs, start=1):
        run_name = (
            f"swebench_{_safe_token(prepared_mode)}_{_safe_token(args.settlement_mode)}_"
            f"{idx:03d}_{_safe_token(spec.instance_id)}"
        )
        run_dir = args.output_root / run_name

        if run_dir.exists():
            if args.resume:
                skipped += 1
                row = {
                    "instance_id": spec.instance_id,
                    "run_dir": str(run_dir),
                    "status": "skipped_existing",
                }
                task_rows.append(row)
                _append_jsonl(task_runs_path, row)
                continue
            if not args.overwrite:
                raise SystemExit(f"run dir already exists: {run_dir}")
            shutil.rmtree(run_dir)

        run_spec = RunSpec(
            benchmark="swebench",
            mode="market" if prepared_mode == "market" else "central_router",
            settlement_mode=str(args.settlement_mode),
            repeat=1,
            scenario_path=str(spec.scenario_path),
            model_ref=None,
        )
        assignment_policy = router_policy_template

        try:
            finished = _run_one_spec(
                spec=run_spec,
                run_dir=run_dir,
                workers_path=Path(args.workers),
                rounds=int(args.rounds),
                concurrency=int(args.concurrency),
                bid_timeout_seconds=(
                    None if args.bid_timeout_seconds is None else float(args.bid_timeout_seconds)
                ),
                execution_timeout_seconds=(
                    None
                    if args.execution_timeout_seconds is None
                    else float(args.execution_timeout_seconds)
                ),
                require_bid_barrier=bool(args.require_bid_barrier),
                integrate_on_pass=bool(str(args.dag_mode) == "planner_market"),
                force_bids=bool(args.force_bids),
                retry_score_penalty_fraction=float(args.retry_score_penalty_fraction),
                exclude_failed_workers=bool(args.exclude_failed_workers),
                dag_mode=str(args.dag_mode),
                replan=bool(args.replan),
                planner_max_tasks=int(args.planner_max_tasks),
                assignment_policy=assignment_policy,
                extra_run_config={
                    "prepared_mode": prepared_mode,
                    "router_model_ref": (
                        None if prepared_mode != "central_router" else str(args.router_model_ref)
                    ),
                },
            )
            completed_run_dirs.append(finished)
            row = {
                "instance_id": spec.instance_id,
                "run_dir": str(run_dir),
                "status": "completed",
            }
        except Exception as e:
            failed += 1
            row = {
                "instance_id": spec.instance_id,
                "run_dir": str(run_dir),
                "status": "failed",
                "error": f"{type(e).__name__}: {e}",
            }
            if not args.continue_on_error:
                task_rows.append(row)
                _append_jsonl(task_runs_path, row)
                raise

        task_rows.append(row)
        _append_jsonl(task_runs_path, row)

        if int(args.check_every) > 0 and (
            idx % int(args.check_every) == 0 or idx == len(prepared_specs)
        ):
            check = {
                "at": idx,
                "total": len(prepared_specs),
                "completed": len([r for r in task_rows if r.get("status") == "completed"]),
                "failed": len([r for r in task_rows if r.get("status") == "failed"]),
                "skipped_existing": len(
                    [r for r in task_rows if r.get("status") == "skipped_existing"]
                ),
            }
            _append_jsonl(quality_path, check)

    summaries = summarize_market_runs(run_dirs=completed_run_dirs)
    (args.output_root / "market_run_summaries.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary_by_dir = {str(Path(r.get("run_dir") or "")): r for r in summaries}
    task_outcomes: list[dict[str, Any]] = []
    for row in task_rows:
        out = dict(row)
        run_summary = summary_by_dir.get(str(row.get("run_dir") or ""), {})
        if isinstance(run_summary, dict):
            out["pass_rate"] = float(run_summary.get("pass_rate") or 0.0)
            out["tasks_done"] = int(run_summary.get("tasks_done") or 0)
            out["tasks_total"] = int(run_summary.get("tasks_total") or 0)
            tokens = (
                run_summary.get("tokens") if isinstance(run_summary.get("tokens"), dict) else {}
            )
            out["total_tokens"] = int((tokens or {}).get("total") or 0)
            penalties = (
                run_summary.get("penalties")
                if isinstance(run_summary.get("penalties"), dict)
                else {}
            )
            out["penalties_total"] = float((penalties or {}).get("total") or 0.0)
        task_outcomes.append(out)

    model_outcomes = _aggregate_model_outcomes(summaries=summaries)

    _write_csv(args.output_root / "task_outcomes.csv", task_outcomes)
    _write_csv(args.output_root / "model_outcomes.csv", model_outcomes)

    final_summary = {
        "mode": f"prepared_{prepared_mode}_only",
        "settlement_mode": str(args.settlement_mode),
        "selected_tasks": len(prepared_specs),
        "completed_runs": len(completed_run_dirs),
        "failed_runs": int(failed),
        "skipped_existing": int(skipped),
        "output_root": str(args.output_root.resolve()),
        "isolate_state": bool(args.isolate_state),
        "rounds": int(args.rounds),
        "concurrency": int(args.concurrency),
        "bid_timeout_seconds": args.bid_timeout_seconds,
        "execution_timeout_seconds": args.execution_timeout_seconds,
        "require_bid_barrier": bool(args.require_bid_barrier),
        "force_bids": bool(args.force_bids),
        "retry_score_penalty_fraction": float(args.retry_score_penalty_fraction),
        "exclude_failed_workers": bool(args.exclude_failed_workers),
        "dag_mode": str(args.dag_mode),
        "replan": bool(args.replan),
        "planner_max_tasks": int(args.planner_max_tasks),
        "router_model_ref": (
            None if prepared_mode != "central_router" else str(args.router_model_ref)
        ),
    }
    (args.output_root / "final_summary.json").write_text(
        json.dumps(final_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"completed_runs={len(completed_run_dirs)}")
    print(f"failed_runs={failed}")
    print(f"skipped_existing={skipped}")
    print(f"summaries={args.output_root / 'market_run_summaries.json'}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Phase II matrix or prepared market experiments"
    )
    parser.add_argument(
        "--benchmarks",
        default="swebench,synthesis",
        help="comma-separated benchmark aliases",
    )
    parser.add_argument(
        "--models",
        default=",".join(DEFAULT_MODELS),
        help="comma-separated model refs",
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--rounds", type=int, default=24)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument(
        "--bid-timeout-seconds",
        type=float,
        default=90.0,
        help="bidder timeout in seconds (<=0 disables timeout)",
    )
    parser.add_argument(
        "--execution-timeout-seconds",
        type=float,
        default=2400.0,
        help="execution timeout in seconds (<=0 disables timeout)",
    )
    parser.add_argument(
        "--require-bid-barrier",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="wait for all in-flight bidder calls (or timeout) before market clearing",
    )
    parser.add_argument(
        "--workers",
        type=Path,
        default=Path("benchmarks/workers_phase2_mixed.json"),
        help="worker pool JSON path",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("runs/research/phase2") / _now_tag(),
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--overwrite", action="store_true")

    parser.add_argument("--task-manifest", type=Path, default=None)
    parser.add_argument(
        "--prepared-mode",
        choices=["market", "central_router"],
        default="market",
        help="assignment mode for prepared task-manifest runs",
    )
    parser.add_argument("--task-offset", type=int, default=0)
    parser.add_argument("--task-limit", type=int, default=0)
    parser.add_argument("--market-only", action="store_true")
    parser.add_argument("--settlement-mode", default="direct_penalty")
    parser.add_argument(
        "--force-bids",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="synthesize conservative fallback bids when a worker returns no valid bids",
    )
    parser.add_argument(
        "--retry-score-penalty-fraction",
        type=float,
        default=0.0,
        help="task-local score handicap multiplier for prior FAILs by same worker on same task",
    )
    parser.add_argument(
        "--exclude-failed-workers",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="exclude workers who previously failed a task from rebidding on that task",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--check-every", type=int, default=25)
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument(
        "--dag-mode",
        choices=["off", "planner_market"],
        default="off",
        help="decomposition mode for prepared SWE tasks",
    )
    parser.add_argument(
        "--planner-max-tasks",
        type=int,
        default=8,
        help="upper bound for planner DAG size in planner_market mode",
    )
    parser.add_argument(
        "--replan",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="enable plan revision on repeated failures (defaults to true in planner_market mode)",
    )
    parser.add_argument(
        "--isolate-state",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="isolate persisted worker state for this run mode",
    )
    parser.add_argument(
        "--router-model-ref",
        default="openai:gpt-5.2-pro-2025-12-11",
        help="central router model for prepared central_router runs",
    )

    args = parser.parse_args()
    if args.replan is None:
        args.replan = bool(str(args.dag_mode) == "planner_market")
    models = _norm_csv(args.models)

    if args.task_manifest is not None:
        _run_prepared_mode(args, models)
        return

    benchmarks = _norm_csv(args.benchmarks)
    scenario_paths = dict(DEFAULT_SCENARIOS)
    matrix = build_run_matrix(
        benchmarks=benchmarks,
        models=models,
        repeats=int(args.repeats),
        scenario_paths=scenario_paths,
    )

    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "run_matrix.json").write_text(
        json.dumps([asdict(r) for r in matrix], indent=2),
        encoding="utf-8",
    )

    if not args.execute:
        print(f"matrix_size={len(matrix)}")
        print(f"run_matrix={args.output_root / 'run_matrix.json'}")
        return

    _require_credentials(models)

    completed: list[Path] = []
    for spec in matrix:
        run_name = (
            f"{spec.benchmark}_{spec.mode}_{spec.settlement_mode}_"
            f"r{spec.repeat}_{(spec.model_ref or 'all').replace(':', '_')}"
        )
        run_dir = args.output_root / run_name
        if run_dir.exists():
            if not args.overwrite:
                raise SystemExit(f"run dir already exists: {run_dir}")
            shutil.rmtree(run_dir)

        completed.append(
            _run_one_spec(
                spec=spec,
                run_dir=run_dir,
                workers_path=Path(args.workers),
                rounds=int(args.rounds),
                concurrency=int(args.concurrency),
                bid_timeout_seconds=(
                    None if args.bid_timeout_seconds is None else float(args.bid_timeout_seconds)
                ),
                execution_timeout_seconds=(
                    None
                    if args.execution_timeout_seconds is None
                    else float(args.execution_timeout_seconds)
                ),
                require_bid_barrier=bool(args.require_bid_barrier),
                integrate_on_pass=True,
                force_bids=bool(args.force_bids),
                retry_score_penalty_fraction=float(args.retry_score_penalty_fraction),
                exclude_failed_workers=bool(args.exclude_failed_workers),
                dag_mode="off",
                replan=False,
                planner_max_tasks=int(args.planner_max_tasks),
            )
        )

    summaries = summarize_market_runs(run_dirs=completed)
    (args.output_root / "market_run_summaries.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"completed_runs={len(completed)}")
    print(f"summaries={args.output_root / 'market_run_summaries.json'}")


if __name__ == "__main__":
    main()
