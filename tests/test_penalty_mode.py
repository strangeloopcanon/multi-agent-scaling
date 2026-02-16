from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from agent_economy.clearing import score_bid_breakdown
from agent_economy.engine import (
    BidResult,
    ClearinghouseEngine,
    EngineSettings,
    ExecutionOutcome,
    ReadyTask,
)
from agent_economy.ledger import HashChainedLedger
from agent_economy.schemas import (
    Bid,
    CommandSpec,
    EventType,
    PaymentRule,
    TaskSpec,
    VerifyStatus,
    WorkerRuntime,
)
from agent_economy.state import SettlementPolicy


class ScriptedBidder:
    def __init__(self, bid: Bid) -> None:
        self._bid = bid

    def get_bids(
        self,
        *,
        worker: WorkerRuntime,
        ready_tasks: Sequence[ReadyTask],
        round_id: int,
        discussion_history: Sequence[Any] = (),
    ) -> BidResult:
        _ = worker, ready_tasks, round_id, discussion_history
        return BidResult(bids=[self._bid])


class ScriptedExecutor:
    def __init__(self, status: VerifyStatus) -> None:
        self._status = status

    def execute(
        self,
        *,
        worker: WorkerRuntime,
        task: TaskSpec,
        bid: Bid,
        round_id: int,
        discussion_history: Sequence[Any] = (),
    ) -> ExecutionOutcome:
        _ = worker, task, bid, round_id, discussion_history
        return ExecutionOutcome(status=self._status, notes="scripted")


def test_score_bid_breakdown_reputation_mode_unchanged() -> None:
    bid = Bid(task_id="T1", ask=12, self_assessed_p_success=0.8, eta_minutes=10)
    breakdown = score_bid_breakdown(
        bounty=100,
        reputation=1.0,
        bid=bid,
        expected_cost=2.0,
    )
    assert breakdown["mode_direct_penalty"] == 0.0
    assert breakdown["score"] == pytest.approx(59.3333333, rel=1e-6)


def test_score_bid_breakdown_direct_penalty_ignores_reputation() -> None:
    bid = Bid(task_id="T1", ask=12, self_assessed_p_success=0.8, eta_minutes=10)

    b1 = score_bid_breakdown(
        bounty=100,
        reputation=0.5,
        bid=bid,
        expected_cost=2.0,
        penalty_mode="direct_penalty",
        penalty_fraction=0.1,
    )
    b2 = score_bid_breakdown(
        bounty=100,
        reputation=1.25,
        bid=bid,
        expected_cost=2.0,
        penalty_mode="direct_penalty",
        penalty_fraction=0.1,
    )

    assert b1["mode_direct_penalty"] == 1.0
    assert b2["mode_direct_penalty"] == 1.0
    assert b1["failure_penalty"] == pytest.approx(13.0)
    assert b1["score"] == pytest.approx(63.4)
    assert b1["score"] == pytest.approx(b2["score"])


def test_engine_direct_penalty_applies_configured_formula(tmp_path) -> None:
    ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
    engine = ClearinghouseEngine(
        ledger=ledger,
        settings=EngineSettings(max_concurrency=1),
        settlement=SettlementPolicy(penalty_mode="direct_penalty", penalty_fraction=0.1),
    )

    tasks = [TaskSpec(id="T1", title="t1", bounty=100, acceptance=[CommandSpec(cmd="true")])]
    workers = [WorkerRuntime(worker_id="w1", reputation=1.0)]
    engine.create_run(run_id="run-1", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    bid = Bid(task_id="T1", ask=10, self_assessed_p_success=0.8, eta_minutes=10)
    bidder = ScriptedBidder(bid)
    executor = ScriptedExecutor(VerifyStatus.FAIL)

    engine.step(bidder=bidder, executor=executor)
    engine.step(bidder=bidder, executor=executor)

    penalties = [
        e
        for e in ledger.iter_events()
        if e.type == EventType.PENALTY_APPLIED and e.payload.get("reason") == "verification_fail"
    ]
    assert penalties
    payload = penalties[0].payload
    assert payload.get("penalty_mode") == "direct_penalty"
    assert float(payload.get("amount") or 0.0) == pytest.approx(13.0)


def test_direct_penalty_score_snapshot_emitted_for_market_and_assignment(tmp_path) -> None:
    ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
    engine = ClearinghouseEngine(
        ledger=ledger,
        settings=EngineSettings(max_concurrency=1),
        settlement=SettlementPolicy(penalty_mode="direct_penalty", penalty_fraction=0.1),
    )

    tasks = [TaskSpec(id="T1", title="t1", bounty=100, acceptance=[CommandSpec(cmd="true")])]
    workers = [WorkerRuntime(worker_id="w1", reputation=1.0)]
    engine.create_run(run_id="run-1", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    bid = Bid(task_id="T1", ask=12, self_assessed_p_success=0.8, eta_minutes=10)
    bidder = ScriptedBidder(bid)
    executor = ScriptedExecutor(VerifyStatus.PASS)

    engine.step(bidder=bidder, executor=executor)

    events = list(ledger.iter_events())
    market = [e for e in events if e.type == EventType.MARKET_CLEARED]
    assigned = [e for e in events if e.type == EventType.TASK_ASSIGNED]

    assert market and assigned
    market_snapshot = ((market[-1].payload.get("assignments") or [])[0] or {}).get("score_snapshot")
    assigned_snapshot = assigned[-1].payload.get("score_snapshot")

    assert market_snapshot.get("penalty_mode") == "direct_penalty"
    assert assigned_snapshot.get("penalty_mode") == "direct_penalty"
    assert "p_success*bounty" in str(market_snapshot.get("formula") or "")
