from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from agent_economy.config import load_settings
from agent_economy.main import _llm_router_for_workers
from agent_economy.research.calibration import (
    CalibrationRecord,
    PromptStrategy,
    elicit_calibration,
)
from agent_economy.research.calibration_metrics import summarize_calibration
from agent_economy.research.market_metrics import summarize_market_run
from agent_economy.research.swebench import load_swebench_subset, to_task_spec
from agent_economy.scenario import load_scenario
from agent_economy.schemas import WorkerRuntime, WorkerType


def _now_tag() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")


def _norm_csv(values: str) -> list[str]:
    return [v.strip() for v in str(values).split(",") if v.strip()]


def _strategy_list(values: str) -> list[PromptStrategy]:
    return [PromptStrategy(v) for v in _norm_csv(values)]


def _load_phase1_tasks(
    *, swe_manifest: Path, swe_limit: int, synthesis_scenario: Path
) -> list[dict]:
    tasks: list[dict] = []

    instances = load_swebench_subset(swe_manifest, limit=swe_limit)
    for inst in instances:
        spec = to_task_spec(inst)
        tasks.append(
            {
                "benchmark": "swebench",
                "task_id": spec.id,
                "title": spec.title,
                "description": spec.description,
                "acceptance": [c.cmd for c in spec.acceptance],
            }
        )

    synthesis = load_scenario(Path(synthesis_scenario))
    for task in synthesis.tasks:
        tasks.append(
            {
                "benchmark": "synthesis",
                "task_id": task.id,
                "title": task.title,
                "description": task.description,
                "acceptance": [c.cmd for c in task.acceptance],
            }
        )

    return tasks


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _maybe_run_solo(
    *,
    execute: bool,
    output_root: Path,
    models: list[str],
    workers_path: Path,
    rounds: int,
    concurrency: int,
    overwrite: bool,
) -> tuple[list[dict], dict[tuple[str, str, str], int]]:
    scenario_map = {
        "swebench": Path("scenarios/swebench_semver_bug.yaml"),
        "synthesis": Path("scenarios/synthesis_reasoning_pilot.yaml"),
    }

    out_rows: list[dict] = []
    outcomes: dict[tuple[str, str, str], int] = {}

    for benchmark, scenario_path in scenario_map.items():
        scenario = load_scenario(scenario_path)
        for model_ref in models:
            run_name = f"solo_{benchmark}_{model_ref.replace(':', '_')}"
            run_dir = output_root / "solo_runs" / run_name

            if not execute:
                out_rows.append(
                    {
                        "benchmark": benchmark,
                        "scenario_path": str(scenario_path),
                        "model_ref": model_ref,
                        "run_dir": str(run_dir),
                        "status": "NOT_RUN",
                    }
                )
                continue

            if run_dir.exists():
                if not overwrite:
                    raise SystemExit(f"solo run dir exists: {run_dir}")
                shutil.rmtree(run_dir)

            # Imported lazily from file path so this works in script execution mode.
            spec_path = Path(__file__).resolve().parent / "run_phase2.py"
            spec_obj = importlib.util.spec_from_file_location("_phase2_runner", spec_path)
            if spec_obj is None or spec_obj.loader is None:
                raise RuntimeError(f"unable to load phase2 runner from {spec_path}")
            phase2_module = importlib.util.module_from_spec(spec_obj)
            spec_obj.loader.exec_module(phase2_module)
            RunSpec = getattr(phase2_module, "RunSpec")
            run_one_spec = getattr(phase2_module, "_run_one_spec")

            spec = RunSpec(
                benchmark=benchmark,
                mode="solo",
                settlement_mode="reputation",
                repeat=1,
                scenario_path=str(scenario_path),
                model_ref=model_ref,
            )
            run_one_spec(
                spec=spec,
                run_dir=run_dir,
                workers_path=workers_path,
                rounds=rounds,
                concurrency=concurrency,
            )
            summary = summarize_market_run(run_dir=run_dir)
            out_rows.append(
                {
                    "benchmark": benchmark,
                    "scenario_path": str(scenario_path),
                    "model_ref": model_ref,
                    "run_dir": str(run_dir),
                    "status": "DONE",
                    "tasks_total": summary.get("tasks_total"),
                    "tasks_done": summary.get("tasks_done"),
                    "pass_rate": summary.get("pass_rate"),
                    "cost_per_pass": summary.get("cost_per_pass"),
                    "tokens_per_pass": summary.get("tokens_per_pass"),
                }
            )

            state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
            task_states = state.get("tasks") if isinstance(state.get("tasks"), dict) else {}
            for task_id, task_payload in task_states.items():
                status = str((task_payload or {}).get("status") or "")
                outcomes[(benchmark, str(task_id), model_ref)] = 1 if status == "DONE" else 0

            # Ensure all scenario tasks are represented, even if absent from state.
            for task in scenario.tasks:
                outcomes.setdefault((benchmark, task.id, model_ref), 0)

    return out_rows, outcomes


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Phase I calibration + solo baselines")
    parser.add_argument(
        "--models",
        default="openai:gpt-5-mini,openai:gpt-5.2,openai:gpt-5.2-pro,openai:gpt-4o,anthropic:claude-sonnet-4-5,google:gemini-2.5-pro",
    )
    parser.add_argument("--strategies", default="direct,anchored,cot")
    parser.add_argument(
        "--swe-manifest",
        type=Path,
        default=Path("benchmarks/swebench/pilot_manifest_v1.json"),
    )
    parser.add_argument("--swe-limit", type=int, default=20)
    parser.add_argument(
        "--synthesis-scenario",
        type=Path,
        default=Path("scenarios/synthesis_reasoning_pilot.yaml"),
    )
    parser.add_argument(
        "--workers",
        type=Path,
        default=Path("benchmarks/workers_phase2_mixed.json"),
        help="worker pool used for optional solo execution",
    )
    parser.add_argument("--rounds", type=int, default=8)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--execute-calibration", action="store_true")
    parser.add_argument("--execute-solo", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("runs/research/phase1") / _now_tag(),
    )
    args = parser.parse_args()

    models = _norm_csv(args.models)
    strategies = _strategy_list(args.strategies)
    tasks = _load_phase1_tasks(
        swe_manifest=Path(args.swe_manifest),
        swe_limit=int(args.swe_limit),
        synthesis_scenario=Path(args.synthesis_scenario),
    )

    args.output_root.mkdir(parents=True, exist_ok=True)

    calibration_records: list[CalibrationRecord] = []

    llm = None
    if args.execute_calibration:
        workers = [
            WorkerRuntime(
                worker_id=f"calib-{idx}",
                worker_type=WorkerType.MODEL_AGENT,
                model_ref=model_ref,
            )
            for idx, model_ref in enumerate(models)
        ]
        llm = _llm_router_for_workers(
            settings=load_settings(),
            workers=workers,
        )

    for model_ref in models:
        for task in tasks:
            for strategy in strategies:
                if args.execute_calibration and llm is not None:
                    record = elicit_calibration(
                        llm=llm,
                        model_ref=model_ref,
                        benchmark=str(task["benchmark"]),
                        task_id=str(task["task_id"]),
                        task_title=str(task["title"]),
                        task_description=str(task["description"]),
                        acceptance_commands=[str(c) for c in list(task.get("acceptance") or [])],
                        strategy=strategy,
                    )
                else:
                    record = CalibrationRecord(
                        benchmark=str(task["benchmark"]),
                        task_id=str(task["task_id"]),
                        model_ref=model_ref,
                        strategy=strategy,
                        p_success=0.5,
                        rationale="not executed",
                    )
                calibration_records.append(record)

    solo_rows, outcomes = _maybe_run_solo(
        execute=bool(args.execute_solo),
        output_root=Path(args.output_root),
        models=models,
        workers_path=Path(args.workers),
        rounds=int(args.rounds),
        concurrency=int(args.concurrency),
        overwrite=bool(args.overwrite),
    )

    record_rows: list[dict] = []
    for rec in calibration_records:
        key = (rec.benchmark, rec.task_id, rec.model_ref)
        row = rec.model_dump(mode="json")
        if key in outcomes:
            row["outcome"] = int(outcomes[key])
        record_rows.append(row)

    _write_jsonl(Path(args.output_root) / "calibration_results.jsonl", record_rows)
    _write_jsonl(Path(args.output_root) / "solo_results.jsonl", solo_rows)

    metrics = summarize_calibration(record_rows)
    (Path(args.output_root) / "metrics_summary.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # reliability bins per model
    from agent_economy.research.calibration_metrics import reliability_bins

    bins_payload = {}
    for model_ref in models:
        model_rows = [r for r in record_rows if r.get("model_ref") == model_ref]
        bins_payload[model_ref] = reliability_bins(model_rows, num_bins=10)

    (Path(args.output_root) / "reliability_bins.json").write_text(
        json.dumps(bins_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"tasks={len(tasks)} models={len(models)} strategies={len(strategies)}")
    print(f"calibration_results={Path(args.output_root) / 'calibration_results.jsonl'}")
    print(f"solo_results={Path(args.output_root) / 'solo_results.jsonl'}")
    print(f"metrics={Path(args.output_root) / 'metrics_summary.json'}")


if __name__ == "__main__":
    main()
