"""Reserve-price procurement auction simulation.

Converts BidBench calibration data into dollar-valued profit metrics,
directly quantifying the economic cost of model miscalibration.

Mechanism
---------
The model reports ``p_success`` and ``estimated_tokens_total`` for a task.
We compute its **breakeven bid** — the minimum payment at which accepting
the task has non-negative expected value:

    b* = (token_cost + penalty * (1 - p)) / p

For each of *n_draws* random reserve prices drawn from Uniform[0, max_reserve]:

* If b* <= r  →  agent wins, gets paid r.
  Expected profit = (r - b*) * p.
* If b* > r   →  agent loses, profit = 0.

We evaluate profit two ways:

* **Expected** — using the model's reported ``p_success``.
* **Realized** — same bid, but substituting the actual outcome rate.

The gap is the dollar cost of miscalibration.
"""

from __future__ import annotations

import random
from collections import defaultdict
from typing import Any


def _as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def compute_breakeven_bid(
    *,
    p_success: float,
    token_cost: float,
    penalty: float,
) -> float:
    """Return the minimum reserve price at which accepting is non-negative EV.

    breakeven = (token_cost + penalty * (1 - p)) / p

    When ``p_success`` is 0 the task is certain to fail so the breakeven is
    infinite (we return ``float('inf')``).
    """
    p = max(0.0, min(1.0, float(p_success)))
    if p <= 0.0:
        return float("inf")
    return (float(token_cost) + float(penalty) * (1.0 - p)) / p


def simulate_reserve_auction(
    *,
    breakeven_bid: float,
    p_success: float,
    max_reserve: float,
    n_draws: int = 100,
    seed: int = 42,
) -> list[dict[str, float]]:
    """Run *n_draws* independent reserve-price draws for a single row.

    Returns one dict per draw with keys ``reserve``, ``won``, ``profit``.
    """
    rng = random.Random(seed)
    p = max(0.0, min(1.0, float(p_success)))
    results: list[dict[str, float]] = []
    for _ in range(n_draws):
        r = rng.uniform(0.0, float(max_reserve))
        won = float(breakeven_bid) <= r
        profit = (r - float(breakeven_bid)) * p if won else 0.0
        results.append({"reserve": r, "won": 1.0 if won else 0.0, "profit": profit})
    return results


def _row_auction_metrics(
    row: dict[str, Any],
    *,
    price_per_token: float,
    penalty: float,
    max_reserve: float,
    n_draws: int,
    seed: int,
) -> dict[str, Any]:
    """Compute auction metrics for a single calibration record."""
    p_reported = max(0.0, min(1.0, _as_float(row.get("p_success"), default=0.0)))
    estimated_tokens = _as_float(row.get("estimated_tokens_total"), default=0.0)
    token_cost = estimated_tokens * price_per_token

    breakeven = compute_breakeven_bid(
        p_success=p_reported,
        token_cost=token_cost,
        penalty=penalty,
    )

    draws = simulate_reserve_auction(
        breakeven_bid=breakeven,
        p_success=p_reported,
        max_reserve=max_reserve,
        n_draws=n_draws,
        seed=seed,
    )

    win_rate = sum(d["won"] for d in draws) / len(draws) if draws else 0.0
    expected_profit = sum(d["profit"] for d in draws) / len(draws) if draws else 0.0

    # Realized profit: same bid, but use actual outcome rate.
    outcome = row.get("outcome")
    if outcome is not None:
        actual_p = 1.0 if int(outcome) == 1 else 0.0
        realized_profit = (
            sum((d["reserve"] - breakeven) * actual_p for d in draws if d["won"] > 0.0) / len(draws)
            if draws
            else 0.0
        )
    else:
        realized_profit = None

    calibration_cost = (expected_profit - realized_profit) if realized_profit is not None else None

    return {
        "model_ref": str(row.get("model_ref") or "unknown"),
        "strategy": str(row.get("strategy") or "unknown"),
        "task_id": str(row.get("task_id") or ""),
        "benchmark": str(row.get("benchmark") or ""),
        "p_success": p_reported,
        "estimated_tokens_total": estimated_tokens,
        "token_cost": token_cost,
        "breakeven_bid": breakeven if breakeven != float("inf") else None,
        "win_rate": win_rate,
        "expected_profit": expected_profit,
        "realized_profit": realized_profit,
        "calibration_cost": calibration_cost,
        "outcome": outcome,
    }


def _summarize_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate auction metrics over a group of per-row results."""
    count = len(rows)
    if count == 0:
        return {
            "count": 0,
            "mean_win_rate": 0.0,
            "mean_expected_profit": 0.0,
            "mean_realized_profit": None,
            "mean_calibration_cost": None,
            "mean_breakeven_bid": 0.0,
        }

    mean_win = sum(float(r["win_rate"]) for r in rows) / count
    mean_exp = sum(float(r["expected_profit"]) for r in rows) / count

    realized_rows = [r for r in rows if r.get("realized_profit") is not None]
    if realized_rows:
        mean_real = sum(float(r["realized_profit"]) for r in realized_rows) / len(realized_rows)
        mean_cal = sum(float(r["calibration_cost"]) for r in realized_rows) / len(realized_rows)
    else:
        mean_real = None
        mean_cal = None

    bid_rows = [r for r in rows if r.get("breakeven_bid") is not None]
    mean_bid = sum(float(r["breakeven_bid"]) for r in bid_rows) / len(bid_rows) if bid_rows else 0.0

    return {
        "count": count,
        "mean_win_rate": mean_win,
        "mean_expected_profit": mean_exp,
        "mean_realized_profit": mean_real,
        "mean_calibration_cost": mean_cal,
        "mean_breakeven_bid": mean_bid,
    }


def summarize_auction_results(
    records: list[dict[str, Any]],
    *,
    price_per_token: float = 0.00001,
    penalty: float = 1.0,
    max_reserve: float = 10.0,
    n_draws: int = 100,
    seed: int = 42,
) -> dict[str, Any]:
    """Run the reserve-price auction simulation over calibration records.

    Parameters
    ----------
    records:
        List of calibration record dicts (same schema as
        ``calibration_results.jsonl``).  Each dict should have at least
        ``p_success`` and ``estimated_tokens_total``; ``outcome`` (0/1)
        is optional but required for realized-profit metrics.
    price_per_token:
        Dollar cost per token (~$10/1M tokens by default).
    penalty:
        Dollar penalty incurred on task failure.
    max_reserve:
        Upper bound of the Uniform[0, max_reserve] distribution from
        which reserve prices are drawn.
    n_draws:
        Number of independent reserve-price draws per record.
    seed:
        Base random seed.  Each row gets ``seed + row_index`` so draws
        vary across rows but are reproducible.

    Returns
    -------
    dict with keys ``parameters``, ``overall``, ``by_model``,
    ``by_model_strategy``, and ``rows`` (per-record detail).
    """
    per_row: list[dict[str, Any]] = []
    for idx, rec in enumerate(records):
        metrics = _row_auction_metrics(
            rec,
            price_per_token=price_per_token,
            penalty=penalty,
            max_reserve=max_reserve,
            n_draws=n_draws,
            seed=seed + idx,
        )
        per_row.append(metrics)

    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_model_strategy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in per_row:
        model = str(row["model_ref"])
        strategy = str(row["strategy"])
        by_model[model].append(row)
        by_model_strategy[f"{model}::{strategy}"].append(row)

    return {
        "parameters": {
            "price_per_token": price_per_token,
            "penalty": penalty,
            "max_reserve": max_reserve,
            "n_draws": n_draws,
            "seed": seed,
        },
        "overall": _summarize_group(per_row),
        "by_model": {k: _summarize_group(v) for k, v in sorted(by_model.items())},
        "by_model_strategy": {k: _summarize_group(v) for k, v in sorted(by_model_strategy.items())},
        "rows": per_row,
    }
