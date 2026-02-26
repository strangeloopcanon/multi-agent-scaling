"""Competitive multi-model auction simulation.

Given informed bids from multiple models on the same set of tasks,
allocate each task under different mechanisms and measure allocation
accuracy against ground-truth solve labels.

Mechanisms
----------
* **min_ask** -- lowest eligible ask wins (first-price payment = ask,
  second-price payment = second-lowest ask or reserve).
* **formula** -- highest ``p_success * reserve - ask`` score wins
  (the Phase II allocation rule).
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any


def _finite(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except Exception:
        return default


def allocate_task(
    bids: list[dict[str, Any]],
    *,
    reserve: float,
    mechanism: str,
) -> dict[str, Any] | None:
    """Pick a winner from *bids* for a single task under *mechanism*.

    Each bid dict must have ``model_ref``, ``ask``, ``p_success``, and
    ``outcome`` (0/1/None from external labels).

    Returns a result dict or ``None`` if no eligible bidder.
    """
    eligible = [
        b for b in bids
        if _finite(b.get("ask"), default=float("inf")) <= reserve
        and _finite(b.get("ask"), default=-1) > 0
    ]
    if not eligible:
        return None

    if mechanism == "min_ask":
        eligible.sort(key=lambda b: _finite(b["ask"]))
    elif mechanism == "formula":
        eligible.sort(
            key=lambda b: _finite(b.get("p_success")) * reserve - _finite(b["ask"]),
            reverse=True,
        )
    else:
        raise ValueError(f"unsupported mechanism: {mechanism}")

    winner = eligible[0]
    ask = _finite(winner["ask"])

    second_price = reserve
    if len(eligible) > 1:
        second_price = min(_finite(eligible[1]["ask"]), reserve)

    outcome = winner.get("outcome")
    solved = int(outcome) == 1 if outcome is not None else None

    return {
        "mechanism": mechanism,
        "winner_model": str(winner.get("model_ref", "unknown")),
        "winner_ask": ask,
        "winner_p_success": _finite(winner.get("p_success")),
        "payment_first_price": ask,
        "payment_second_price": second_price,
        "solved": solved,
        "outcome": outcome,
        "n_eligible": len(eligible),
        "n_bidders": len(bids),
    }


def run_competitive_auction(
    records: list[dict[str, Any]],
    *,
    reserve: float,
) -> dict[str, Any]:
    """Run competitive allocation across all tasks for a given reserve level.

    Parameters
    ----------
    records:
        Calibration records filtered to a single ``reserve_shown`` level.
        Each must have ``task_id``, ``model_ref``, ``ask``, ``p_success``,
        and ``outcome``.
    reserve:
        The reserve price (budget cap) for this auction.

    Returns
    -------
    Dict with ``parameters``, ``per_task`` results, and ``summary``
    for each mechanism.
    """
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        by_task[str(rec.get("task_id", ""))].append(rec)

    per_task: list[dict[str, Any]] = []
    for task_id, bids in sorted(by_task.items()):
        row: dict[str, Any] = {"task_id": task_id}
        for mech in ("min_ask", "formula"):
            result = allocate_task(bids, reserve=reserve, mechanism=mech)
            row[mech] = result
        per_task.append(row)

    summary: dict[str, dict[str, Any]] = {}
    for mech in ("min_ask", "formula"):
        allocated = [t[mech] for t in per_task if t.get(mech) is not None]
        n_allocated = len(allocated)
        solved_rows = [a for a in allocated if a.get("solved") is not None]
        n_solved = sum(1 for a in solved_rows if a["solved"])
        n_with_outcome = len(solved_rows)

        accuracy = n_solved / n_with_outcome if n_with_outcome > 0 else None

        total_first = sum(_finite(a["payment_first_price"]) for a in allocated)
        total_second = sum(_finite(a["payment_second_price"]) for a in allocated)
        cost_per_solve_first = total_first / n_solved if n_solved > 0 else None
        cost_per_solve_second = total_second / n_solved if n_solved > 0 else None

        winner_counts: dict[str, int] = defaultdict(int)
        for a in allocated:
            winner_counts[str(a["winner_model"])] += 1

        summary[mech] = {
            "n_tasks": len(per_task),
            "n_allocated": n_allocated,
            "n_with_outcome": n_with_outcome,
            "n_solved": n_solved,
            "allocation_accuracy": accuracy,
            "total_payment_first_price": total_first,
            "total_payment_second_price": total_second,
            "cost_per_solve_first_price": cost_per_solve_first,
            "cost_per_solve_second_price": cost_per_solve_second,
            "winner_distribution": dict(winner_counts),
        }

    return {
        "parameters": {"reserve": reserve},
        "per_task": per_task,
        "summary": summary,
    }


def oracle_accuracy(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute the oracle ceiling: always pick a model that solves the task."""
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        by_task[str(rec.get("task_id", ""))].append(rec)

    n_tasks = len(by_task)
    n_solvable = 0
    n_with_outcome = 0
    for task_id, bids in by_task.items():
        outcomes = [b.get("outcome") for b in bids if b.get("outcome") is not None]
        if outcomes:
            n_with_outcome += 1
            if any(int(o) == 1 for o in outcomes):
                n_solvable += 1

    return {
        "n_tasks": n_tasks,
        "n_with_outcome": n_with_outcome,
        "n_solvable": n_solvable,
        "oracle_accuracy": n_solvable / n_with_outcome if n_with_outcome > 0 else None,
    }
