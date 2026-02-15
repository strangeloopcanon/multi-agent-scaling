from __future__ import annotations

from collections import defaultdict
from typing import Any


def _as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _as_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _valid_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in records:
        outcome = row.get("outcome")
        if outcome is None:
            continue
        p = max(0.0, min(1.0, _as_float(row.get("p_success"), default=0.0)))
        y = 1 if int(outcome) == 1 else 0
        rows.append({**row, "p_success": p, "outcome": y})
    return rows


def brier_score(records: list[dict[str, Any]]) -> float:
    rows = _valid_rows(records)
    if not rows:
        return 0.0
    return sum((r["p_success"] - float(r["outcome"])) ** 2 for r in rows) / len(rows)


def reliability_bins(records: list[dict[str, Any]], *, num_bins: int = 10) -> list[dict[str, Any]]:
    if num_bins <= 0:
        raise ValueError("num_bins must be positive")

    rows = _valid_rows(records)
    if not rows:
        return []

    bins: list[list[dict[str, Any]]] = [[] for _ in range(num_bins)]
    for row in rows:
        p = float(row["p_success"])
        idx = min(num_bins - 1, int(p * num_bins))
        bins[idx].append(row)

    out: list[dict[str, Any]] = []
    for idx, bucket in enumerate(bins):
        low = idx / num_bins
        high = (idx + 1) / num_bins
        if not bucket:
            out.append(
                {
                    "bin": idx,
                    "low": low,
                    "high": high,
                    "count": 0,
                    "predicted_mean": 0.0,
                    "outcome_rate": 0.0,
                }
            )
            continue

        predicted_mean = sum(float(r["p_success"]) for r in bucket) / len(bucket)
        outcome_rate = sum(int(r["outcome"]) for r in bucket) / len(bucket)
        out.append(
            {
                "bin": idx,
                "low": low,
                "high": high,
                "count": len(bucket),
                "predicted_mean": predicted_mean,
                "outcome_rate": outcome_rate,
            }
        )
    return out


def expected_calibration_error(records: list[dict[str, Any]], *, num_bins: int = 10) -> float:
    rows = _valid_rows(records)
    if not rows:
        return 0.0

    bins = reliability_bins(rows, num_bins=num_bins)
    total = len(rows)
    ece = 0.0
    for b in bins:
        count = int(b["count"])
        if count <= 0:
            continue
        ece += (count / total) * abs(float(b["predicted_mean"]) - float(b["outcome_rate"]))
    return ece


def summarize_calibration(records: list[dict[str, Any]], *, num_bins: int = 10) -> dict[str, Any]:
    rows = _valid_rows(records)
    if not rows:
        return {
            "overall": {
                "count": 0,
                "accuracy": 0.0,
                "brier": 0.0,
                "ece": 0.0,
                "mean_input_tokens": 0.0,
                "mean_output_tokens": 0.0,
            },
            "by_model": {},
            "by_model_strategy": {},
        }

    def summarize_subset(subset: list[dict[str, Any]]) -> dict[str, Any]:
        count = len(subset)
        acc = sum(int(r["outcome"]) for r in subset) / count
        return {
            "count": count,
            "accuracy": acc,
            "brier": brier_score(subset),
            "ece": expected_calibration_error(subset, num_bins=num_bins),
            "mean_input_tokens": sum(_as_int(r.get("input_tokens"), default=0) for r in subset)
            / count,
            "mean_output_tokens": sum(_as_int(r.get("output_tokens"), default=0) for r in subset)
            / count,
        }

    by_model_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_model_strategy_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        model = str(row.get("model_ref") or "unknown")
        strategy = str(row.get("strategy") or "unknown")
        by_model_rows[model].append(row)
        by_model_strategy_rows[f"{model}::{strategy}"] += [row]

    by_model = {model: summarize_subset(subset) for model, subset in sorted(by_model_rows.items())}
    by_model_strategy = {
        key: summarize_subset(subset) for key, subset in sorted(by_model_strategy_rows.items())
    }

    return {
        "overall": summarize_subset(rows),
        "by_model": by_model,
        "by_model_strategy": by_model_strategy,
    }
