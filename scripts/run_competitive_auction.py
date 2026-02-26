"""Run competitive multi-model auction on Phase I calibration data.

Reads ``calibration_results.jsonl`` from a Phase I run, filters to
a specified reserve level, and allocates tasks under min-ask and
formula mechanisms.  Outputs per-task allocation results and summary
metrics as JSON.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_economy.research.competitive_auction import (
    oracle_accuracy,
    run_competitive_auction,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run competitive auction on Phase I data")
    parser.add_argument("--phase1-dir", type=Path, required=True)
    parser.add_argument("--reserve", type=float, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSON path (default: <phase1-dir>/competitive_reserve_<R>.json)",
    )
    args = parser.parse_args()

    cal_path = Path(args.phase1_dir) / "calibration_results.jsonl"
    if not cal_path.exists():
        raise SystemExit(f"calibration_results.jsonl not found in {args.phase1_dir}")

    records: list[dict] = []
    with cal_path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    reserve = float(args.reserve)

    filtered = [
        r for r in records
        if r.get("reserve_shown") is not None
        and abs(float(r["reserve_shown"]) - reserve) < 0.001
    ]
    if not filtered:
        print(
            f"[competitive] no records with reserve_shown={reserve}, "
            f"trying all records (reserve_shown values: "
            f"{sorted(set(r.get('reserve_shown') for r in records))})",
            flush=True,
        )
        filtered = records

    print(f"[competitive] {len(filtered)} records for reserve={reserve}", flush=True)

    results = run_competitive_auction(filtered, reserve=reserve)
    oracle = oracle_accuracy(filtered)
    results["oracle"] = oracle

    output = args.output
    if output is None:
        output = Path(args.phase1_dir) / f"competitive_reserve_{reserve}.json"

    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[competitive] output: {output}", flush=True)

    for mech, stats in results.get("summary", {}).items():
        acc = stats.get("allocation_accuracy")
        acc_str = f"{acc:.1%}" if acc is not None else "N/A"
        print(
            f"  {mech}: accuracy={acc_str} "
            f"solved={stats.get('n_solved')}/{stats.get('n_with_outcome')} "
            f"allocated={stats.get('n_allocated')}/{stats.get('n_tasks')}",
            flush=True,
        )
    oracle_acc = oracle.get("oracle_accuracy")
    oracle_str = f"{oracle_acc:.1%}" if oracle_acc is not None else "N/A"
    print(f"  oracle: accuracy={oracle_str} solvable={oracle.get('n_solvable')}/{oracle.get('n_with_outcome')}")


if __name__ == "__main__":
    main()
