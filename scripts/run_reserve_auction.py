"""Run the reserve-price procurement auction simulation on Phase I results.

Reads ``calibration_results.jsonl`` from a Phase I output directory,
runs the auction simulation, and writes results alongside the Phase I
outputs.

Usage::

    python scripts/run_reserve_auction.py --phase1-dir runs/research/phase1_mini_test
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_economy.research.reserve_auction import summarize_auction_results
from agent_economy.research.reserve_auction import VALID_TREATMENTS
from agent_economy.research.reserve_auction import TREATMENT_LEGACY_PAY_RESERVE


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run reserve-price procurement auction on Phase I calibration results"
    )
    parser.add_argument(
        "--phase1-dir",
        type=Path,
        required=True,
        help="path to Phase I output directory containing calibration_results.jsonl",
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
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="output path (default: <phase1-dir>/reserve_auction_results.json)",
    )
    args = parser.parse_args()

    calibration_path = Path(args.phase1_dir) / "calibration_results.jsonl"
    if not calibration_path.exists():
        raise SystemExit(f"calibration results not found: {calibration_path}")

    records = _read_jsonl(calibration_path)
    if not records:
        raise SystemExit(f"no records in {calibration_path}")

    results = summarize_auction_results(
        records,
        price_per_token=float(args.price_per_token),
        penalty=float(args.penalty),
        max_reserve=float(args.max_reserve),
        n_draws=int(args.n_draws),
        seed=int(args.seed),
        treatment=str(args.treatment),
    )

    output_path = args.output or (Path(args.phase1_dir) / "reserve_auction_results.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    overall = results.get("overall") or {}
    print(f"records={len(records)}")
    print(f"treatment={args.treatment}")
    print(f"mean_win_rate={overall.get('mean_win_rate', 0.0):.4f}")
    print(f"mean_expected_profit=${overall.get('mean_expected_profit', 0.0):.4f}")
    realized = overall.get("mean_realized_profit")
    if realized is not None:
        print(f"mean_realized_profit=${realized:.4f}")
        cal_cost = overall.get("mean_calibration_cost")
        if cal_cost is not None:
            print(f"mean_calibration_cost=${cal_cost:.4f}")
    else:
        print("mean_realized_profit=N/A (no outcome data)")
    print(f"output={output_path}")


if __name__ == "__main__":
    main()
