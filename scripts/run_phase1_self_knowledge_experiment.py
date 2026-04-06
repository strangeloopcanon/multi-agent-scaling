from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_economy.config import load_settings
from agent_economy.main import _llm_router_for_workers
from agent_economy.research.calibration import (
    CalibrationResponse,
    PromptStrategy,
    build_calibration_prompt,
)
from agent_economy.research.calibration_metrics import summarize_calibration
from agent_economy.research.external_swe_evidence import (
    _build_acceptance_hints,
    _load_swebench_lite_rows,
)
from agent_economy.schemas import WorkerRuntime, WorkerType


EXPERIMENT_STRATEGY = "self_knowledge_direct"
SYSTEM_PROMPT = "You are a calibration evaluator. Output strict JSON only."


def _now_tag() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_pricing_csv(path: Path) -> dict[str, float]:
    rows = _read_jsonl(path) if path.suffix == ".jsonl" else None
    if rows is not None:
        raise ValueError("pricing path must be a CSV, not JSONL")
    pricing: dict[str, float] = {}
    with path.open(encoding="utf-8") as f:
        header = [col.strip() for col in f.readline().strip().split(",")]
        for line in f:
            raw = [cell.strip() for cell in line.rstrip("\n").split(",")]
            row = dict(zip(header, raw))
            model_ref = str(row.get("model_ref") or "").strip()
            blended = row.get("blended_price_per_token")
            if not model_ref or blended is None:
                continue
            try:
                price = float(blended)
            except Exception:
                continue
            if price <= 0.0:
                continue
            pricing[model_ref] = price
    return pricing


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _actual_tokens_total(
    row: dict[str, Any], *, pricing_by_model: dict[str, float]
) -> float | None:
    explicit = _safe_float(row.get("external_actual_tokens_total"), default=0.0)
    if explicit > 0.0:
        return explicit

    model_ref = str(row.get("model_ref") or "")
    blended = pricing_by_model.get(model_ref, 0.0)
    if blended <= 0.0:
        return None

    cost = _safe_float(row.get("external_cost"), default=0.0)
    if cost <= 0.0:
        return None
    return cost / blended


def _first_seen_values(rows: list[dict[str, Any]], key: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        value = str(row.get(key) or "")
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _baseline_rows(path: Path) -> list[dict[str, Any]]:
    rows = _read_jsonl(path)
    out: list[dict[str, Any]] = []
    for row in rows:
        strategy = str(row.get("strategy") or "")
        if strategy != "direct":
            continue
        if row.get("outcome") is None:
            continue
        out.append(row)
    return out


def _build_task_specs(task_ids: list[str]) -> dict[str, dict[str, Any]]:
    lite_rows = _load_swebench_lite_rows()
    tasks: dict[str, dict[str, Any]] = {}
    for task_id in task_ids:
        row = lite_rows.get(task_id)
        if row is None:
            raise ValueError(f"task missing from SWE-bench Lite rows: {task_id}")
        tasks[task_id] = {
            "benchmark": "swebench",
            "task_id": task_id,
            "title": f"SWE-bench fix: {task_id}",
            "repo": str(row.get("repo") or ""),
            "description": "\n".join(
                [
                    "You are fixing a SWE-bench Lite issue.",
                    f"Repository: {row.get('repo') or ''}",
                    f"Base commit: {row.get('base_commit') or ''}",
                    "",
                    str(row.get("problem_statement") or "").strip(),
                ]
            ).strip(),
            "acceptance": _build_acceptance_hints(
                fail_to_pass=list(row.get("fail_to_pass") or []),
                pass_to_pass=list(row.get("pass_to_pass") or []),
            ),
        }
    return tasks


def _format_pct(value: float) -> str:
    return f"{100.0 * value:.1f}%"


def _format_multiplier(value: float) -> str:
    if value >= 10.0:
        return f"{value:.0f}x"
    return f"{value:.1f}x"


def _calibration_phrase(mean_p: float, pass_rate: float) -> str:
    gap = mean_p - pass_rate
    if abs(gap) < 0.03:
        return "historically close to calibrated"
    if gap > 0:
        return f"historically overconfident by {_format_pct(abs(gap))}"
    return f"historically underconfident by {_format_pct(abs(gap))}"


def _build_self_knowledge_card(
    *,
    model_ref: str,
    task_id: str,
    task_repo: str,
    baseline_by_model: dict[str, list[dict[str, Any]]],
    task_repo_by_id: dict[str, str],
    pricing_by_model: dict[str, float],
) -> str:
    held_out = [row for row in baseline_by_model[model_ref] if str(row.get("task_id")) != task_id]
    if not held_out:
        return (
            "Historical self-knowledge summary for this model is unavailable. "
            "Answer using only the current task details."
        )

    mean_p = sum(_safe_float(row.get("p_success"), default=0.5) for row in held_out) / len(held_out)
    pass_rate = sum(_safe_int(row.get("outcome"), default=0) for row in held_out) / len(held_out)

    token_multipliers: list[float] = []
    for row in held_out:
        est = _safe_float(row.get("estimated_tokens_total"), default=0.0)
        actual = _actual_tokens_total(row, pricing_by_model=pricing_by_model)
        if est <= 0.0 or actual is None or actual <= 0.0:
            continue
        token_multipliers.append(actual / est)

    repo_rows = [
        row for row in held_out if task_repo_by_id.get(str(row.get("task_id") or "")) == task_repo
    ]
    repo_note = None
    if len(repo_rows) >= 5:
        repo_mean_p = sum(
            _safe_float(row.get("p_success"), default=0.5) for row in repo_rows
        ) / len(repo_rows)
        repo_pass_rate = sum(_safe_int(row.get("outcome"), default=0) for row in repo_rows) / len(
            repo_rows
        )
        repo_note = (
            f"- On prior tasks from {task_repo} ({len(repo_rows)} held-out tasks), "
            f"your pass rate was {_format_pct(repo_pass_rate)} and your mean stated success "
            f"probability was {_format_pct(repo_mean_p)}."
        )

    lines = [
        "Use the historical self-knowledge summary below as a prior for this model.",
        "These statistics come from held-out MarketBench tasks for this same model and exclude the current task.",
        "",
        "Historical self-knowledge summary:",
        f"- Across {len(held_out)} held-out tasks, your pass rate was {_format_pct(pass_rate)}.",
        f"- Your mean previously stated success probability was {_format_pct(mean_p)}; you were {_calibration_phrase(mean_p, pass_rate)}.",
    ]
    if token_multipliers:
        lines.append(
            f"- Your actual solve-token usage was typically {_format_multiplier(median(token_multipliers))} your estimate."
        )
    if repo_note is not None:
        lines.append(repo_note)
    lines.extend(
        [
            "",
            "Start from these historical tendencies, then update using the specific evidence in the current task.",
            "Be willing to move away from the historical average when the task details clearly support it.",
        ]
    )
    return "\n".join(lines)


def _build_experiment_prompt(
    *,
    task: dict[str, Any],
    self_knowledge_card: str,
) -> str:
    base_prompt = build_calibration_prompt(
        task_id=str(task["task_id"]),
        task_title=str(task["title"]),
        task_description=str(task["description"]),
        acceptance_commands=[str(cmd) for cmd in list(task.get("acceptance") or [])],
        strategy=PromptStrategy.DIRECT,
    )
    return "\n\n".join(
        [
            self_knowledge_card.strip(),
            "Now answer the same calibration task below. Use the historical summary as a prior and adjust from it using the current task evidence.",
            base_prompt,
        ]
    )


def _quality_snapshot(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {"count": 0}
    summary = summarize_calibration(records)
    overall = dict(summary.get("overall") or {})
    overall["count"] = len(records)
    return overall


def _model_token_summary(
    rows: list[dict[str, Any]],
    *,
    pricing_by_model: dict[str, float],
) -> dict[str, Any]:
    valid_rows = [row for row in rows if row.get("outcome") is not None]
    if not valid_rows:
        return {"overall": {}, "by_model": {}}

    base_rate = sum(_safe_int(row.get("outcome"), default=0) for row in valid_rows) / len(
        valid_rows
    )
    base_brier = sum(
        (base_rate - _safe_int(row.get("outcome"), default=0)) ** 2 for row in valid_rows
    ) / len(valid_rows)

    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in valid_rows:
        by_model[str(row.get("model_ref") or "unknown")].append(row)

    def summarize_subset(subset: list[dict[str, Any]]) -> dict[str, Any]:
        mean_p = sum(_safe_float(row.get("p_success"), default=0.5) for row in subset) / len(subset)
        pass_rate = sum(_safe_int(row.get("outcome"), default=0) for row in subset) / len(subset)
        brier = sum(
            (
                _safe_float(row.get("p_success"), default=0.5)
                - _safe_int(row.get("outcome"), default=0)
            )
            ** 2
            for row in subset
        ) / len(subset)
        ratios: list[float] = []
        estimated: list[float] = []
        actuals: list[float] = []
        for row in subset:
            est = _safe_float(row.get("estimated_tokens_total"), default=0.0)
            actual = _actual_tokens_total(row, pricing_by_model=pricing_by_model)
            if est > 0.0:
                estimated.append(est)
            if actual is not None and actual > 0.0:
                actuals.append(actual)
                if est > 0.0:
                    ratios.append(est / actual)
        return {
            "count": len(subset),
            "mean_p_success": mean_p,
            "pass_rate": pass_rate,
            "brier": brier,
            "brier_skill": (1.0 - (brier / base_brier)) if base_brier > 0 else 0.0,
            "mean_estimated_tokens_total": (sum(estimated) / len(estimated)) if estimated else 0.0,
            "mean_actual_tokens_total": (sum(actuals) / len(actuals)) if actuals else 0.0,
            "median_est_over_actual_ratio": median(ratios) if ratios else 0.0,
        }

    return {
        "overall": summarize_subset(valid_rows),
        "by_model": {
            model_ref: summarize_subset(model_rows)
            for model_ref, model_rows in sorted(by_model.items())
        },
    }


def _comparison_summary(
    *,
    baseline_rows: list[dict[str, Any]],
    experiment_rows: list[dict[str, Any]],
    pricing_by_model: dict[str, float],
) -> dict[str, Any]:
    baseline_metrics = summarize_calibration(baseline_rows)
    experiment_metrics = summarize_calibration(experiment_rows)
    baseline_tokens = _model_token_summary(baseline_rows, pricing_by_model=pricing_by_model)
    experiment_tokens = _model_token_summary(experiment_rows, pricing_by_model=pricing_by_model)

    baseline_overall = dict(baseline_metrics.get("overall") or {})
    baseline_overall.update(baseline_tokens.get("overall") or {})
    experiment_overall = dict(experiment_metrics.get("overall") or {})
    experiment_overall.update(experiment_tokens.get("overall") or {})

    delta = {}
    for key in (
        "brier",
        "ece",
        "mean_p_success",
        "mean_estimated_tokens_total",
        "mean_actual_tokens_total",
        "median_est_over_actual_ratio",
    ):
        if key not in baseline_overall or key not in experiment_overall:
            continue
        delta[key] = _safe_float(experiment_overall[key]) - _safe_float(baseline_overall[key])

    by_model_delta: dict[str, dict[str, float]] = {}
    baseline_by_model = baseline_tokens.get("by_model") or {}
    experiment_by_model = experiment_tokens.get("by_model") or {}
    for model_ref in sorted(set(baseline_by_model) | set(experiment_by_model)):
        lhs = baseline_by_model.get(model_ref) or {}
        rhs = experiment_by_model.get(model_ref) or {}
        entry: dict[str, float] = {}
        for key in ("brier", "brier_skill", "mean_p_success", "median_est_over_actual_ratio"):
            if key in lhs and key in rhs:
                entry[key] = _safe_float(rhs[key]) - _safe_float(lhs[key])
        by_model_delta[model_ref] = entry

    return {
        "experiment_strategy": EXPERIMENT_STRATEGY,
        "baseline_strategy": "direct",
        "baseline_overall": baseline_overall,
        "experiment_overall": experiment_overall,
        "delta_experiment_minus_baseline": delta,
        "baseline_by_model": baseline_by_model,
        "experiment_by_model": experiment_by_model,
        "delta_by_model": by_model_delta,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the self-knowledge-augmented Phase I calibration experiment."
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("docs/research/data/phase1/calibration_results.jsonl"),
        help="Baseline direct calibration JSONL used for labels and held-out self-knowledge cards.",
    )
    parser.add_argument(
        "--pricing-csv",
        type=Path,
        default=Path("docs/research/data/phase1/model_token_pricing.csv"),
        help="Per-model blended pricing CSV.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("runs/research/phase1") / f"{EXPERIMENT_STRATEGY}_{_now_tag()}",
        help="Output directory for experiment artifacts.",
    )
    parser.add_argument(
        "--calibration-concurrency",
        type=int,
        default=6,
        help="Parallel workers for calibration elicitation.",
    )
    parser.add_argument(
        "--check-every",
        type=int,
        default=25,
        help="Checkpoint frequency for quality snapshots.",
    )
    parser.add_argument(
        "--task-limit",
        type=int,
        default=0,
        help="Optional prefix limit on the baseline task list; 0 means all tasks.",
    )
    parser.add_argument(
        "--models",
        default="",
        help="Optional CSV of model_refs to execute. Leave empty to run all baseline models.",
    )
    parser.add_argument(
        "--rerun-hard-errors",
        action="store_true",
        help="Retry rows with hard_error for the selected models instead of treating them as completed.",
    )
    args = parser.parse_args()

    all_baseline_rows = _baseline_rows(Path(args.baseline))
    if not all_baseline_rows:
        raise SystemExit(f"no direct baseline rows found in {args.baseline}")

    all_model_refs = _first_seen_values(all_baseline_rows, "model_ref")
    requested_models = [part.strip() for part in str(args.models).split(",") if part.strip()]
    if requested_models:
        unknown_models = [
            model_ref for model_ref in requested_models if model_ref not in all_model_refs
        ]
        if unknown_models:
            raise SystemExit(f"unknown model refs in --models: {unknown_models}")
        model_refs = requested_models
    else:
        model_refs = list(all_model_refs)
    task_ids = _first_seen_values(all_baseline_rows, "task_id")
    if int(args.task_limit) > 0:
        task_ids = task_ids[: int(args.task_limit)]

    task_id_set = set(task_ids)
    selected_baseline_rows = [
        row
        for row in all_baseline_rows
        if str(row.get("task_id") or "") in task_id_set
        and str(row.get("model_ref") or "") in set(model_refs)
    ]
    baseline_lookup = {
        (str(row["model_ref"]), str(row["task_id"])): row for row in selected_baseline_rows
    }
    expected_rows = len(model_refs) * len(task_ids)
    if len(baseline_lookup) != expected_rows:
        raise SystemExit(
            f"baseline coverage mismatch: expected {expected_rows} rows, found {len(baseline_lookup)}"
        )

    pricing_by_model = _load_pricing_csv(Path(args.pricing_csv))
    tasks_by_id = _build_task_specs(task_ids)
    task_repo_by_id = {task_id: str(task["repo"]) for task_id, task in tasks_by_id.items()}

    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    selected_tasks_path = output_root / "selected_tasks.jsonl"
    if not selected_tasks_path.exists():
        for task_id in task_ids:
            task = tasks_by_id[task_id]
            _append_jsonl(
                selected_tasks_path,
                {
                    "benchmark": task["benchmark"],
                    "task_id": task["task_id"],
                    "title": task["title"],
                    "meta": {"repo": task["repo"]},
                },
            )

    results_path = output_root / "calibration_results.jsonl"
    quality_checks_path = output_root / "quality_checks.jsonl"
    existing_records = _read_jsonl(results_path) if results_path.exists() else []
    selected_model_set = set(model_refs)
    existing_lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for row in existing_records:
        key = (str(row.get("model_ref") or ""), str(row.get("task_id") or ""))
        if args.rerun_hard_errors and key[0] in selected_model_set and row.get("hard_error"):
            continue
        existing_lookup[key] = row

    baseline_by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in all_baseline_rows:
        baseline_by_model[str(row["model_ref"])].append(row)

    workers = [
        WorkerRuntime(
            worker_id=f"calib-{idx}",
            worker_type=WorkerType.MODEL_AGENT,
            model_ref=model_ref,
        )
        for idx, model_ref in enumerate(model_refs)
    ]
    llm = _llm_router_for_workers(settings=load_settings(), workers=workers)

    def run_one(model_ref: str, task_id: str) -> dict[str, Any]:
        task = tasks_by_id[task_id]
        baseline = dict(baseline_lookup[(model_ref, task_id)])
        card = _build_self_knowledge_card(
            model_ref=model_ref,
            task_id=task_id,
            task_repo=str(task["repo"]),
            baseline_by_model=baseline_by_model,
            task_repo_by_id=task_repo_by_id,
            pricing_by_model=pricing_by_model,
        )
        user_prompt = _build_experiment_prompt(task=task, self_knowledge_card=card)

        try:
            response, usage, _raw = llm.call_json(
                model_ref=model_ref,
                system=SYSTEM_PROMPT,
                user=user_prompt,
                schema=CalibrationResponse,
                max_output_tokens=1200,
                temperature=0.0,
            )
            p_success = float(response.p_success)
            estimated_tokens_total = int(response.estimated_tokens_total)
            rationale = response.rationale
            hard_error = None
            input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        except Exception as exc:
            p_success = 0.5
            estimated_tokens_total = 0
            rationale = f"ERROR: {type(exc).__name__}: {exc}"
            hard_error = f"{type(exc).__name__}: {exc}"
            input_tokens = 0
            output_tokens = 0

        return {
            **baseline,
            "strategy": EXPERIMENT_STRATEGY,
            "p_success": p_success,
            "estimated_tokens_total": estimated_tokens_total,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "rationale": rationale,
            "self_knowledge_card": card,
            "prompt_condition": EXPERIMENT_STRATEGY,
            "hard_error": hard_error,
        }

    jobs = [(model_ref, task_id) for model_ref in model_refs for task_id in task_ids]
    completed_records = list(existing_lookup.values())
    completed = len(existing_lookup)
    if completed:
        print(f"[self-knowledge] resumed {completed}/{len(jobs)} completed rows", flush=True)

    new_jobs = [
        (model_ref, task_id)
        for model_ref, task_id in jobs
        if (model_ref, task_id) not in existing_lookup
    ]

    with ThreadPoolExecutor(max_workers=max(1, int(args.calibration_concurrency))) as pool:
        future_to_key = {
            pool.submit(run_one, model_ref, task_id): (model_ref, task_id)
            for model_ref, task_id in new_jobs
        }
        for fut in as_completed(future_to_key):
            model_ref, task_id = future_to_key[fut]
            row = fut.result()
            existing_lookup[(model_ref, task_id)] = row
            completed_records.append(row)
            completed += 1
            _append_jsonl(results_path, row)
            print(
                f"[self-knowledge] calibration {completed}/{len(jobs)} model={model_ref} task={task_id}",
                flush=True,
            )
            if int(args.check_every) > 0 and completed % int(args.check_every) == 0:
                _append_jsonl(
                    quality_checks_path,
                    {
                        "type": "checkpoint",
                        "completed": completed,
                        "total": len(jobs),
                        **_quality_snapshot(completed_records),
                    },
                )

    ordered_records = [
        existing_lookup[(model_ref, task_id)]
        for model_ref in all_model_refs
        for task_id in task_ids
        if (model_ref, task_id) in existing_lookup
    ]
    _write_jsonl(results_path, ordered_records)
    _write_json(
        output_root / "metrics_summary.json",
        summarize_calibration(ordered_records),
    )
    ordered_key_set = {
        (str(row.get("model_ref") or ""), str(row.get("task_id") or "")) for row in ordered_records
    }
    summary_baseline_rows = [
        row
        for row in all_baseline_rows
        if (str(row.get("model_ref") or ""), str(row.get("task_id") or "")) in ordered_key_set
    ]

    _write_json(
        output_root / "comparison_summary.json",
        _comparison_summary(
            baseline_rows=summary_baseline_rows,
            experiment_rows=ordered_records,
            pricing_by_model=pricing_by_model,
        ),
    )
    _append_jsonl(
        quality_checks_path,
        {
            "type": "final",
            "completed": len(ordered_records),
            "total": len(jobs),
            **_quality_snapshot(ordered_records),
        },
    )

    print(f"output_root={output_root}")
    print(f"calibration_results={results_path}")
    print(f"metrics_summary={output_root / 'metrics_summary.json'}")
    print(f"comparison_summary={output_root / 'comparison_summary.json'}")


if __name__ == "__main__":
    main()
