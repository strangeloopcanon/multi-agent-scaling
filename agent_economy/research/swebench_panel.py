from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


CORE_COLUMNS = ["model", "task", "task family", "success", "token consumption"]
SOURCE_COLUMNS = [
    *CORE_COLUMNS,
    "source_run",
    "run_id",
    "round_id",
    "attempt_index",
    "verify_status",
    "input_tokens",
    "output_tokens",
    "worker_id",
    "ledger_path",
]
PUBLISHED_SOURCE_COLUMNS = [
    "published_result",
    "published_phase",
    "published_paradigm",
    "source_kind",
    "coverage_note",
    *SOURCE_COLUMNS,
]


@dataclass(frozen=True)
class PublishedRunSpec:
    result: str
    phase: str
    paradigm: str
    roots: tuple[str, ...]
    source_kind: str = "raw_ledger_exact"
    coverage_note: str = ""
    include_tasks: frozenset[str] | None = None
    exclude_tasks: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SwebenchAttemptRow:
    model: str
    task: str
    task_family: str
    success: bool
    token_consumption: int
    source_run: str
    run_id: str
    round_id: int
    attempt_index: int
    verify_status: str
    input_tokens: int
    output_tokens: int
    worker_id: str
    ledger_path: str

    def core_csv_row(self) -> dict[str, str | int]:
        return {
            "model": self.model,
            "task": self.task,
            "task family": self.task_family,
            "success": "true" if self.success else "false",
            "token consumption": self.token_consumption,
        }

    def source_csv_row(self) -> dict[str, str | int]:
        return {
            **self.core_csv_row(),
            "source_run": self.source_run,
            "run_id": self.run_id,
            "round_id": self.round_id,
            "attempt_index": self.attempt_index,
            "verify_status": self.verify_status,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "worker_id": self.worker_id,
            "ledger_path": self.ledger_path,
        }


@dataclass(frozen=True)
class PublishedAttemptRow:
    attempt: SwebenchAttemptRow
    published_result: str
    published_phase: str
    published_paradigm: str
    source_kind: str
    coverage_note: str

    def core_csv_row(self) -> dict[str, str | int]:
        return self.attempt.core_csv_row()

    def source_csv_row(self) -> dict[str, str | int]:
        return {
            "published_result": self.published_result,
            "published_phase": self.published_phase,
            "published_paradigm": self.published_paradigm,
            "source_kind": self.source_kind,
            "coverage_note": self.coverage_note,
            **self.attempt.source_csv_row(),
        }


PUBLISHED_MARKET_900S_MISSING_BATCH_NOTE = (
    "Exact model-attempt rows are available only for the surviving 30-task raw-ledger "
    "subset; the 20-task next20_market_20260219T074528Z batch is preserved only in "
    "summary artifacts."
)

PHASE2D_FINAL_REPLACEMENT_TASKS = frozenset(
    {
        "astropy__astropy-12907",
        "django__django-12308",
        "matplotlib__matplotlib-23314",
        "pytest-dev__pytest-7432",
        "scikit-learn__scikit-learn-13142",
        "scikit-learn__scikit-learn-13496",
        "sympy__sympy-15345",
    }
)

PUBLISHED_BASE_SPECS = (
    PublishedRunSpec(
        result="phase2_market_900s",
        phase="Phase II",
        paradigm="market_900s",
        roots=(
            "gateB10_exclfix_t900_20260218T190909Z",
            "next20_contiguous_market_20260219T183750Z",
        ),
        source_kind="raw_ledger_exact_partial",
        coverage_note=PUBLISHED_MARKET_900S_MISSING_BATCH_NOTE,
    ),
    PublishedRunSpec(
        result="phase2_solo_gpt52_900s",
        phase="Phase II",
        paradigm="solo_gpt52_900s",
        roots=(
            "solo_gpt52_10_cleanDocker_20260218T225012Z",
            "solo_gpt52_market50_remaining40_20260219T221230Z",
        ),
    ),
    PublishedRunSpec(
        result="phase2c_codex_gpt52_relaxed_1800s",
        phase="Phase IIc",
        paradigm="codex_gpt52_1800s",
        roots=("codex_direct_gpt52_relaxedfull50_20260402T050426Z",),
    ),
    PublishedRunSpec(
        result="phase2b_market_matched_900s",
        phase="Phase IIb",
        paradigm="market_matched_900s",
        roots=(
            "exp2_market_matched_timeoutaligned_20260409T030344Z",
            "exp2_market_shard01_offset002_limit016_20260409T033114Z",
            "exp2_market_shard02_offset018_limit016_20260409T033114Z",
            "exp2_market_shard03_offset034_limit016_20260409T033114Z",
        ),
    ),
    PublishedRunSpec(
        result="phase2b_central_router_matched_900s",
        phase="Phase IIb",
        paradigm="central_router_matched_900s",
        roots=(
            "exp2_central_router_matched_timeoutaligned_20260409T030344Z",
            "exp2_central_shard01_offset002_limit016_20260409T033114Z",
            "exp2_central_shard02_offset018_limit016_20260409T033114Z",
            "exp2_central_shard03_offset034_limit016_20260409T033114Z",
        ),
    ),
)

PHASE2D_FINAL_SPECS = (
    PublishedRunSpec(
        result="phase2d_hardprior_market_final",
        phase="Phase IId",
        paradigm="hardprior_market_final",
        roots=(
            "phase2d_targeted5_hardprior_20260410T003800Z",
            "phase2d_remaining45_hardprior_20260410T045117Z",
            "phase2d_tail4_hardprior_20260410T142400Z",
        ),
        coverage_note=(
            "Initial full-sweep rows excluding the seven-task cleanup slice replaced "
            "in the final Phase IId accounting."
        ),
        exclude_tasks=PHASE2D_FINAL_REPLACEMENT_TASKS,
    ),
    PublishedRunSpec(
        result="phase2d_hardprior_market_final",
        phase="Phase IId",
        paradigm="hardprior_market_final",
        roots=("phase2d_repair1_astropy_hardprior_1800_20260411T004900Z",),
        coverage_note="Final cleanup-row replacement for astropy__astropy-12907.",
        include_tasks=frozenset({"astropy__astropy-12907"}),
    ),
    PublishedRunSpec(
        result="phase2d_hardprior_market_final",
        phase="Phase IId",
        paradigm="hardprior_market_final",
        roots=("phase2d_repair7_hardprior_valid2_20260410T214500Z",),
        coverage_note="Final cleanup-row replacement for django__django-12308.",
        include_tasks=frozenset({"django__django-12308"}),
    ),
    PublishedRunSpec(
        result="phase2d_hardprior_market_final",
        phase="Phase IId",
        paradigm="hardprior_market_final",
        roots=("phase2d_repair5_hardprior_1800_20260411T031545Z",),
        coverage_note="Final cleanup-row replacements for the remaining five tasks.",
        include_tasks=frozenset(
            {
                "matplotlib__matplotlib-23314",
                "pytest-dev__pytest-7432",
                "scikit-learn__scikit-learn-13142",
                "scikit-learn__scikit-learn-13496",
                "sympy__sympy-15345",
            }
        ),
    ),
)

PUBLISHED_RESULT_EXPECTATIONS = {
    "phase2_market_900s": {
        "published_task_count": 50,
        "published_pass_count": 29,
        "published_token_note": "Published total is about 5.82M including bid overhead; exact attempt rows here cover the surviving 30-task raw-ledger subset.",
        "supporting_artifacts": [
            "docs/research/data/phase2/phase2_rollup_50.json",
            "docs/research/data/phase2/market_vs_solo_summary.json",
            "docs/research/data/phase2/per_task_outcomes.jsonl",
            "docs/research/data/phase2/overlap_manifest.json",
        ],
    },
    "phase2_solo_gpt52_900s": {
        "published_task_count": 50,
        "published_pass_count": 24,
        "published_token_note": "Published total is about 4.37M including available execution accounting; attempt-token rows use patch-generation input plus output tokens.",
        "supporting_artifacts": [
            "docs/research/data/phase2/market_vs_solo_summary.json",
            "docs/research/data/phase2/per_task_outcomes.jsonl",
        ],
    },
    "phase2c_codex_gpt52_relaxed_1800s": {
        "published_task_count": 50,
        "published_pass_count": 35,
        "published_attempts": 66,
        "published_tokens": 321335319,
        "supporting_artifacts": [
            "docs/research/data/phase2/codex_relaxed_gpt52_summary.json",
            "docs/research/report/09_PHASE_2_CODEX_RELAXED_TIME_GPT52_2026-04-02.md",
        ],
    },
    "phase2b_market_matched_900s": {
        "published_task_count": 50,
        "published_pass_count": 23,
        "published_token_note": "Published total is 5,072,291 including bid overhead; attempt-token rows use patch-generation input plus output tokens.",
        "supporting_artifacts": [
            "docs/research/report/10_PHASE_2B_CENTRAL_ROUTER_BASELINE_2026-04-07.md",
        ],
    },
    "phase2b_central_router_matched_900s": {
        "published_task_count": 50,
        "published_pass_count": 27,
        "published_token_note": "Published total is 3,479,510 including router/bid overhead; attempt-token rows use patch-generation input plus output tokens.",
        "supporting_artifacts": [
            "docs/research/report/10_PHASE_2B_CENTRAL_ROUTER_BASELINE_2026-04-07.md",
        ],
    },
    "phase2d_hardprior_market_final": {
        "published_task_count": 50,
        "published_pass_count": 28,
        "published_token_note": "Published adjusted total is 5,343,801 including market overhead; attempt-token rows use patch-generation input plus output tokens.",
        "supporting_artifacts": [
            "docs/research/report/10_PHASE_2B_CENTRAL_ROUTER_BASELINE_2026-04-07.md",
            "runs/research/phase2/phase2d_full50_combined_20260410T171500Z_task_outcomes.csv",
        ],
    },
}


def task_family_from_instance_id(instance_id: str) -> str:
    owner, sep, repo_and_issue = str(instance_id).strip().partition("__")
    if not sep or not owner or not repo_and_issue:
        return "unknown"

    repo, issue_sep, _issue = repo_and_issue.rpartition("-")
    if not issue_sep or not repo:
        repo = repo_and_issue
    return f"{owner}/{repo}"


def find_swebench_ledgers(
    roots: Iterable[Path],
    *,
    include_invalid: bool = False,
) -> list[Path]:
    ledgers: set[Path] = set()
    for root in roots:
        root = Path(root)
        if root.is_file() and root.name == "ledger.jsonl":
            ledgers.add(root)
            continue
        if (root / "ledger.jsonl").exists():
            ledgers.add(root / "ledger.jsonl")
        ledgers.update(root.glob("swebench_*/ledger.jsonl"))
        ledgers.update(root.glob("*/swebench_*/ledger.jsonl"))
        ledgers.update(root.glob("*/*/swebench_*/ledger.jsonl"))

    filtered = []
    for ledger in ledgers:
        if not include_invalid and "_invalid" in ledger.parts:
            continue
        filtered.append(ledger)
    return sorted(filtered, key=lambda p: str(p))


def attempt_rows_from_ledger(ledger_path: Path) -> list[SwebenchAttemptRow]:
    worker_models: dict[str, str] = {}
    patches_by_key: dict[tuple[int, str, str], dict[str, Any]] = {}
    attempts_by_task: dict[str, int] = {}
    rows: list[SwebenchAttemptRow] = []

    with Path(ledger_path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            event_type = str(event.get("type") or "")
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            round_id = _as_int(event.get("round_id"))

            if event_type == "worker_registered":
                worker_id = str(payload.get("worker_id") or "")
                model_ref = str(payload.get("model_ref") or "")
                if worker_id and model_ref:
                    worker_models[worker_id] = model_ref
                continue

            if event_type == "patch_submitted":
                task = str(payload.get("task_id") or "")
                worker_id = str(payload.get("worker_id") or "")
                if task and worker_id:
                    patches_by_key[(round_id, task, worker_id)] = dict(payload)
                continue

            if event_type != "task_completed":
                continue

            task = str(payload.get("task_id") or "")
            worker_id = str(payload.get("worker_id") or "")
            if "__" not in task or not worker_id:
                continue

            patch = patches_by_key.get((round_id, task, worker_id), {})
            usage = patch.get("llm_usage") if isinstance(patch.get("llm_usage"), dict) else {}
            input_tokens = _as_int((usage or {}).get("input_tokens"))
            output_tokens = _as_int((usage or {}).get("output_tokens"))
            model = str(patch.get("model_ref") or worker_models.get(worker_id) or "")
            if not model:
                continue

            attempts_by_task[task] = attempts_by_task.get(task, 0) + 1
            verify_status = str(payload.get("verify_status") or "")
            rows.append(
                SwebenchAttemptRow(
                    model=model,
                    task=task,
                    task_family=task_family_from_instance_id(task),
                    success=bool(payload.get("success")) or verify_status == "PASS",
                    token_consumption=input_tokens + output_tokens,
                    source_run=Path(ledger_path).parent.parent.name,
                    run_id=str(event.get("run_id") or ""),
                    round_id=round_id,
                    attempt_index=attempts_by_task[task],
                    verify_status=verify_status,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    worker_id=worker_id,
                    ledger_path=str(Path(ledger_path)),
                )
            )

    return rows


def collect_attempt_rows(
    roots: Iterable[Path],
    *,
    include_invalid: bool = False,
) -> list[SwebenchAttemptRow]:
    rows: list[SwebenchAttemptRow] = []
    for ledger in find_swebench_ledgers(roots, include_invalid=include_invalid):
        rows.extend(attempt_rows_from_ledger(ledger))
    return sorted(
        rows,
        key=lambda r: (r.source_run, r.task, r.round_id, r.worker_id, r.model),
    )


def published_attempt_rows_from_spec(
    spec: PublishedRunSpec,
    *,
    runs_root: Path,
) -> list[PublishedAttemptRow]:
    rows = collect_attempt_rows([runs_root / root for root in spec.roots])
    selected = []
    for row in rows:
        if spec.include_tasks is not None and row.task not in spec.include_tasks:
            continue
        if row.task in spec.exclude_tasks:
            continue
        selected.append(
            PublishedAttemptRow(
                attempt=row,
                published_result=spec.result,
                published_phase=spec.phase,
                published_paradigm=spec.paradigm,
                source_kind=spec.source_kind,
                coverage_note=spec.coverage_note,
            )
        )
    return selected


def collect_published_attempt_rows(
    *,
    runs_root: Path = Path("runs/research/phase2"),
) -> list[PublishedAttemptRow]:
    rows: list[PublishedAttemptRow] = []
    for spec in (*PUBLISHED_BASE_SPECS, *PHASE2D_FINAL_SPECS):
        rows.extend(published_attempt_rows_from_spec(spec, runs_root=runs_root))
    return sorted(
        rows,
        key=lambda r: (
            r.published_result,
            r.attempt.source_run,
            r.attempt.task,
            r.attempt.round_id,
            r.attempt.worker_id,
            r.attempt.model,
        ),
    )


def build_published_manifest(rows: Iterable[PublishedAttemptRow]) -> dict[str, Any]:
    rows_by_result: dict[str, list[PublishedAttemptRow]] = {}
    for row in rows:
        rows_by_result.setdefault(row.published_result, []).append(row)

    results: dict[str, dict[str, Any]] = {}
    for result, result_rows in sorted(rows_by_result.items()):
        tasks = sorted({row.attempt.task for row in result_rows})
        solved_tasks = sorted(
            {
                task
                for task in tasks
                if any(row.attempt.task == task and row.attempt.success for row in result_rows)
            }
        )
        expectations = PUBLISHED_RESULT_EXPECTATIONS.get(result, {})
        expected_tasks = expectations.get("published_task_count")
        expected_passes = expectations.get("published_pass_count")
        results[result] = {
            "published_phase": result_rows[0].published_phase,
            "published_paradigm": result_rows[0].published_paradigm,
            "row_count": len(result_rows),
            "exact_attempt_task_count": len(tasks),
            "exact_attempt_pass_count": len(solved_tasks),
            "exact_attempt_token_consumption": sum(
                row.attempt.token_consumption for row in result_rows
            ),
            "matches_published_pass_count": (
                len(solved_tasks) == expected_passes
                if len(tasks) == expected_tasks and expected_passes is not None
                else None
            ),
            **expectations,
            "coverage_notes": sorted(
                {row.coverage_note for row in result_rows if row.coverage_note}
            ),
            "source_runs": sorted({row.attempt.source_run for row in result_rows}),
        }

    return {
        "description": (
            "Published-only SWE-bench model-task attempt panel. Core CSV rows keep "
            "the same five-column shape as the all-ledger panel; source CSV rows add "
            "published-result provenance."
        ),
        "token_consumption_definition": (
            "Input plus output tokens attached to the patch-generation attempt in the "
            "run ledger. Market bid/router overhead is documented in published summary "
            "artifacts but is not assigned to individual model-task patch attempts here."
        ),
        "known_limitations": [
            PUBLISHED_MARKET_900S_MISSING_BATCH_NOTE,
            "Rows are attempt-level; task-level pass counts in this manifest treat a task as solved if any included attempt passed.",
        ],
        "results": results,
    }


def write_core_panel(rows: Iterable[SwebenchAttemptRow], path: Path) -> None:
    _write_rows([row.core_csv_row() for row in rows], path=path, fieldnames=CORE_COLUMNS)


def write_source_panel(rows: Iterable[SwebenchAttemptRow], path: Path) -> None:
    _write_rows([row.source_csv_row() for row in rows], path=path, fieldnames=SOURCE_COLUMNS)


def write_published_core_panel(rows: Iterable[PublishedAttemptRow], path: Path) -> None:
    _write_rows([row.core_csv_row() for row in rows], path=path, fieldnames=CORE_COLUMNS)


def write_published_source_panel(rows: Iterable[PublishedAttemptRow], path: Path) -> None:
    _write_rows(
        [row.source_csv_row() for row in rows],
        path=path,
        fieldnames=PUBLISHED_SOURCE_COLUMNS,
    )


def _write_rows(
    rows: list[dict[str, str | int]],
    *,
    path: Path,
    fieldnames: list[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0
