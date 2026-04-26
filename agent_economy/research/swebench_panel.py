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


def write_core_panel(rows: Iterable[SwebenchAttemptRow], path: Path) -> None:
    _write_rows([row.core_csv_row() for row in rows], path=path, fieldnames=CORE_COLUMNS)


def write_source_panel(rows: Iterable[SwebenchAttemptRow], path: Path) -> None:
    _write_rows([row.source_csv_row() for row in rows], path=path, fieldnames=SOURCE_COLUMNS)


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
