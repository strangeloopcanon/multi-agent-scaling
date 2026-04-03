"""Run reserve auction simulation on a calibration JSONL file.

This is a standalone variant that defaults to Phase I research data paths and
writes a separate JSON output artifact.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_economy.research.reserve_auction import TREATMENT_LEGACY_PAY_RESERVE
from agent_economy.research.reserve_auction import VALID_TREATMENTS
from agent_economy.research.reserve_auction import _row_auction_metrics
from agent_economy.research.reserve_auction import _summarize_group
from agent_economy.research.reserve_auction import summarize_auction_results


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _default_output_path(input_path: Path) -> Path:
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    return input_path.parent / f"reserve_auction_results_{ts}.json"


def _as_float(value: object, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _load_pricing_csv(path: Path) -> dict[str, float]:
    if not path.exists():
        raise SystemExit(f"pricing csv not found: {path}")
    out: dict[str, float] = {}
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        required = {"model_ref", "blended_price_per_token"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"pricing csv missing columns: {sorted(missing)} in {path}")
        for row in reader:
            model_ref = str(row.get("model_ref") or "").strip()
            if not model_ref:
                continue
            price = _as_float(row.get("blended_price_per_token"), default=0.0)
            if price <= 0.0:
                continue
            out[model_ref] = price
    return out


def _summarize_with_model_pricing(
    records: list[dict],
    *,
    pricing_by_model: dict[str, float],
    fallback_price_per_token: float,
    penalty: float,
    max_reserve: float,
    n_draws: int,
    seed: int,
    treatment: str,
) -> dict[str, object]:
    per_row: list[dict] = []
    for idx, rec in enumerate(records):
        model_ref = str(rec.get("model_ref") or "")
        row_price = pricing_by_model.get(model_ref, float(fallback_price_per_token))
        metrics = _row_auction_metrics(
            rec,
            price_per_token=float(row_price),
            penalty=penalty,
            max_reserve=max_reserve,
            n_draws=n_draws,
            seed=seed + idx,
            treatment=treatment,
        )
        metrics["price_per_token_used"] = float(row_price)
        per_row.append(metrics)

    by_model: dict[str, list[dict]] = {}
    by_model_strategy: dict[str, list[dict]] = {}
    for row in per_row:
        model = str(row.get("model_ref") or "unknown")
        strategy = str(row.get("strategy") or "unknown")
        by_model.setdefault(model, []).append(row)
        by_model_strategy.setdefault(f"{model}::{strategy}", []).append(row)

    return {
        "parameters": {
            "price_per_token": float(fallback_price_per_token),
            "penalty": penalty,
            "max_reserve": max_reserve,
            "n_draws": n_draws,
            "seed": seed,
            "treatment": treatment,
            "pricing_mode": "per_model_csv",
        },
        "overall": _summarize_group(per_row),
        "by_model": {k: _summarize_group(v) for k, v in sorted(by_model.items())},
        "by_model_strategy": {k: _summarize_group(v) for k, v in sorted(by_model_strategy.items())},
        "rows": per_row,
    }


def _perfect_knowledge_oracle_summary(
    records: list[dict],
    *,
    pricing_by_model: dict[str, float] | None,
    fallback_price_per_token: float,
    max_reserve: float,
    n_draws: int,
    seed: int,
) -> dict[str, object]:
    """Upper bound if each model knows exactly which tasks it can solve.

    Assumptions:
    - If outcome == 1, model knows it can solve and bids at breakeven for p=1:
      breakeven = token_cost.
    - If outcome == 0, model abstains (never wins, zero profit).
    - Reserve remains Uniform[0, max_reserve], with n_draws and deterministic seeds.
    """
    rows: list[dict[str, object]] = []
    for idx, rec in enumerate(records):
        model_ref = str(rec.get("model_ref") or "unknown")
        price = (
            pricing_by_model.get(model_ref, float(fallback_price_per_token))
            if pricing_by_model is not None
            else float(fallback_price_per_token)
        )
        outcome_raw = rec.get("outcome")
        outcome = int(outcome_raw) if outcome_raw is not None else None
        est_tokens = _as_float(rec.get("estimated_tokens_total"), default=0.0)
        token_cost = est_tokens * float(price)

        rng = random.Random(seed + idx)
        if outcome == 1:
            breakeven = token_cost
            wins = 0.0
            profit_sum = 0.0
            for _ in range(int(n_draws)):
                r = rng.uniform(0.0, float(max_reserve))
                if breakeven <= r:
                    wins += 1.0
                    profit_sum += r - breakeven
            win_rate = wins / float(n_draws)
            mean_profit = profit_sum / float(n_draws)
        else:
            # Perfect knowledge chooses not to take unsolved tasks.
            breakeven = None
            win_rate = 0.0
            mean_profit = 0.0

        rows.append(
            {
                "model_ref": model_ref,
                "task_id": str(rec.get("task_id") or ""),
                "outcome": outcome,
                "oracle_win_rate": win_rate,
                "oracle_profit": mean_profit,
                "oracle_breakeven_bid": breakeven,
            }
        )

    def _agg(subset: list[dict[str, object]]) -> dict[str, float | int]:
        if not subset:
            return {
                "count": 0,
                "mean_oracle_win_rate": 0.0,
                "mean_oracle_profit": 0.0,
                "mean_oracle_breakeven_bid": 0.0,
            }
        count = len(subset)
        mean_win = sum(float(r["oracle_win_rate"]) for r in subset) / count
        mean_profit = sum(float(r["oracle_profit"]) for r in subset) / count
        bid_rows = [
            float(r["oracle_breakeven_bid"])
            for r in subset
            if r["oracle_breakeven_bid"] is not None
        ]
        mean_bid = (sum(bid_rows) / len(bid_rows)) if bid_rows else 0.0
        return {
            "count": count,
            "mean_oracle_win_rate": mean_win,
            "mean_oracle_profit": mean_profit,
            "mean_oracle_breakeven_bid": mean_bid,
        }

    by_model: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        by_model.setdefault(str(row["model_ref"]), []).append(row)

    return {
        "assumption": "perfect task solvability knowledge (abstain on unsolved tasks)",
        "overall": _agg(rows),
        "by_model": {k: _agg(v) for k, v in sorted(by_model.items())},
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run reserve-price auction from a calibration_results.jsonl file"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("docs/research/data/phase1/calibration_results.jsonl"),
        help="Path to calibration_results.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Output JSON path. Default: timestamped file next to input "
            "(reserve_auction_results_<ts>.json)."
        ),
    )
    parser.add_argument(
        "--pricing-csv",
        type=Path,
        default=Path("docs/research/data/phase1/model_token_pricing.csv"),
        help=(
            "Optional per-model pricing CSV with columns model_ref,blended_price_per_token. "
            "If present, each row uses that model's price per token."
        ),
    )
    parser.add_argument("--price-per-token", type=float, default=0.00001)
    parser.add_argument("--penalty", type=float, default=1.0)
    parser.add_argument("--max-reserve", type=float, default=10.0)
    parser.add_argument("--n-draws", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--treatment",
        choices=sorted(VALID_TREATMENTS),
        default=TREATMENT_LEGACY_PAY_RESERVE,
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"input calibration file not found: {input_path}")
    if float(args.price_per_token) < 0.0:
        raise SystemExit("--price-per-token must be >= 0")
    if float(args.penalty) < 0.0:
        raise SystemExit("--penalty must be >= 0")

    records = _read_jsonl(input_path)
    if not records:
        raise SystemExit(f"no records in {input_path}")

    pricing_csv_path = Path(args.pricing_csv)
    pricing_by_model: dict[str, float] | None = None
    if pricing_csv_path.exists():
        pricing_by_model = _load_pricing_csv(pricing_csv_path)
        results = _summarize_with_model_pricing(
            records,
            pricing_by_model=pricing_by_model,
            fallback_price_per_token=float(args.price_per_token),
            penalty=float(args.penalty),
            max_reserve=float(args.max_reserve),
            n_draws=int(args.n_draws),
            seed=int(args.seed),
            treatment=str(args.treatment),
        )
        pricing_mode = "per_model_csv"
    else:
        results = summarize_auction_results(
            records,
            price_per_token=float(args.price_per_token),
            penalty=float(args.penalty),
            max_reserve=float(args.max_reserve),
            n_draws=int(args.n_draws),
            seed=int(args.seed),
            treatment=str(args.treatment),
        )
        pricing_mode = "single_price_per_token"

    oracle = _perfect_knowledge_oracle_summary(
        records,
        pricing_by_model=pricing_by_model,
        fallback_price_per_token=float(args.price_per_token),
        max_reserve=float(args.max_reserve),
        n_draws=int(args.n_draws),
        seed=int(args.seed),
    )
    results["perfect_knowledge_oracle"] = oracle
    payload = {
        "meta": {
            "generated_at_utc": datetime.now(tz=UTC).isoformat(),
            "input": str(input_path),
            "price_per_token": float(args.price_per_token),
            "penalty": float(args.penalty),
            "max_reserve": float(args.max_reserve),
            "n_draws": int(args.n_draws),
            "seed": int(args.seed),
            "treatment": str(args.treatment),
            "records": len(records),
            "pricing_mode": pricing_mode,
            "pricing_csv": str(pricing_csv_path),
        },
        "results": results,
    }

    output_path = Path(args.output) if args.output is not None else _default_output_path(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    overall = (results.get("overall") or {}) if isinstance(results, dict) else {}
    oracle_overall = (
        ((results.get("perfect_knowledge_oracle") or {}).get("overall") or {})
        if isinstance(results, dict)
        else {}
    )
    print(f"records={len(records)}")
    print(f"treatment={args.treatment}")
    print(f"mean_win_rate={overall.get('mean_win_rate', 0.0):.4f}")
    print(f"mean_expected_profit=${overall.get('mean_expected_profit', 0.0):.4f}")
    realized = overall.get("mean_realized_profit")
    if realized is not None:
        print(f"mean_realized_profit=${realized:.4f}")
    print(f"oracle_mean_profit=${float(oracle_overall.get('mean_oracle_profit', 0.0)):.4f}")
    print(f"output={output_path}")


if __name__ == "__main__":
    main()
