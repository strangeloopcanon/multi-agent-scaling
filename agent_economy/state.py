from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agent_economy.schemas import (
    DerivedState,
    EventType,
    LedgerEvent,
    PaymentRule,
    TaskRuntime,
    VerifyStatus,
    WorkerRuntime,
    WorkerType,
    DiscussionMessage,
)


def _clamp(v: float, *, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


@dataclass(frozen=True)
class SettlementPolicy:
    rep_gain_on_pass: float = 0.06
    rep_loss_on_fail: float = 0.2
    rep_min: float = 0.5
    rep_max: float = 1.25

    # When verify_mode=judges, pay most now and hold back the rest until the entire run completes.
    judges_holdback_fraction: float = 0.25

    max_penalty: int = 10
    penalty_fraction: float = 0.10
    confidence_penalty_floor: float = 0.5
    confidence_penalty_max_multiplier: float = 1.0
    # Reputation mode (default) keeps current market behavior.
    # direct_penalty removes reputation from bid scoring and uses a
    # direct fail-penalty model based on bounty + reported confidence.
    penalty_mode: Literal["reputation", "direct_penalty"] = "reputation"
    # Optional task-local retry handicap used during market clearing:
    # each prior FAIL by the same worker on the same task contributes
    # retry_score_penalty_fraction * bounty_at_fail * reported_p_success.
    # 0.0 keeps existing behavior.
    retry_score_penalty_fraction: float = 0.0

    # Trigger plan revision after a task fails this many times (0 = disabled).
    replan_fail_threshold: int = 3


def replay_ledger(
    *, events: list[LedgerEvent], settlement: SettlementPolicy | None = None
) -> DerivedState:
    if not events:
        raise ValueError("cannot replay empty ledger")
    settlement = settlement or SettlementPolicy()

    run_id: str | None = None
    round_id = 0
    payment_rule = PaymentRule.ASK

    tasks: dict[str, TaskRuntime] = {}
    workers: dict[str, WorkerRuntime] = {}
    discussion_history: list[DiscussionMessage] = []

    for event in events:
        run_id = run_id or event.run_id
        if event.round_id is not None:
            round_id = max(round_id, int(event.round_id))

        p = event.payload

        if event.type == EventType.RUN_CREATED:
            raw_rule = str(p.get("payment_rule", payment_rule.value))
            if raw_rule in {r.value for r in PaymentRule}:
                payment_rule = PaymentRule(raw_rule)
            continue

        if event.type == EventType.WORKER_REGISTERED:
            worker_id = str(p["worker_id"])
            raw_type = str(p.get("worker_type", WorkerType.MODEL_AGENT.value))
            try:
                worker_type = WorkerType(raw_type)
            except Exception:
                worker_type = WorkerType.MODEL_AGENT
            workers[worker_id] = WorkerRuntime(
                worker_id=worker_id,
                worker_type=worker_type,
                model_ref=p.get("model_ref"),
                balance=float(p.get("balance", 0.0)),
                reputation=float(p.get("reputation", 1.0)),
            )
            continue

        if event.type == EventType.TASK_CREATED:
            task_id = str(p["task_id"])
            bounty = int(p["bounty"])
            tasks[task_id] = TaskRuntime(
                task_id=task_id,
                bounty_current=bounty,
                bounty_original=bounty,
            )
            continue

        if event.type == EventType.BOUNTY_ADJUSTED:
            task_id = str(p["task_id"])
            if task_id in tasks:
                tasks[task_id].bounty_current = int(p["bounty_current"])
            continue

        if event.type == EventType.TASK_ASSIGNED:
            task_id = str(p["task_id"])
            worker_id = str(p["worker_id"])
            if task_id in tasks and worker_id in workers:
                tasks[task_id].status = "ASSIGNED"
                tasks[task_id].assigned_worker = worker_id
                workers[worker_id].assigned_task = task_id
                workers[worker_id].wins += 1
            continue

        if event.type == EventType.TASK_RELEASED:
            task_id = str(p["task_id"])
            worker_id = str(p["worker_id"])
            if task_id in tasks and worker_id in workers:
                tasks[task_id].assigned_worker = None
                tasks[task_id].status = "TODO"
                workers[worker_id].assigned_task = None
            continue

        if event.type == EventType.TASK_COMPLETED:
            task_id = str(p["task_id"])
            worker_id = str(p["worker_id"])
            if task_id not in tasks or worker_id not in workers:
                continue

            workers[worker_id].assigned_task = None

            raw_status = str(p.get("verify_status") or p.get("status") or "").strip()
            status: VerifyStatus | None = None
            if raw_status:
                try:
                    status = VerifyStatus(raw_status)
                except Exception:
                    status = None
            if status is None:
                success = bool(p.get("success", False))
                status = VerifyStatus.PASS if success else VerifyStatus.FAIL

            if status == VerifyStatus.PASS:
                tasks[task_id].assigned_worker = None
                tasks[task_id].status = "DONE"
                workers[worker_id].completions += 1
                workers[worker_id].reputation = _clamp(
                    workers[worker_id].reputation + settlement.rep_gain_on_pass,
                    lo=settlement.rep_min,
                    hi=settlement.rep_max,
                )
            elif status == VerifyStatus.FAIL:
                tasks[task_id].assigned_worker = None
                tasks[task_id].status = "TODO"
                tasks[task_id].fail_count += 1
                workers[worker_id].failures += 1
                workers[worker_id].reputation = _clamp(
                    workers[worker_id].reputation - settlement.rep_loss_on_fail,
                    lo=settlement.rep_min,
                    hi=settlement.rep_max,
                )
            elif status == VerifyStatus.MANUAL_REVIEW:
                tasks[task_id].status = "REVIEW"
                tasks[task_id].assigned_worker = worker_id
            else:
                # INFRA/TIMEOUT/FLAKE_SUSPECTED: do not poison reputation or task fail_count.
                tasks[task_id].assigned_worker = None
                tasks[task_id].status = "TODO"
            continue

        if event.type == EventType.PAYMENT_MADE:
            worker_id = str(p["worker_id"])
            if worker_id in workers:
                workers[worker_id].balance += float(p["amount"])
            continue

        if event.type == EventType.PENALTY_APPLIED:
            worker_id = str(p["worker_id"])
            if worker_id in workers:
                workers[worker_id].balance -= float(p["amount"])
            continue

        if event.type == EventType.DISCUSSION_POST:
            discussion_history.append(
                DiscussionMessage(
                    sender=str(p["sender"]),
                    message=str(p["message"]),
                    ts=event.ts,
                )
            )
            continue

    if run_id is None:
        raise ValueError("ledger missing run_id")

    return DerivedState(
        run_id=run_id,
        round_id=round_id,
        tasks=tasks,
        workers=workers,
        payment_rule=payment_rule,
        discussion_history=discussion_history,
    )
