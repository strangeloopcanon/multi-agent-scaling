from __future__ import annotations

from math import sqrt
from typing import Any


def _pearson(xs: list[float], ys: list[float]) -> float:
    if len(xs) != len(ys) or len(xs) < 2:
        return 0.0
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)

    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0.0 or var_y <= 0.0:
        return 0.0
    return cov / sqrt(var_x * var_y)


def join_phase_metrics(
    *,
    calibration_summary: dict[str, Any],
    market_run_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    calibration_by_model = dict((calibration_summary.get("by_model") or {}))

    market_by_model: dict[str, dict[str, float]] = {}
    for run in market_run_summaries:
        for worker in list(run.get("workers") or []):
            model_ref = str(worker.get("model_ref") or worker.get("worker_id") or "")
            if not model_ref:
                continue
            entry = market_by_model.setdefault(
                model_ref,
                {
                    "wins": 0.0,
                    "completions": 0.0,
                    "penalties": 0.0,
                    "token_total": 0.0,
                    "runs": 0.0,
                },
            )
            usage = worker.get("usage") if isinstance(worker.get("usage"), dict) else {}
            entry["wins"] += float(worker.get("wins") or 0.0)
            entry["completions"] += float(worker.get("completions") or 0.0)
            entry["penalties"] += float(worker.get("penalties") or 0.0)
            entry["token_total"] += float((usage or {}).get("input_tokens") or 0.0) + float(
                (usage or {}).get("output_tokens") or 0.0
            )
            entry["runs"] += 1.0

    joined_rows: list[dict[str, Any]] = []
    for model_ref in sorted(set(calibration_by_model.keys()) | set(market_by_model.keys())):
        c = calibration_by_model.get(model_ref) or {}
        m = market_by_model.get(model_ref) or {}
        runs = float(m.get("runs") or 0.0)
        joined_rows.append(
            {
                "model_ref": model_ref,
                "brier": float(c.get("brier") or 0.0),
                "ece": float(c.get("ece") or 0.0),
                "phase1_accuracy": float(c.get("accuracy") or 0.0),
                "phase2_wins": float(m.get("wins") or 0.0),
                "phase2_completions": float(m.get("completions") or 0.0),
                "phase2_penalties": float(m.get("penalties") or 0.0),
                "phase2_token_total": float(m.get("token_total") or 0.0),
                "phase2_completions_per_run": (
                    float(m.get("completions") or 0.0) / runs if runs > 0 else 0.0
                ),
            }
        )

    brier_vals = [float(r["brier"]) for r in joined_rows]
    completion_vals = [float(r["phase2_completions_per_run"]) for r in joined_rows]
    win_vals = [float(r["phase2_wins"]) for r in joined_rows]
    penalty_vals = [float(r["phase2_penalties"]) for r in joined_rows]

    correlations = {
        "brier_vs_completion_per_run": _pearson(brier_vals, completion_vals),
        "brier_vs_wins": _pearson(brier_vals, win_vals),
        "brier_vs_penalties": _pearson(brier_vals, penalty_vals),
    }

    return {
        "rows": joined_rows,
        "correlations": correlations,
    }
