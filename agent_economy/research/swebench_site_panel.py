from __future__ import annotations

import csv
import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_economy.research.swebench_panel import (
    CORE_COLUMNS,
    task_family_from_instance_id,
)


DEFAULT_LEADERBOARDS_URL = (
    "https://raw.githubusercontent.com/swe-bench/swe-bench.github.io/master/data/leaderboards.json"
)
DEFAULT_TASK_LIST = Path("docs/research/data/phase2/per_task_outcomes.jsonl")
MISSING_TOKEN_NOTE = (
    "SWE-bench website per-instance details expose resolved, cost, and api_calls; "
    "they do not expose input/output token counts."
)

SITE_SOURCE_COLUMNS = [
    *CORE_COLUMNS,
    "leaderboard",
    "site_submission_name",
    "site_submission_folder",
    "site_submission_date",
    "site_resolved_rate",
    "site_cost",
    "site_api_calls",
    "site_model_tags",
    "source_url",
    "token_note",
]


@dataclass(frozen=True)
class SwebenchSiteModelSpec:
    model: str
    folder: str | None
    missing_note: str = ""


@dataclass(frozen=True)
class SwebenchSiteRow:
    model: str
    task: str
    task_family: str
    success: bool
    site_cost: float | None
    site_api_calls: int | None
    leaderboard: str
    site_submission_name: str
    site_submission_folder: str
    site_submission_date: str
    site_resolved_rate: float | None
    site_model_tags: str
    source_url: str

    def core_csv_row(self) -> dict[str, str | int]:
        return {
            "model": self.model,
            "task": self.task,
            "task family": self.task_family,
            "success": "true" if self.success else "false",
            "token consumption": "",
        }

    def source_csv_row(self) -> dict[str, str | int | float | None]:
        return {
            **self.core_csv_row(),
            "leaderboard": self.leaderboard,
            "site_submission_name": self.site_submission_name,
            "site_submission_folder": self.site_submission_folder,
            "site_submission_date": self.site_submission_date,
            "site_resolved_rate": self.site_resolved_rate,
            "site_cost": self.site_cost,
            "site_api_calls": self.site_api_calls,
            "site_model_tags": self.site_model_tags,
            "source_url": self.source_url,
            "token_note": MISSING_TOKEN_NOTE,
        }


BASH_ONLY_MODEL_SPECS = (
    SwebenchSiteModelSpec(
        model="openai:gpt-5.2-2025-12-11",
        folder="20251211_mini-v1.17.2_gpt-5.2-2025-12-11",
    ),
    SwebenchSiteModelSpec(
        model="openai:gpt-5-mini-2025-08-07",
        folder="20250807_mini-v1.7.0_gpt-5-mini",
    ),
    SwebenchSiteModelSpec(
        model="anthropic:claude-opus-4-5-20251101",
        folder="20251124_mini-v1.16.0_claude-opus-4-5-20251101",
    ),
    SwebenchSiteModelSpec(
        model="anthropic:claude-sonnet-4-5-20250929",
        folder="20250929_mini-v1.13.3_sonnet-4-5-20250929",
    ),
    SwebenchSiteModelSpec(
        model="google:models/gemini-3-pro-preview",
        folder="20251118_mini-v1.15.0_gemini-3-pro-preview-20251118",
    ),
    SwebenchSiteModelSpec(
        model="openai:gpt-5.2-pro-2025-12-11",
        folder=None,
        missing_note=(
            "No exact GPT-5.2 Pro row appears in the SWE-bench bash-only site data; "
            "GPT-5.2 high and GPT-5.2 Codex are separate entries."
        ),
    ),
)


def load_task_ids(path: Path = DEFAULT_TASK_LIST) -> list[str]:
    task_ids: list[str] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            payload = json.loads(line)
            task_id = str(payload.get("instance_id") or "")
            if task_id:
                task_ids.append(task_id)
    return task_ids


def load_leaderboards(source_url: str = DEFAULT_LEADERBOARDS_URL) -> dict[str, Any]:
    with urllib.request.urlopen(source_url, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def collect_site_rows(
    payload: dict[str, Any],
    *,
    task_ids: list[str],
    source_url: str = DEFAULT_LEADERBOARDS_URL,
    leaderboard_name: str = "bash-only",
    model_specs: tuple[SwebenchSiteModelSpec, ...] = BASH_ONLY_MODEL_SPECS,
) -> tuple[list[SwebenchSiteRow], dict[str, str]]:
    leaderboard = _find_leaderboard(payload, leaderboard_name)
    entries_by_folder = {
        str(entry.get("folder") or ""): entry
        for entry in leaderboard.get("results", [])
        if isinstance(entry, dict)
    }
    rows: list[SwebenchSiteRow] = []
    missing_models: dict[str, str] = {}

    for spec in model_specs:
        if spec.folder is None:
            missing_models[spec.model] = spec.missing_note or "No site folder configured."
            continue

        entry = entries_by_folder.get(spec.folder)
        if entry is None:
            missing_models[spec.model] = f"Site folder not found: {spec.folder}"
            continue

        details = entry.get("per_instance_details")
        if not isinstance(details, dict):
            missing_models[spec.model] = f"Site folder has no per_instance_details: {spec.folder}"
            continue

        for task_id in task_ids:
            raw_detail = details.get(task_id)
            if not isinstance(raw_detail, dict):
                continue
            rows.append(
                SwebenchSiteRow(
                    model=spec.model,
                    task=task_id,
                    task_family=task_family_from_instance_id(task_id),
                    success=bool(raw_detail.get("resolved")),
                    site_cost=_as_optional_float(raw_detail.get("cost")),
                    site_api_calls=_as_optional_int(raw_detail.get("api_calls")),
                    leaderboard=leaderboard_name,
                    site_submission_name=str(entry.get("name") or ""),
                    site_submission_folder=spec.folder,
                    site_submission_date=str(entry.get("date") or ""),
                    site_resolved_rate=_as_optional_float(entry.get("resolved")),
                    site_model_tags="; ".join(str(tag) for tag in entry.get("tags", [])),
                    source_url=source_url,
                )
            )

    return (
        sorted(rows, key=lambda row: (row.model, row.task)),
        missing_models,
    )


def build_site_manifest(
    rows: list[SwebenchSiteRow],
    *,
    task_ids: list[str],
    missing_models: dict[str, str],
    source_url: str = DEFAULT_LEADERBOARDS_URL,
    leaderboard_name: str = "bash-only",
    task_filter_source: Path = DEFAULT_TASK_LIST,
) -> dict[str, Any]:
    rows_by_model: dict[str, list[SwebenchSiteRow]] = {}
    for row in rows:
        rows_by_model.setdefault(row.model, []).append(row)

    models = {}
    for model, model_rows in sorted(rows_by_model.items()):
        models[model] = {
            "row_count": len(model_rows),
            "task_count": len({row.task for row in model_rows}),
            "pass_count": sum(1 for row in model_rows if row.success),
            "site_cost_sum": sum(row.site_cost or 0.0 for row in model_rows),
            "site_api_calls_sum": sum(row.site_api_calls or 0 for row in model_rows),
            "site_submission_name": model_rows[0].site_submission_name,
            "site_submission_folder": model_rows[0].site_submission_folder,
            "site_resolved_rate": model_rows[0].site_resolved_rate,
        }

    return {
        "description": (
            "Task-level panel pulled from the SWE-bench website leaderboard backing JSON, "
            "filtered to the repo's 50-task Phase II slice and the closest bash-only rows "
            "for the experiment worker models."
        ),
        "source_url": source_url,
        "leaderboard": leaderboard_name,
        "task_filter_source": str(task_filter_source),
        "requested_task_count": len(task_ids),
        "row_count": len(rows),
        "token_consumption_available": False,
        "token_note": MISSING_TOKEN_NOTE,
        "models": models,
        "missing_models": missing_models,
    }


def write_site_core_panel(rows: list[SwebenchSiteRow], path: Path) -> None:
    _write_rows([row.core_csv_row() for row in rows], path=path, fieldnames=CORE_COLUMNS)


def write_site_source_panel(rows: list[SwebenchSiteRow], path: Path) -> None:
    _write_rows(
        [row.source_csv_row() for row in rows],
        path=path,
        fieldnames=SITE_SOURCE_COLUMNS,
    )


def _find_leaderboard(payload: dict[str, Any], leaderboard_name: str) -> dict[str, Any]:
    leaderboards = payload.get("leaderboards", [])
    if not isinstance(leaderboards, list):
        raise ValueError("leaderboards payload must contain a leaderboards list")

    for leaderboard in leaderboards:
        if isinstance(leaderboard, dict) and leaderboard.get("name") == leaderboard_name:
            return leaderboard
    raise ValueError(f"leaderboard not found: {leaderboard_name}")


def _write_rows(
    rows: list[dict[str, str | int | float | None]],
    *,
    path: Path,
    fieldnames: list[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _as_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
