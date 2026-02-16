from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import importlib
import json
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

if __package__ in {None, ""}:
    # Allow `python scripts/run_phase1.py` from repo root.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_economy.config import load_settings
from agent_economy.ledger import HashChainedLedger
from agent_economy.main import _llm_router_for_workers
from agent_economy.research.calibration import (
    CalibrationRecord,
    PromptStrategy,
    elicit_calibration,
)
from agent_economy.research.calibration_metrics import summarize_calibration
from agent_economy.research.external_swe_evidence import (
    DEFAULT_EXTERNAL_EVIDENCE_URL,
    build_external_covered_lite_phase1,
)
from agent_economy.research.market_metrics import summarize_market_run
from agent_economy.research.swebench import load_swebench_subset, to_task_spec
from agent_economy.scenario import load_scenario
from agent_economy.schemas import EventType, VerifyStatus, WorkerRuntime, WorkerType


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


def _load_phase1_inputs(
    *,
    task_source: str,
    swe_manifest: Path,
    swe_limit: int,
    synthesis_scenario: Path,
    model_refs: list[str],
    tasks_limit: int,
    external_evidence_url: str,
) -> tuple[list[dict], dict[tuple[str, str, str], dict[str, object]], dict[str, object] | None]:
    if task_source == "external_covered_lite":
        tasks, labels, manifest = build_external_covered_lite_phase1(
            model_refs=model_refs,
            task_limit=int(tasks_limit),
            leaderboard_url=str(external_evidence_url),
        )
        return tasks, labels, manifest

    tasks = _load_phase1_tasks(
        swe_manifest=Path(swe_manifest),
        swe_limit=int(swe_limit),
        synthesis_scenario=Path(synthesis_scenario),
    )
    return tasks, {}, None


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_swebench_phase1_scenario(
    *,
    output_root: Path,
    manifest_path: Path,
    swe_limit: int,
) -> Path | None:
    instances = load_swebench_subset(manifest_path, limit=swe_limit)
    if not instances:
        return None

    first_template = instances[0].template_dir
    template_dir = Path(first_template) if first_template else Path("templates/swebench_semver")

    tasks = [to_task_spec(inst).model_dump(mode="json") for inst in instances]
    payload = {
        "scenario_id": "swebench_phase1_subset",
        "title": f"SWE-bench Phase I subset ({len(tasks)} tasks)",
        "template_dir": str(template_dir),
        "tasks": tasks,
    }
    scenario_path = Path(output_root) / "_generated" / "swebench_phase1_subset.yaml"
    scenario_path.parent.mkdir(parents=True, exist_ok=True)
    scenario_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return scenario_path


def _safe_model_tag(model_ref: str) -> str:
    # Keep run dirs stable and filesystem-safe across providers/model naming.
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(model_ref).strip())


def _first_attempt_outcomes(*, run_dir: Path, task_ids: list[str]) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {
        task_id: {
            "outcome": None,
            "outcome_status": "not_attempted",
            "attempted": False,
        }
        for task_id in task_ids
    }
    ledger_path = Path(run_dir) / "ledger.jsonl"
    if not ledger_path.exists():
        return out

    seen: set[str] = set()
    events = list(HashChainedLedger(ledger_path).iter_events())
    for event in events:
        if event.type != EventType.TASK_COMPLETED:
            continue
        payload = event.payload or {}
        task_id = str(payload.get("task_id") or "")
        if not task_id or task_id not in out or task_id in seen:
            continue
        seen.add(task_id)

        raw_status = str(payload.get("verify_status") or payload.get("status") or "").strip()
        if raw_status:
            try:
                verify_status = VerifyStatus(raw_status)
            except Exception:
                verify_status = None
        else:
            verify_status = None

        if verify_status is None:
            # Fallback for legacy/malformed rows.
            success = bool(payload.get("success", False))
            out[task_id] = {
                "outcome": 1 if success else 0,
                "outcome_status": "pass" if success else "fail",
                "attempted": True,
            }
            continue

        if verify_status == VerifyStatus.PASS:
            out[task_id] = {
                "outcome": 1,
                "outcome_status": "pass",
                "attempted": True,
            }
        elif verify_status == VerifyStatus.FAIL:
            out[task_id] = {
                "outcome": 0,
                "outcome_status": "fail",
                "attempted": True,
            }
        elif verify_status == VerifyStatus.TIMEOUT:
            out[task_id] = {
                "outcome": None,
                "outcome_status": "timeout",
                "attempted": True,
            }
        elif verify_status == VerifyStatus.INFRA:
            out[task_id] = {
                "outcome": None,
                "outcome_status": "infra",
                "attempted": True,
            }
        elif verify_status == VerifyStatus.MANUAL_REVIEW:
            out[task_id] = {
                "outcome": None,
                "outcome_status": "manual_review",
                "attempted": True,
            }
        else:
            out[task_id] = {
                "outcome": None,
                "outcome_status": str(verify_status.value).lower(),
                "attempted": True,
            }

    return out


def _maybe_run_solo(
    *,
    execute: bool,
    output_root: Path,
    models: list[str],
    include_swebench: bool,
    swe_manifest: Path,
    swe_limit: int,
    synthesis_scenario: Path,
    workers_path: Path,
    rounds: int,
    concurrency: int,
    overwrite: bool,
) -> tuple[list[dict], dict[tuple[str, str, str], dict[str, object]]]:
    scenario_map = {"synthesis": Path(synthesis_scenario)}
    if include_swebench:
        swe_scenario = _write_swebench_phase1_scenario(
            output_root=output_root,
            manifest_path=Path(swe_manifest),
            swe_limit=int(swe_limit),
        )
        if swe_scenario is not None:
            scenario_map["swebench"] = swe_scenario

    out_rows: list[dict] = []
    outcomes: dict[tuple[str, str, str], dict[str, object]] = {}

    for benchmark, scenario_path in scenario_map.items():
        scenario = load_scenario(scenario_path)
        for model_ref in models:
            run_name = f"solo_{benchmark}_{_safe_model_tag(model_ref)}"
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
            phase2_module = importlib.import_module("scripts.run_phase2")
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
            try:
                run_one_spec(
                    spec=spec,
                    run_dir=run_dir,
                    workers_path=workers_path,
                    rounds=rounds,
                    concurrency=concurrency,
                )
                summary = summarize_market_run(run_dir=run_dir)
                task_outcomes = _first_attempt_outcomes(
                    run_dir=run_dir, task_ids=[task.id for task in scenario.tasks]
                )
                observed = sum(
                    1 for info in task_outcomes.values() if info.get("outcome") is not None
                )
                censored = len(task_outcomes) - observed
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
                        "phase1_observed_outcomes": observed,
                        "phase1_censored_outcomes": censored,
                    }
                )

                for task_id, info in task_outcomes.items():
                    outcomes[(benchmark, str(task_id), model_ref)] = info
            except Exception as e:
                out_rows.append(
                    {
                        "benchmark": benchmark,
                        "scenario_path": str(scenario_path),
                        "model_ref": model_ref,
                        "run_dir": str(run_dir),
                        "status": "ERROR",
                        "error": f"{type(e).__name__}: {e}",
                    }
                )

    return out_rows, outcomes


def _quality_snapshot(*, rows: list[CalibrationRecord]) -> dict[str, object]:
    count = len(rows)
    if count <= 0:
        return {
            "count": 0,
            "parse_success_rate": 0.0,
            "hard_error_rate": 0.0,
            "missing_estimated_tokens": 0,
            "duplicate_keys": 0,
            "invalid_p_success": 0,
        }

    parse_errors = sum(1 for rec in rows if str(rec.rationale or "").strip().startswith("ERROR:"))
    missing_estimated = sum(1 for rec in rows if rec.estimated_tokens_total is None)
    invalid_p = sum(1 for rec in rows if not (0.0 <= float(rec.p_success) <= 1.0))

    keys = [(rec.model_ref, rec.task_id, rec.strategy.value) for rec in rows]
    duplicate_keys = max(0, len(keys) - len(set(keys)))

    return {
        "count": count,
        "parse_success_rate": float((count - parse_errors) / count),
        "hard_error_rate": float(parse_errors / count),
        "missing_estimated_tokens": missing_estimated,
        "duplicate_keys": duplicate_keys,
        "invalid_p_success": invalid_p,
    }


def _apply_outcomes_to_rows(
    *,
    rows: list[dict],
    outcomes: dict[tuple[str, str, str], dict[str, object]],
) -> list[dict]:
    for row in rows:
        key = (str(row.get("benchmark")), str(row.get("task_id")), str(row.get("model_ref")))
        info = outcomes.get(key)
        if info is None:
            continue
        for field in (
            "outcome",
            "outcome_status",
            "attempted",
            "outcome_source",
            "external_row_name",
            "external_row_date",
            "external_api_calls",
            "external_cost",
        ):
            if field in info:
                row[field] = info.get(field)
        if "outcome_status" not in row:
            row["outcome_status"] = "unknown"
        if "attempted" not in row:
            row["attempted"] = False
    return rows


def _run_calibration(
    *,
    execute_calibration: bool,
    llm: object | None,
    models: list[str],
    tasks: list[dict],
    strategies: list[PromptStrategy],
    calibration_concurrency: int,
    check_every: int = 25,
    quality_checks_path: Path | None = None,
) -> list[CalibrationRecord]:
    total = len(models) * len(tasks) * len(strategies)
    jobs: list[tuple[str, dict, PromptStrategy, int]] = []
    idx = 0
    for model_ref in models:
        for task in tasks:
            for strategy in strategies:
                idx += 1
                jobs.append((model_ref, task, strategy, idx))

    if not execute_calibration or llm is None:
        records: list[CalibrationRecord] = []
        done = 0
        for model_ref, task, strategy, job_idx in jobs:
            print(
                f"[phase1] calibration {job_idx}/{total} "
                f"model={model_ref} task={task['task_id']} strategy={strategy.value}",
                flush=True,
            )
            records.append(
                CalibrationRecord(
                    benchmark=str(task["benchmark"]),
                    task_id=str(task["task_id"]),
                    model_ref=model_ref,
                    strategy=strategy,
                    p_success=0.5,
                    estimated_tokens_total=None,
                    rationale="not executed",
                )
            )
            done += 1
            if check_every > 0 and done % check_every == 0 and quality_checks_path is not None:
                snapshot = _quality_snapshot(rows=records)
                _append_jsonl(
                    quality_checks_path,
                    {
                        "type": "checkpoint",
                        "completed": done,
                        "total": total,
                        **snapshot,
                    },
                )
        if quality_checks_path is not None:
            snapshot = _quality_snapshot(rows=records)
            _append_jsonl(
                quality_checks_path,
                {
                    "type": "final",
                    "completed": len(records),
                    "total": total,
                    **snapshot,
                },
            )
        return records

    def _run_one(model_ref: str, task: dict, strategy: PromptStrategy) -> CalibrationRecord:
        try:
            return elicit_calibration(
                llm=llm,
                model_ref=model_ref,
                benchmark=str(task["benchmark"]),
                task_id=str(task["task_id"]),
                task_title=str(task["title"]),
                task_description=str(task["description"]),
                acceptance_commands=[str(c) for c in list(task.get("acceptance") or [])],
                strategy=strategy,
            )
        except Exception as e:
            return CalibrationRecord(
                benchmark=str(task["benchmark"]),
                task_id=str(task["task_id"]),
                model_ref=model_ref,
                strategy=strategy,
                p_success=0.5,
                estimated_tokens_total=None,
                rationale=f"ERROR: {type(e).__name__}: {e}",
            )

    records_by_key: dict[tuple[str, str, str], CalibrationRecord] = {}
    completed_rows: list[CalibrationRecord] = []
    done = 0
    max_workers = max(1, int(calibration_concurrency))
    if max_workers == 1:
        for model_ref, task, strategy, job_idx in jobs:
            rec = _run_one(model_ref, task, strategy)
            done += 1
            print(
                f"[phase1] calibration {done}/{total} "
                f"model={model_ref} task={task['task_id']} strategy={strategy.value}",
                flush=True,
            )
            key = (model_ref, str(task["task_id"]), strategy.value)
            records_by_key[key] = rec
            completed_rows.append(rec)
            if check_every > 0 and done % check_every == 0 and quality_checks_path is not None:
                snapshot = _quality_snapshot(rows=completed_rows)
                _append_jsonl(
                    quality_checks_path,
                    {
                        "type": "checkpoint",
                        "completed": done,
                        "total": total,
                        **snapshot,
                    },
                )
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_job = {
                pool.submit(_run_one, model_ref, task, strategy): (
                    model_ref,
                    task,
                    strategy,
                    job_idx,
                )
                for model_ref, task, strategy, job_idx in jobs
            }
            for fut in as_completed(future_to_job):
                model_ref, task, strategy, _job_idx = future_to_job[fut]
                rec = fut.result()
                done += 1
                print(
                    f"[phase1] calibration {done}/{total} "
                    f"model={model_ref} task={task['task_id']} strategy={strategy.value}",
                    flush=True,
                )
                key = (model_ref, str(task["task_id"]), strategy.value)
                records_by_key[key] = rec
                completed_rows.append(rec)
                if check_every > 0 and done % check_every == 0 and quality_checks_path is not None:
                    snapshot = _quality_snapshot(rows=completed_rows)
                    _append_jsonl(
                        quality_checks_path,
                        {
                            "type": "checkpoint",
                            "completed": done,
                            "total": total,
                            **snapshot,
                        },
                    )

    out: list[CalibrationRecord] = []
    for model_ref, task, strategy, _job_idx in jobs:
        key = (model_ref, str(task["task_id"]), strategy.value)
        rec = records_by_key.get(key)
        if rec is None:
            rec = CalibrationRecord(
                benchmark=str(task["benchmark"]),
                task_id=str(task["task_id"]),
                model_ref=model_ref,
                strategy=strategy,
                p_success=0.5,
                estimated_tokens_total=None,
                rationale="ERROR: missing calibration result",
            )
        out.append(rec)

    if quality_checks_path is not None:
        snapshot = _quality_snapshot(rows=out)
        _append_jsonl(
            quality_checks_path,
            {
                "type": "final",
                "completed": len(out),
                "total": total,
                **snapshot,
            },
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Phase I calibration + solo baselines")
    parser.add_argument(
        "--models",
        default=(
            "openai:gpt-5.2-2025-12-11,openai:gpt-5.2-pro-2025-12-11,"
            "openai:gpt-5-mini-2025-08-07,anthropic:claude-sonnet-4-5-20250929,"
            "anthropic:claude-opus-4-5-20251101,google:models/gemini-3-pro-preview"
        ),
    )
    parser.add_argument("--strategies", default="direct")
    parser.add_argument(
        "--task-source",
        choices=["manifest_plus_synthesis", "external_covered_lite"],
        default="manifest_plus_synthesis",
    )
    parser.add_argument("--tasks-limit", type=int, default=30)
    parser.add_argument(
        "--external-evidence-url",
        default=DEFAULT_EXTERNAL_EVIDENCE_URL,
    )
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
    parser.add_argument("--check-every", type=int, default=25)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument(
        "--smoke-models",
        default="openai:gpt-5.2-2025-12-11,anthropic:claude-sonnet-4-5-20250929",
    )
    parser.add_argument("--smoke-tasks", type=int, default=6)
    parser.add_argument(
        "--calibration-concurrency",
        type=int,
        default=1,
        help="parallel workers for calibration elicitation",
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("runs/research/phase1") / _now_tag(),
    )
    args = parser.parse_args()

    models = _norm_csv(args.models)
    strategies = _strategy_list(args.strategies)
    tasks, base_outcomes, evidence_manifest = _load_phase1_inputs(
        task_source=str(args.task_source),
        swe_manifest=Path(args.swe_manifest),
        swe_limit=int(args.swe_limit),
        synthesis_scenario=Path(args.synthesis_scenario),
        model_refs=models,
        tasks_limit=int(args.tasks_limit),
        external_evidence_url=str(args.external_evidence_url),
    )
    if args.smoke:
        smoke_models = _norm_csv(args.smoke_models)
        if smoke_models:
            models = [m for m in smoke_models if m in set(models)]
        tasks = list(tasks)[: max(1, int(args.smoke_tasks))]
        if not models:
            raise SystemExit("smoke requested but no smoke models overlap --models")

    args.output_root.mkdir(parents=True, exist_ok=True)
    quality_checks_path = Path(args.output_root) / "quality_checks.jsonl"
    if quality_checks_path.exists():
        quality_checks_path.unlink()

    if tasks:
        _write_jsonl(
            Path(args.output_root) / "selected_tasks.jsonl",
            [
                {
                    "benchmark": t.get("benchmark"),
                    "task_id": t.get("task_id"),
                    "title": t.get("title"),
                    "meta": t.get("meta"),
                }
                for t in tasks
            ],
        )
    if evidence_manifest is not None:
        (Path(args.output_root) / "external_evidence_manifest.json").write_text(
            json.dumps(evidence_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

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

    calibration_records = _run_calibration(
        execute_calibration=bool(args.execute_calibration),
        llm=llm,
        models=models,
        tasks=tasks,
        strategies=strategies,
        calibration_concurrency=int(args.calibration_concurrency),
        check_every=max(0, int(args.check_every)),
        quality_checks_path=quality_checks_path,
    )

    record_rows: list[dict] = []
    for rec in calibration_records:
        row = rec.model_dump(mode="json")
        record_rows.append(row)

    # Checkpoint calibration rows before solo execution.
    _write_jsonl(Path(args.output_root) / "calibration_results.jsonl", record_rows)

    outcomes: dict[tuple[str, str, str], dict[str, object]] = dict(base_outcomes)
    if str(args.task_source) == "external_covered_lite":
        if args.execute_solo:
            print("[phase1] execute-solo ignored for external_covered_lite task source", flush=True)
        solo_rows = [{"status": "SKIPPED", "reason": "external_covered_lite"}]
    else:
        try:
            solo_rows, solo_outcomes = _maybe_run_solo(
                execute=bool(args.execute_solo),
                output_root=Path(args.output_root),
                models=models,
                include_swebench=int(args.swe_limit) > 0,
                swe_manifest=Path(args.swe_manifest),
                swe_limit=int(args.swe_limit),
                synthesis_scenario=Path(args.synthesis_scenario),
                workers_path=Path(args.workers),
                rounds=int(args.rounds),
                concurrency=int(args.concurrency),
                overwrite=bool(args.overwrite),
            )
            outcomes.update(solo_outcomes)
        except Exception as e:
            solo_rows = [{"status": "ERROR", "error": f"{type(e).__name__}: {e}"}]

    _apply_outcomes_to_rows(rows=record_rows, outcomes=outcomes)

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
    print(f"quality_checks={quality_checks_path}")
    print(f"metrics={Path(args.output_root) / 'metrics_summary.json'}")


if __name__ == "__main__":
    main()
