"""End-to-end integration tests for agent-economy.

Tests the full flow: init -> run -> verify tasks complete.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

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
    DiscussionMessage,
    EventType,
    PaymentRule,
    TaskSpec,
    VerifyStatus,
    WorkerRuntime,
)
from agent_economy.state import SettlementPolicy, replay_ledger


class DeterministicBidder:
    """Bidder that bids on tasks in a predictable way for testing."""

    def __init__(self, *, ask: int = 5, p_success: float = 0.9) -> None:
        self._ask = ask
        self._p_success = p_success

    def get_bids(
        self,
        *,
        worker: WorkerRuntime,
        ready_tasks: Sequence[ReadyTask],
        round_id: int,
        discussion_history: Sequence[DiscussionMessage] = (),
    ) -> BidResult:
        _ = round_id, discussion_history
        bids: list[Bid] = []
        for rt in ready_tasks:
            bids.append(
                Bid(
                    task_id=rt.spec.id,
                    ask=self._ask,
                    self_assessed_p_success=self._p_success,
                    eta_minutes=10,
                )
            )
        return BidResult(bids=bids[:2])


class DeterministicExecutor:
    """Executor that passes or fails tasks based on configuration."""

    def __init__(self, *, fail_task_ids: set[str] | None = None) -> None:
        self._fail_task_ids = fail_task_ids or set()

    def execute(
        self,
        *,
        worker: WorkerRuntime,
        task: TaskSpec,
        bid: Bid,
        round_id: int,
        discussion_history: Sequence[DiscussionMessage] = (),
    ) -> ExecutionOutcome:
        _ = worker, bid, round_id, discussion_history
        if task.id in self._fail_task_ids:
            return ExecutionOutcome(status=VerifyStatus.FAIL, notes="scripted fail")
        return ExecutionOutcome(status=VerifyStatus.PASS, notes="scripted pass")


class TestE2EFullMarketRun:
    """End-to-end tests for a complete market run."""

    @pytest.fixture
    def mini_scenario(self) -> list[TaskSpec]:
        """A minimal 3-task scenario with dependencies."""
        return [
            TaskSpec(
                id="T1",
                title="Setup project structure",
                bounty=20,
                deps=[],
                acceptance=[CommandSpec(cmd="true")],
            ),
            TaskSpec(
                id="T2",
                title="Implement core logic",
                bounty=40,
                deps=["T1"],
                acceptance=[CommandSpec(cmd="true")],
            ),
            TaskSpec(
                id="T3",
                title="Add tests",
                bounty=30,
                deps=["T1"],
                acceptance=[CommandSpec(cmd="true")],
            ),
        ]

    @pytest.fixture
    def workers(self) -> list[WorkerRuntime]:
        """Two workers with different reputations."""
        return [
            WorkerRuntime(worker_id="worker-fast", reputation=1.0),
            WorkerRuntime(worker_id="worker-reliable", reputation=1.1),
        ]

    def test_full_run_all_tasks_complete(
        self, tmp_path: Path, mini_scenario: list[TaskSpec], workers: list[WorkerRuntime]
    ) -> None:
        """Run a complete scenario and verify all tasks complete."""
        ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
        engine = ClearinghouseEngine(
            ledger=ledger,
            settings=EngineSettings(max_concurrency=2),
        )

        engine.create_run(
            run_id="e2e-test-1",
            payment_rule=PaymentRule.ASK,
            workers=workers,
            tasks=mini_scenario,
        )

        bidder = DeterministicBidder(ask=5, p_success=0.95)
        executor = DeterministicExecutor()

        # Run enough steps to complete all tasks
        for _ in range(20):
            engine.step(bidder=bidder, executor=executor)
            state = replay_ledger(events=list(ledger.iter_events()))
            if all(t.status == "DONE" for t in state.tasks.values()):
                break

        # Verify all tasks completed
        final_state = replay_ledger(events=list(ledger.iter_events()))
        assert final_state.tasks["T1"].status == "DONE"
        assert final_state.tasks["T2"].status == "DONE"
        assert final_state.tasks["T3"].status == "DONE"

        # Verify ledger integrity
        ledger.verify_chain()

        # Verify workers got paid
        total_paid = sum(w.balance for w in final_state.workers.values())
        assert total_paid > 0, "Workers should have been paid"

    def test_dependency_ordering_respected(
        self, tmp_path: Path, mini_scenario: list[TaskSpec], workers: list[WorkerRuntime]
    ) -> None:
        """Verify tasks with dependencies aren't started before deps complete."""
        ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
        engine = ClearinghouseEngine(
            ledger=ledger,
            settings=EngineSettings(max_concurrency=1),
        )

        engine.create_run(
            run_id="e2e-deps-test",
            payment_rule=PaymentRule.ASK,
            workers=workers,
            tasks=mini_scenario,
        )

        bidder = DeterministicBidder()
        executor = DeterministicExecutor()

        # Track assignment order
        assigned_order: list[str] = []

        for _ in range(30):
            engine.step(bidder=bidder, executor=executor)
            events = list(ledger.iter_events())
            for e in events:
                if e.type == EventType.TASK_ASSIGNED:
                    tid = e.payload.get("task_id", "")
                    if tid and tid not in assigned_order:
                        assigned_order.append(tid)

            state = replay_ledger(events=events)
            if all(t.status == "DONE" for t in state.tasks.values()):
                break

        # T1 must be assigned before T2 (T2 depends on T1)
        assert "T1" in assigned_order
        assert "T2" in assigned_order
        assert assigned_order.index("T1") < assigned_order.index("T2")

    def test_failed_task_reopens_and_bounty_bumps(
        self, tmp_path: Path, workers: list[WorkerRuntime]
    ) -> None:
        """Verify failed tasks are reopened and bounty increases on failure."""
        tasks = [
            TaskSpec(
                id="T1",
                title="Difficult task",
                bounty=50,
                deps=[],
                acceptance=[CommandSpec(cmd="true")],
            ),
        ]

        ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
        engine = ClearinghouseEngine(
            ledger=ledger,
            settings=EngineSettings(max_concurrency=1, exclude_failed_workers=False),
        )

        engine.create_run(
            run_id="e2e-fail-test",
            payment_rule=PaymentRule.ASK,
            workers=workers,
            tasks=tasks,
        )

        # First attempt fails
        failing_executor = DeterministicExecutor(fail_task_ids={"T1"})
        bidder = DeterministicBidder()

        # Run until task fails twice
        for _ in range(10):
            engine.step(bidder=bidder, executor=failing_executor)
            state = replay_ledger(events=list(ledger.iter_events()))
            if state.tasks["T1"].fail_count >= 2:
                break

        state = replay_ledger(events=list(ledger.iter_events()))
        assert state.tasks["T1"].status == "TODO", "Failed task should be reopened"
        assert state.tasks["T1"].fail_count >= 2
        assert state.tasks["T1"].bounty_current > 50, "Bounty should have bumped"

        # Now let it pass
        passing_executor = DeterministicExecutor()
        for _ in range(10):
            engine.step(bidder=bidder, executor=passing_executor)
            state = replay_ledger(events=list(ledger.iter_events()))
            if state.tasks["T1"].status == "DONE":
                break

        assert state.tasks["T1"].status == "DONE"

    def test_reputation_changes_on_pass_and_fail(self, tmp_path: Path) -> None:
        """Verify reputation increases on pass and decreases on fail."""
        tasks = [
            TaskSpec(
                id="T1", title="Task 1", bounty=30, deps=[], acceptance=[CommandSpec(cmd="true")]
            ),
            TaskSpec(
                id="T2", title="Task 2", bounty=30, deps=[], acceptance=[CommandSpec(cmd="true")]
            ),
        ]
        workers = [
            WorkerRuntime(worker_id="passer", reputation=1.0),
            WorkerRuntime(worker_id="failer", reputation=1.0),
        ]

        ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
        settlement = SettlementPolicy(rep_gain_on_pass=0.06, rep_loss_on_fail=0.2)
        engine = ClearinghouseEngine(
            ledger=ledger,
            settlement=settlement,
            settings=EngineSettings(max_concurrency=2),
        )

        engine.create_run(
            run_id="e2e-rep-test",
            payment_rule=PaymentRule.ASK,
            workers=workers,
            tasks=tasks,
        )

        class TargetedBidder:
            def get_bids(
                self,
                *,
                worker: WorkerRuntime,
                ready_tasks: Sequence[ReadyTask],
                round_id: int,
                discussion_history: Sequence[DiscussionMessage] = (),
            ) -> BidResult:
                _ = round_id, discussion_history
                # passer bids on T1, failer bids on T2
                for rt in ready_tasks:
                    if worker.worker_id == "passer" and rt.spec.id == "T1":
                        return BidResult(
                            bids=[
                                Bid(
                                    task_id="T1", ask=5, self_assessed_p_success=0.9, eta_minutes=10
                                )
                            ]
                        )
                    if worker.worker_id == "failer" and rt.spec.id == "T2":
                        return BidResult(
                            bids=[
                                Bid(
                                    task_id="T2", ask=5, self_assessed_p_success=0.9, eta_minutes=10
                                )
                            ]
                        )
                return BidResult(bids=[])

        class TargetedExecutor:
            def execute(
                self,
                *,
                worker: WorkerRuntime,
                task: TaskSpec,
                bid: Bid,
                round_id: int,
                discussion_history: Sequence[DiscussionMessage] = (),
            ) -> ExecutionOutcome:
                _ = bid, round_id, discussion_history
                if worker.worker_id == "failer":
                    return ExecutionOutcome(status=VerifyStatus.FAIL, notes="fail")
                return ExecutionOutcome(status=VerifyStatus.PASS, notes="pass")

        bidder = TargetedBidder()
        executor = TargetedExecutor()

        for _ in range(10):
            engine.step(bidder=bidder, executor=executor)

        state = replay_ledger(events=list(ledger.iter_events()), settlement=settlement)

        # Passer should have gained reputation
        assert state.workers["passer"].reputation > 1.0

        # Failer should have lost reputation
        assert state.workers["failer"].reputation < 1.0

    def test_ledger_contains_all_event_types(
        self, tmp_path: Path, mini_scenario: list[TaskSpec], workers: list[WorkerRuntime]
    ) -> None:
        """Verify ledger captures the expected event types."""
        ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
        engine = ClearinghouseEngine(ledger=ledger)

        engine.create_run(
            run_id="e2e-events-test",
            payment_rule=PaymentRule.ASK,
            workers=workers,
            tasks=mini_scenario,
        )

        bidder = DeterministicBidder()
        executor = DeterministicExecutor()

        for _ in range(20):
            engine.step(bidder=bidder, executor=executor)
            state = replay_ledger(events=list(ledger.iter_events()))
            if all(t.status == "DONE" for t in state.tasks.values()):
                break

        events = list(ledger.iter_events())
        event_types = {e.type for e in events}

        # Core events should be present
        assert EventType.RUN_CREATED in event_types
        assert EventType.WORKER_REGISTERED in event_types
        assert EventType.TASK_CREATED in event_types
        assert EventType.BID_SUBMITTED in event_types
        assert EventType.TASK_ASSIGNED in event_types
        assert EventType.TASK_COMPLETED in event_types
        assert EventType.PAYMENT_MADE in event_types

    def test_run_state_can_be_serialized(
        self, tmp_path: Path, mini_scenario: list[TaskSpec], workers: list[WorkerRuntime]
    ) -> None:
        """Verify the final state can be serialized to JSON."""
        ledger = HashChainedLedger(tmp_path / "ledger.jsonl")
        engine = ClearinghouseEngine(ledger=ledger)

        engine.create_run(
            run_id="e2e-serialize-test",
            payment_rule=PaymentRule.ASK,
            workers=workers,
            tasks=mini_scenario,
        )

        bidder = DeterministicBidder()
        executor = DeterministicExecutor()

        for _ in range(20):
            engine.step(bidder=bidder, executor=executor)
            state = replay_ledger(events=list(ledger.iter_events()))
            if all(t.status == "DONE" for t in state.tasks.values()):
                break

        state = replay_ledger(events=list(ledger.iter_events()))

        # Should serialize without error
        state_json = state.model_dump_json(indent=2)
        assert len(state_json) > 0

        # Should round-trip
        from agent_economy.schemas import DerivedState

        restored = DerivedState.model_validate_json(state_json)
        assert restored.run_id == state.run_id
        assert len(restored.tasks) == len(state.tasks)
