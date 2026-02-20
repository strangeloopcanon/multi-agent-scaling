from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agent_economy.config import repo_root
from agent_economy.schemas import CommandSpec, ContextSpec, JudgeSpec, TaskSpec


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    title: str
    template_dir: Path | None
    tasks: list[TaskSpec]


def _resolve_path(raw: str, *, scenario_path: Path) -> Path:
    p = Path(raw)
    if p.is_absolute():
        return p

    root = repo_root()

    # Prefer project-root relative paths, fall back to scenario-relative.
    # Compatibility: allow paths prefixed with "<project_dirname>/" from a prior nested layout.
    candidates: list[Path] = [p]
    if p.parts and p.parts[0] == root.name:
        candidates.append(Path(*p.parts[1:]))

    for rel in candidates:
        cand = (root / rel).resolve()
        if cand.exists():
            return cand
        cand2 = (scenario_path.parent / rel).resolve()
        if cand2.exists():
            return cand2

    # If nothing exists yet, return the scenario-relative location for error reporting.
    return (scenario_path.parent / p).resolve()


def _parse_commands(raw: Any) -> list[CommandSpec]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("acceptance must be a list")

    cmds: list[CommandSpec] = []
    for item in raw:
        if isinstance(item, str):
            cmds.append(CommandSpec(cmd=item))
        elif isinstance(item, dict):
            cmds.append(CommandSpec.model_validate(item))
        else:
            raise ValueError("acceptance entries must be strings or objects")
    return cmds


def _parse_judges(raw: Any) -> JudgeSpec | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        if not all(isinstance(x, str) for x in raw):
            raise ValueError("judges must be a list of worker refs (strings)")
        return JudgeSpec(workers=[str(x) for x in raw])
    if isinstance(raw, dict):
        return JudgeSpec.model_validate(raw)
    raise ValueError("judges must be a list or mapping")


def load_scenario(path: Path) -> ScenarioSpec:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("scenario must be a YAML mapping")

    scenario_id = str(data.get("scenario_id") or data.get("id") or "scenario")
    title = str(data.get("title") or scenario_id)
    template_dir_raw = data.get("template_dir")
    template_dir = (
        _resolve_path(str(template_dir_raw), scenario_path=path) if template_dir_raw else None
    )

    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("scenario.tasks must be a non-empty list")

    tasks: list[TaskSpec] = []
    for raw in raw_tasks:
        if not isinstance(raw, dict):
            raise ValueError("each task must be a mapping")
        task_id = str(raw.get("id") or "")
        if not task_id:
            raise ValueError("task.id is required")

        context_raw = raw.get("context")
        context = ContextSpec.model_validate(context_raw) if isinstance(context_raw, dict) else None
        verify_mode = str(raw.get("verify_mode") or raw.get("verify") or "commands").strip()
        judges = _parse_judges(raw.get("judges"))

        spec = TaskSpec(
            id=task_id,
            title=str(raw.get("title") or task_id),
            description=str(raw.get("description") or ""),
            deps=[str(d) for d in (raw.get("deps") or [])],
            bounty=int(raw.get("bounty") or 1),
            max_attempts=int(raw.get("max_attempts") or 3),
            verify_mode=verify_mode,
            submission_kind=str(raw.get("submission_kind") or "patch"),
            acceptance=_parse_commands(raw.get("acceptance")),
            hidden_acceptance=_parse_commands(raw.get("hidden_acceptance")),
            judges=judges,
            allowed_paths=[str(p) for p in (raw.get("allowed_paths") or ["./"])],
            files_hint=[str(p) for p in (raw.get("files_hint") or [])],
            context=context,
        )
        tasks.append(spec)

    return ScenarioSpec(
        scenario_id=scenario_id, title=title, template_dir=template_dir, tasks=tasks
    )
