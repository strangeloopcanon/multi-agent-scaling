from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from agent_economy.schemas import CommandSpec, TaskSpec, VerifyMode


DEFAULT_MANIFEST_PATH = Path("benchmarks/swebench/pilot_manifest_v1.json")


class SwebenchInstance(BaseModel):
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    test_cmd: str

    hints_text: str | None = None
    hidden_test_cmd: str | None = None
    expected_patch: str | None = None
    template_dir: str | None = None
    source: str = "swebench_lite"


def _slugify(value: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip())
    s = s.strip("_")
    return s or "task"


def load_swebench_subset(
    manifest_path: Path | str | None = None,
    *,
    limit: int | None = 20,
) -> list[SwebenchInstance]:
    path = Path(manifest_path or DEFAULT_MANIFEST_PATH)
    raw = json.loads(path.read_text(encoding="utf-8"))

    if isinstance(raw, dict):
        entries = raw.get("instances")
    else:
        entries = raw
    if not isinstance(entries, list):
        raise ValueError("SWE-bench manifest must be a list or {'instances': [...]} mapping")

    out = [SwebenchInstance.model_validate(e) for e in entries]
    if limit is not None:
        return out[: max(0, int(limit))]
    return out


def materialize_instance_workspace(
    instance: SwebenchInstance,
    *,
    workspace_root: Path,
) -> Path:
    workspace_root = Path(workspace_root)
    dst = workspace_root / _slugify(instance.instance_id)
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    src: Path | None = None
    if instance.template_dir:
        candidate = Path(instance.template_dir)
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        if candidate.exists() and candidate.is_dir():
            src = candidate

    if src is not None:
        shutil.copytree(src, dst)
    else:
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "README.md").write_text(
            "# SWE-bench workspace\n\n"
            f"- instance_id: {instance.instance_id}\n"
            f"- repo: {instance.repo}\n"
            f"- base_commit: {instance.base_commit}\n",
            encoding="utf-8",
        )

    return dst


def to_task_spec(
    instance: SwebenchInstance,
    *,
    task_id: str | None = None,
    bounty: int = 90,
    max_attempts: int = 3,
) -> TaskSpec:
    tid = task_id or f"T1_{_slugify(instance.instance_id)}"

    desc_lines = [
        "You are fixing a SWE-bench issue.",
        "",
        f"Repository: {instance.repo}",
        f"Base commit: {instance.base_commit}",
        "",
        "Problem statement:",
        instance.problem_statement.strip(),
    ]
    if instance.hints_text:
        desc_lines.extend(["", "Hints:", instance.hints_text.strip()])

    hidden = [CommandSpec(cmd=instance.hidden_test_cmd)] if instance.hidden_test_cmd else []
    return TaskSpec(
        id=tid,
        title=f"SWE-bench fix: {instance.instance_id}",
        description="\n".join(desc_lines).strip(),
        deps=[],
        bounty=int(bounty),
        max_attempts=int(max_attempts),
        verify_mode=VerifyMode.COMMANDS,
        acceptance=[CommandSpec(cmd=instance.test_cmd)],
        hidden_acceptance=hidden,
        allowed_paths=["./"],
        files_hint=[],
    )


def write_instance_scenario(
    *,
    instance: SwebenchInstance,
    scenario_path: Path,
    template_dir: Path,
    bounty: int = 90,
) -> Path:
    task = to_task_spec(instance, bounty=bounty)

    payload: dict[str, Any] = {
        "scenario_id": f"swebench_{_slugify(instance.instance_id)}",
        "title": f"SWE-bench pilot: {instance.instance_id}",
        "template_dir": str(template_dir),
        "tasks": [
            {
                "id": task.id,
                "title": task.title,
                "description": task.description,
                "deps": task.deps,
                "bounty": task.bounty,
                "max_attempts": task.max_attempts,
                "verify_mode": task.verify_mode.value,
                "acceptance": [c.cmd for c in task.acceptance],
                "hidden_acceptance": [c.cmd for c in task.hidden_acceptance],
                "allowed_paths": task.allowed_paths,
                "files_hint": task.files_hint,
            }
        ],
    }

    scenario_path.parent.mkdir(parents=True, exist_ok=True)
    scenario_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return scenario_path
