from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from agent_economy.engine import (
    BidResult,
    ClearinghouseEngine,
    EngineSettings,
    ExecutionOutcome,
    ReadyTask,
)
from agent_economy.ledger import HashChainedLedger
from agent_economy.research.market_metrics import summarize_market_run
from agent_economy.schemas import (
    Bid,
    CommandSpec,
    PaymentRule,
    TaskSpec,
    VerifyStatus,
    WorkerRuntime,
)


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


class PassExecutor:
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
        return ExecutionOutcome(status=VerifyStatus.PASS, notes="ok")


def test_summarize_market_run_smoke(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)

    ledger = HashChainedLedger(run_dir / "ledger.jsonl")
    engine = ClearinghouseEngine(ledger=ledger, settings=EngineSettings(max_concurrency=1))
    tasks = [TaskSpec(id="T1", title="task", bounty=20, acceptance=[CommandSpec(cmd="true")])]
    workers = [WorkerRuntime(worker_id="w1", model_ref="openai:gpt-5-mini")]

    engine.create_run(run_id="run-1", payment_rule=PaymentRule.ASK, workers=workers, tasks=tasks)

    bid = Bid(task_id="T1", ask=5, self_assessed_p_success=0.9, eta_minutes=10)
    bidder = ScriptedBidder(bid)
    executor = PassExecutor()

    engine.step(bidder=bidder, executor=executor)
    engine.step(bidder=bidder, executor=executor)

    summary = summarize_market_run(run_dir=run_dir)
    assert summary["tasks_total"] == 1
    assert summary["tasks_done"] == 1
    assert summary["pass_rate"] == 1.0
    assert summary["cost_per_pass"] >= 0.0
