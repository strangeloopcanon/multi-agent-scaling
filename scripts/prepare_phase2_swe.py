from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from agent_economy.research.swebench import (
    DEFAULT_PHASE2_MANIFEST_PATH,
    load_phase2_manifest,
    load_phase2_task_ids,
    load_swebench_lite_instances_by_id,
    materialize_real_instance_workspace,
    suggest_files_hint,
    write_phase2_instance_scenario,
)
from agent_economy.research.swebench_eval import evaluate_with_harness


def _now_tag() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")


def _slice_ids(instance_ids: list[str], *, offset: int, limit: int) -> list[str]:
    start = max(0, int(offset))
    if int(limit) <= 0:
        return list(instance_ids[start:])
    end = start + max(0, int(limit))
    return list(instance_ids[start:end])


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare real SWE-bench instances for Phase II")
    parser.add_argument(
        "--task-manifest",
        type=Path,
        default=DEFAULT_PHASE2_MANIFEST_PATH,
        help="canonical Phase II task manifest",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("runs/research/phase2/_prepared") / _now_tag(),
    )
    parser.add_argument("--task-offset", type=int, default=0)
    parser.add_argument("--task-limit", type=int, default=0)
    parser.add_argument("--repo-cache-root", type=Path, default=None)
    parser.add_argument("--bounty", type=int, default=90)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--max-files-hint", type=int, default=24)
    parser.add_argument("--timeout-sec", type=int, default=1800)
    parser.add_argument("--check-every", type=int, default=10)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--strict-preflight", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    manifest = load_phase2_manifest(Path(args.task_manifest))
    all_ids = load_phase2_task_ids(Path(args.task_manifest))
    selected_ids = _slice_ids(
        all_ids,
        offset=int(args.task_offset),
        limit=int(args.task_limit),
    )
    if not selected_ids:
        raise SystemExit("no task IDs selected")

    args.output_root.mkdir(parents=True, exist_ok=True)
    repo_cache_root = (
        Path(args.repo_cache_root)
        if args.repo_cache_root is not None
        else (Path(args.output_root) / "_repo_cache")
    )

    instances = load_swebench_lite_instances_by_id(
        instance_ids=selected_ids,
        dataset_name=manifest.dataset_name,
        split=manifest.split,
    )

    prepared_rows: list[dict] = []
    preflight_rows: list[dict] = []
    for idx, instance in enumerate(instances, start=1):
        instance_root = Path(args.output_root) / instance.instance_id
        if instance_root.exists() and args.overwrite:
            shutil.rmtree(instance_root)
        instance_root.mkdir(parents=True, exist_ok=True)

        template_tmp = materialize_real_instance_workspace(
            instance,
            workspace_root=instance_root,
            repo_cache_root=repo_cache_root,
        )
        template_dir = instance_root / "template"
        if template_tmp != template_dir:
            if template_dir.exists():
                shutil.rmtree(template_dir)
            template_tmp.rename(template_dir)

        files_hint = suggest_files_hint(
            instance=instance,
            workspace_dir=template_dir,
            max_files=int(args.max_files_hint),
        )

        scenario_path = instance_root / "scenario.yaml"
        write_phase2_instance_scenario(
            instance=instance,
            scenario_path=scenario_path,
            template_dir=template_dir,
            files_hint=files_hint,
            bounty=int(args.bounty),
            max_attempts=int(args.max_attempts),
            timeout_sec=int(args.timeout_sec),
        )

        preflight = {
            "instance_id": instance.instance_id,
            "completed": None,
            "resolved": None,
            "report_path": None,
            "run_id": None,
            "returncode": None,
            "notes": None,
        }
        if args.preflight:
            result = evaluate_with_harness(
                instance_id=instance.instance_id,
                dataset_name=manifest.dataset_name,
                split=manifest.split,
                timeout_sec=int(args.timeout_sec),
                work_dir=instance_root,
                run_id_prefix="phase2_preflight",
                patch_text=None,
                gold=True,
            )
            preflight = {
                "instance_id": instance.instance_id,
                "completed": bool(result.completed),
                "resolved": bool(result.resolved),
                "report_path": result.report_path,
                "run_id": result.run_id,
                "returncode": int(result.returncode),
                "notes": result.notes,
            }
            preflight_rows.append(preflight)

        row = {
            "instance_id": instance.instance_id,
            "repo": instance.repo,
            "base_commit": instance.base_commit,
            "dataset_name": manifest.dataset_name,
            "split": manifest.split,
            "scenario_path": str(scenario_path.resolve()),
            "template_dir": str(template_dir.resolve()),
            "files_hint": list(files_hint),
            "preflight": preflight,
        }
        prepared_rows.append(row)

        (instance_root / "metadata.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        if int(args.check_every) > 0 and idx % int(args.check_every) == 0:
            print(f"prepared={idx}/{len(instances)}")

    prepared_manifest = {
        "name": "phase2_prepared_manifest_v1",
        "source_manifest": str(Path(args.task_manifest).resolve()),
        "dataset_name": manifest.dataset_name,
        "split": manifest.split,
        "task_offset": int(args.task_offset),
        "task_limit": int(args.task_limit),
        "max_files_hint": int(args.max_files_hint),
        "selected_count": len(prepared_rows),
        "rows": prepared_rows,
    }

    preflight_failed = [
        row
        for row in preflight_rows
        if not (bool(row.get("completed")) and bool(row.get("resolved")))
    ]
    preflight_report = {
        "enabled": bool(args.preflight),
        "strict": bool(args.strict_preflight),
        "total": len(preflight_rows),
        "passed": len(preflight_rows) - len(preflight_failed),
        "failed": len(preflight_failed),
        "failed_instance_ids": [row["instance_id"] for row in preflight_failed],
        "rows": preflight_rows,
    }

    (Path(args.output_root) / "prepared_manifest.json").write_text(
        json.dumps(prepared_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (Path(args.output_root) / "preflight_report.json").write_text(
        json.dumps(preflight_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"prepared_manifest={Path(args.output_root) / 'prepared_manifest.json'}")
    print(f"preflight_report={Path(args.output_root) / 'preflight_report.json'}")

    if args.preflight and args.strict_preflight and preflight_failed:
        raise SystemExit(
            "strict preflight failed for instances: "
            + ", ".join(row["instance_id"] for row in preflight_failed)
        )


if __name__ == "__main__":
    main()
