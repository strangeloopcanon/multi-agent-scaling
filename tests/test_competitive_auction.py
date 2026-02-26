from __future__ import annotations

from agent_economy.research.competitive_auction import (
    allocate_task,
    oracle_accuracy,
    run_competitive_auction,
)


def _bid(model: str, ask: float, p: float, outcome: int | None = None) -> dict:
    return {
        "model_ref": model,
        "ask": ask,
        "p_success": p,
        "outcome": outcome,
        "task_id": "T1",
    }


def test_min_ask_picks_lowest_ask() -> None:
    bids = [_bid("A", 3.0, 0.5, 1), _bid("B", 2.0, 0.9, 0), _bid("C", 4.0, 0.8, 1)]
    result = allocate_task(bids, reserve=5.0, mechanism="min_ask")
    assert result is not None
    assert result["winner_model"] == "B"
    assert result["winner_ask"] == 2.0
    assert result["payment_first_price"] == 2.0
    assert result["payment_second_price"] == 3.0


def test_formula_picks_highest_score() -> None:
    bids = [_bid("A", 3.0, 0.5, 1), _bid("B", 2.0, 0.3, 0), _bid("C", 4.0, 0.95, 1)]
    result = allocate_task(bids, reserve=5.0, mechanism="formula")
    assert result is not None
    # C: 0.95*5 - 4 = 0.75;  A: 0.5*5 - 3 = -0.5;  B: 0.3*5 - 2 = -0.5
    assert result["winner_model"] == "C"


def test_no_eligible_returns_none() -> None:
    bids = [_bid("A", 6.0, 0.9, 1)]
    result = allocate_task(bids, reserve=5.0, mechanism="min_ask")
    assert result is None


def test_second_price_falls_back_to_reserve_with_single_bidder() -> None:
    bids = [_bid("A", 3.0, 0.9, 1), _bid("B", 6.0, 0.5, 0)]
    result = allocate_task(bids, reserve=5.0, mechanism="min_ask")
    assert result is not None
    assert result["winner_model"] == "A"
    assert result["payment_second_price"] == 5.0


def test_solved_flag_from_outcome() -> None:
    result = allocate_task([_bid("A", 2.0, 0.9, 1)], reserve=5.0, mechanism="min_ask")
    assert result is not None
    assert result["solved"] is True

    result = allocate_task([_bid("A", 2.0, 0.9, 0)], reserve=5.0, mechanism="min_ask")
    assert result is not None
    assert result["solved"] is False

    result = allocate_task([_bid("A", 2.0, 0.9, None)], reserve=5.0, mechanism="min_ask")
    assert result is not None
    assert result["solved"] is None


def test_run_competitive_auction_multi_task() -> None:
    records = [
        {"task_id": "T1", "model_ref": "A", "ask": 2.0, "p_success": 0.8, "outcome": 1},
        {"task_id": "T1", "model_ref": "B", "ask": 3.0, "p_success": 0.9, "outcome": 0},
        {"task_id": "T2", "model_ref": "A", "ask": 4.0, "p_success": 0.5, "outcome": 0},
        {"task_id": "T2", "model_ref": "B", "ask": 1.0, "p_success": 0.6, "outcome": 1},
    ]
    result = run_competitive_auction(records, reserve=5.0)

    assert len(result["per_task"]) == 2
    s = result["summary"]

    # min_ask: T1->A(solve), T2->B(solve) => 2/2
    assert s["min_ask"]["n_solved"] == 2
    assert s["min_ask"]["allocation_accuracy"] == 1.0

    # formula: T1 scores A=0.8*5-2=2, B=0.9*5-3=1.5 => A wins (solves)
    #          T2 scores A=0.5*5-4=-1.5, B=0.6*5-1=2 => B wins (solves)
    assert s["formula"]["n_solved"] == 2


def test_oracle_accuracy_computation() -> None:
    records = [
        {"task_id": "T1", "model_ref": "A", "outcome": 0},
        {"task_id": "T1", "model_ref": "B", "outcome": 1},
        {"task_id": "T2", "model_ref": "A", "outcome": 0},
        {"task_id": "T2", "model_ref": "B", "outcome": 0},
        {"task_id": "T3", "model_ref": "A", "outcome": 1},
        {"task_id": "T3", "model_ref": "B", "outcome": 1},
    ]
    result = oracle_accuracy(records)
    assert result["n_tasks"] == 3
    assert result["n_solvable"] == 2  # T1 and T3
    assert result["oracle_accuracy"] == 2 / 3


def test_strategy_list_accepts_informed_bid() -> None:
    from scripts.run_phase1 import _strategy_list
    from agent_economy.research.calibration import PromptStrategy

    strategies = _strategy_list("informed_bid")
    assert strategies == [PromptStrategy.INFORMED_BID]


def test_run_calibration_with_reserves(tmp_path) -> None:
    from scripts.run_phase1 import _run_calibration
    from agent_economy.research.calibration import PromptStrategy

    tasks = [
        {
            "benchmark": "swebench",
            "task_id": "T1",
            "title": "t1",
            "description": "d1",
            "acceptance": [],
        },
    ]
    records = _run_calibration(
        execute_calibration=False,
        llm=None,
        models=["model-a"],
        tasks=tasks,
        strategies=[PromptStrategy.INFORMED_BID],
        reserves=[5.0, 10.0],
        calibration_concurrency=1,
        check_every=100,
        quality_checks_path=None,
    )
    assert len(records) == 2
    assert records[0].reserve_shown == 5.0
    assert records[1].reserve_shown == 10.0
