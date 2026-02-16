from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from agent_economy.research.study_join import join_phase_metrics


def _read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return

    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Join Phase I calibration and Phase II market outcomes"
    )
    parser.add_argument(
        "--phase1-metrics",
        type=Path,
        required=True,
        help="path to Phase I metrics_summary.json",
    )
    parser.add_argument(
        "--phase2-summaries",
        type=Path,
        required=True,
        help="path to Phase II market_run_summaries.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runs/research/comparison"),
    )
    args = parser.parse_args()

    phase1 = _read_json(Path(args.phase1_metrics))
    phase2 = _read_json(Path(args.phase2_summaries))

    joined = join_phase_metrics(
        calibration_summary=phase1,
        market_run_summaries=list(phase2),
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "phase1_phase2_join.json").write_text(
        json.dumps(joined, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_csv(args.output_dir / "phase1_phase2_join.csv", list(joined.get("rows") or []))

    print(f"rows={len(list(joined.get('rows') or []))}")
    print(f"output_json={args.output_dir / 'phase1_phase2_join.json'}")
    print(f"output_csv={args.output_dir / 'phase1_phase2_join.csv'}")


if __name__ == "__main__":
    main()
