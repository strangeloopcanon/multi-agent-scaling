from __future__ import annotations

from agent_economy.clearing import BidSubmission, choose_assignments, score_bid_breakdown
from agent_economy.schemas import Bid, TaskRuntime, WorkerRuntime


def test_choose_assignments_deterministic_tiebreak() -> None:
    tasks = [
        TaskRuntime(task_id="T1", bounty_current=100, bounty_original=100),
        TaskRuntime(task_id="T2", bounty_current=100, bounty_original=100),
    ]
    workers = [
        WorkerRuntime(worker_id="w1", reputation=1.0),
        WorkerRuntime(worker_id="w2", reputation=1.0),
    ]
    bid = Bid(task_id="T1", ask=10, self_assessed_p_success=1.0, eta_minutes=10)
    bid2 = Bid(task_id="T2", ask=10, self_assessed_p_success=1.0, eta_minutes=10)

    bids_by_task = {
        "T1": [
            BidSubmission(worker_id="w1", bid=bid),
            BidSubmission(worker_id="w2", bid=bid),
        ],
        "T2": [
            BidSubmission(worker_id="w1", bid=bid2),
            BidSubmission(worker_id="w2", bid=bid2),
        ],
    }

    assignments = choose_assignments(
        ready_tasks=tasks, available_workers=workers, bids_by_task=bids_by_task
    )
    assert [(a.task_id, a.worker_id) for a in assignments] == [("T1", "w2"), ("T2", "w1")]


def test_expected_cost_can_change_winner() -> None:
    tasks = [TaskRuntime(task_id="T1", bounty_current=100, bounty_original=100)]
    workers = [
        WorkerRuntime(worker_id="cheap", reputation=1.0),
        WorkerRuntime(worker_id="expensive", reputation=1.0),
    ]
    bid_cheap = Bid(task_id="T1", ask=10, self_assessed_p_success=1.0, eta_minutes=10)
    bid_exp = Bid(task_id="T1", ask=9, self_assessed_p_success=1.0, eta_minutes=10)

    bids_by_task = {
        "T1": [
            BidSubmission(worker_id="cheap", bid=bid_cheap, expected_cost=0.0),
            BidSubmission(worker_id="expensive", bid=bid_exp, expected_cost=5.0),
        ]
    }

    assignments = choose_assignments(
        ready_tasks=tasks, available_workers=workers, bids_by_task=bids_by_task
    )
    assert len(assignments) == 1
    assert assignments[0].worker_id == "cheap"


def test_score_bid_breakdown_contains_components() -> None:
    bid = Bid(task_id="T1", ask=12, self_assessed_p_success=0.8, eta_minutes=15)
    breakdown = score_bid_breakdown(
        bounty=100,
        reputation=1.0,
        bid=bid,
        expected_cost=3.0,
    )
    assert breakdown["bounty"] == 100.0
    assert breakdown["reputation"] == 1.0
    assert breakdown["p_success"] == 0.8
    assert breakdown["ask"] == 12.0
    assert breakdown["expected_cost"] == 3.0
    assert "failure_penalty" in breakdown
    assert "score" in breakdown


def test_choose_assignments_excluded_pairs_filters_candidates() -> None:
    tasks = [TaskRuntime(task_id="T1", bounty_current=100, bounty_original=100)]
    workers = [
        WorkerRuntime(worker_id="w1", reputation=1.0),
        WorkerRuntime(worker_id="w2", reputation=1.0),
    ]
    high_bid = Bid(task_id="T1", ask=5, self_assessed_p_success=0.95, eta_minutes=10)
    low_bid = Bid(task_id="T1", ask=10, self_assessed_p_success=0.80, eta_minutes=10)
    bids_by_task = {
        "T1": [
            BidSubmission(worker_id="w1", bid=high_bid, expected_cost=1.0),
            BidSubmission(worker_id="w2", bid=low_bid, expected_cost=1.0),
        ]
    }

    without_exclusion = choose_assignments(
        ready_tasks=tasks,
        available_workers=workers,
        bids_by_task=bids_by_task,
    )
    assert len(without_exclusion) == 1
    assert without_exclusion[0].worker_id == "w1"

    with_exclusion = choose_assignments(
        ready_tasks=tasks,
        available_workers=workers,
        bids_by_task=bids_by_task,
        excluded_pairs={("T1", "w1")},
    )
    assert len(with_exclusion) == 1
    assert with_exclusion[0].worker_id == "w2"


def test_choose_assignments_excluded_pairs_does_not_affect_other_tasks() -> None:
    tasks = [
        TaskRuntime(task_id="T1", bounty_current=100, bounty_original=100),
        TaskRuntime(task_id="T2", bounty_current=100, bounty_original=100),
    ]
    workers = [
        WorkerRuntime(worker_id="w1", reputation=1.0),
        WorkerRuntime(worker_id="w2", reputation=1.0),
    ]
    bid_t1 = Bid(task_id="T1", ask=10, self_assessed_p_success=0.9, eta_minutes=10)
    bid_t2 = Bid(task_id="T2", ask=10, self_assessed_p_success=0.9, eta_minutes=10)
    bids_by_task = {
        "T1": [BidSubmission(worker_id="w1", bid=bid_t1)],
        "T2": [
            BidSubmission(worker_id="w1", bid=bid_t2),
            BidSubmission(worker_id="w2", bid=bid_t2),
        ],
    }

    assignments = choose_assignments(
        ready_tasks=tasks,
        available_workers=workers,
        bids_by_task=bids_by_task,
        excluded_pairs={("T1", "w1")},
    )
    assigned_tasks = {a.task_id for a in assignments}
    assert "T1" not in assigned_tasks
    assert "T2" in assigned_tasks


def test_choose_assignments_retry_penalty_can_change_winner() -> None:
    tasks = [TaskRuntime(task_id="T1", bounty_current=100, bounty_original=100)]
    workers = [
        WorkerRuntime(worker_id="w1", reputation=1.0),
        WorkerRuntime(worker_id="w2", reputation=1.0),
    ]
    same_bid = Bid(task_id="T1", ask=10, self_assessed_p_success=0.9, eta_minutes=10)
    bids_by_task = {
        "T1": [
            BidSubmission(worker_id="w1", bid=same_bid, expected_cost=1.0),
            BidSubmission(worker_id="w2", bid=same_bid, expected_cost=1.0),
        ]
    }

    assignments = choose_assignments(
        ready_tasks=tasks,
        available_workers=workers,
        bids_by_task=bids_by_task,
        penalty_mode="direct_penalty",
        penalty_fraction=0.1,
        retry_penalty_by_pair={("T1", "w2"): 9.0},
    )
    assert len(assignments) == 1
    assert assignments[0].worker_id == "w1"
    assert assignments[0].score_breakdown is not None
    assert assignments[0].score_breakdown.get("retry_score_penalty") == 0.0
