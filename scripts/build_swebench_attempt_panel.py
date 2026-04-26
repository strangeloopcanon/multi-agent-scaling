from __future__ import annotations

import argparse
from pathlib import Path

from agent_economy.research.swebench_panel import (
    collect_attempt_rows,
    write_core_panel,
    write_source_panel,
)


DEFAULT_OUTPUT = Path("docs/research/data/phase2/swebench_model_task_attempt_panel.csv")
DEFAULT_SOURCE_OUTPUT = Path(
    "docs/research/data/phase2/swebench_model_task_attempt_panel_sources.csv"
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a model-task attempt panel from SWE-bench run ledgers."
    )
    parser.add_argument(
        "--runs-root",
        action="append",
        type=Path,
        default=None,
        help="Run root, task-run directory, or ledger path. Defaults to runs/research/phase2.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--sources-output", type=Path, default=DEFAULT_SOURCE_OUTPUT)
    parser.add_argument("--no-sources-output", action="store_true")
    parser.add_argument("--include-invalid", action="store_true")
    args = parser.parse_args()

    roots = args.runs_root or [Path("runs/research/phase2")]
    rows = collect_attempt_rows(roots, include_invalid=bool(args.include_invalid))
    write_core_panel(rows, args.output)
    if not args.no_sources_output:
        write_source_panel(rows, args.sources_output)

    total_tokens = sum(row.token_consumption for row in rows)
    passes = sum(1 for row in rows if row.success)
    print(f"rows={len(rows)}")
    print(f"passes={passes}")
    print(f"token_consumption={total_tokens}")
    print(f"output={args.output}")
    if not args.no_sources_output:
        print(f"sources_output={args.sources_output}")


if __name__ == "__main__":
    main()
