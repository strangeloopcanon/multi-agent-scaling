from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_economy.research.swebench_site_panel import (
    DEFAULT_LEADERBOARDS_URL,
    DEFAULT_TASK_LIST,
    build_site_manifest,
    collect_site_rows,
    load_leaderboards,
    load_task_ids,
    write_site_core_panel,
    write_site_source_panel,
)


DEFAULT_OUTPUT = Path("docs/research/data/phase2/swebench_site_bashonly_model_task_panel.csv")
DEFAULT_SOURCE_OUTPUT = Path(
    "docs/research/data/phase2/swebench_site_bashonly_model_task_panel_sources.csv"
)
DEFAULT_MANIFEST_OUTPUT = Path(
    "docs/research/data/phase2/swebench_site_bashonly_model_task_panel_manifest.json"
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a task-level panel from the SWE-bench website leaderboard data."
    )
    parser.add_argument("--leaderboards-url", default=DEFAULT_LEADERBOARDS_URL)
    parser.add_argument("--leaderboard", default="bash-only")
    parser.add_argument("--task-list", type=Path, default=DEFAULT_TASK_LIST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--sources-output", type=Path, default=DEFAULT_SOURCE_OUTPUT)
    parser.add_argument("--manifest-output", type=Path, default=DEFAULT_MANIFEST_OUTPUT)
    parser.add_argument("--no-sources-output", action="store_true")
    args = parser.parse_args()

    task_ids = load_task_ids(args.task_list)
    payload = load_leaderboards(args.leaderboards_url)
    rows, missing_models = collect_site_rows(
        payload,
        task_ids=task_ids,
        source_url=args.leaderboards_url,
        leaderboard_name=args.leaderboard,
    )

    write_site_core_panel(rows, args.output)
    if not args.no_sources_output:
        write_site_source_panel(rows, args.sources_output)
    _write_json(
        build_site_manifest(
            rows,
            task_ids=task_ids,
            missing_models=missing_models,
            source_url=args.leaderboards_url,
            leaderboard_name=args.leaderboard,
            task_filter_source=args.task_list,
        ),
        args.manifest_output,
    )

    print(f"rows={len(rows)}")
    print(f"models={len({row.model for row in rows})}")
    print(f"passes={sum(1 for row in rows if row.success)}")
    print("token_consumption_available=false")
    print(f"missing_models={len(missing_models)}")
    print(f"output={args.output}")
    if not args.no_sources_output:
        print(f"sources_output={args.sources_output}")
    print(f"manifest_output={args.manifest_output}")


def _write_json(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
