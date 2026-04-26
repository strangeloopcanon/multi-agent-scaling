from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_economy.research.swebench_panel import (
    build_published_manifest,
    collect_attempt_rows,
    collect_published_attempt_rows,
    write_core_panel,
    write_published_core_panel,
    write_published_source_panel,
    write_source_panel,
)


DEFAULT_OUTPUT = Path("docs/research/data/phase2/swebench_model_task_attempt_panel.csv")
DEFAULT_SOURCE_OUTPUT = Path(
    "docs/research/data/phase2/swebench_model_task_attempt_panel_sources.csv"
)
DEFAULT_PUBLISHED_OUTPUT = Path(
    "docs/research/data/phase2/swebench_model_task_attempt_panel_published.csv"
)
DEFAULT_PUBLISHED_SOURCE_OUTPUT = Path(
    "docs/research/data/phase2/swebench_model_task_attempt_panel_published_sources.csv"
)
DEFAULT_PUBLISHED_MANIFEST_OUTPUT = Path(
    "docs/research/data/phase2/swebench_model_task_attempt_panel_published_manifest.json"
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
    parser.add_argument("--manifest-output", type=Path, default=None)
    parser.add_argument("--no-sources-output", action="store_true")
    parser.add_argument("--include-invalid", action="store_true")
    parser.add_argument(
        "--published-only",
        action="store_true",
        help="Build only report-backed published-result rows, excluding local smoke/diagnostic runs.",
    )
    args = parser.parse_args()

    if args.published_only:
        _build_published_only(args)
    else:
        _build_all_ledgers(args)


def _build_all_ledgers(args: argparse.Namespace) -> None:
    roots = args.runs_root or [Path("runs/research/phase2")]
    rows = collect_attempt_rows(roots, include_invalid=bool(args.include_invalid))
    output = args.output
    sources_output = args.sources_output
    write_core_panel(rows, output)
    if not args.no_sources_output:
        write_source_panel(rows, sources_output)

    total_tokens = sum(row.token_consumption for row in rows)
    passes = sum(1 for row in rows if row.success)
    print(f"rows={len(rows)}")
    print(f"passes={passes}")
    print(f"token_consumption={total_tokens}")
    print(f"output={output}")
    if not args.no_sources_output:
        print(f"sources_output={sources_output}")


def _build_published_only(args: argparse.Namespace) -> None:
    output = _published_default(args.output, DEFAULT_OUTPUT, DEFAULT_PUBLISHED_OUTPUT)
    sources_output = _published_default(
        args.sources_output,
        DEFAULT_SOURCE_OUTPUT,
        DEFAULT_PUBLISHED_SOURCE_OUTPUT,
    )
    manifest_output = args.manifest_output or DEFAULT_PUBLISHED_MANIFEST_OUTPUT
    runs_root = args.runs_root[0] if args.runs_root else Path("runs/research/phase2")
    if args.runs_root and len(args.runs_root) > 1:
        raise SystemExit("--published-only accepts at most one --runs-root")

    rows = collect_published_attempt_rows(runs_root=runs_root)
    write_published_core_panel(rows, output)
    if not args.no_sources_output:
        write_published_source_panel(rows, sources_output)
    _write_manifest(build_published_manifest(rows), manifest_output)

    total_tokens = sum(row.attempt.token_consumption for row in rows)
    passes = sum(1 for row in rows if row.attempt.success)
    print(f"rows={len(rows)}")
    print(f"passes={passes}")
    print(f"token_consumption={total_tokens}")
    print(f"output={output}")
    if not args.no_sources_output:
        print(f"sources_output={sources_output}")
    print(f"manifest_output={manifest_output}")


def _published_default(path: Path, all_default: Path, published_default: Path) -> Path:
    if path == all_default:
        return published_default
    return path


def _write_manifest(manifest: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
