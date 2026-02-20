from __future__ import annotations

import json
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from agent_economy.sandbox import parse_patch_changes
from agent_economy.schemas import CommandSpec, TaskSpec, VerifyMode


DEFAULT_MANIFEST_PATH = Path("benchmarks/swebench/pilot_manifest_v1.json")
DEFAULT_PHASE2_MANIFEST_PATH = Path("benchmarks/swebench/phase2_93_manifest_v1.json")
DEFAULT_DATASET_NAME = "princeton-nlp/SWE-bench_Lite"
DEFAULT_SPLIT = "test"


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

    fail_to_pass: list[str] = Field(default_factory=list)
    pass_to_pass: list[str] = Field(default_factory=list)
    test_patch: str | None = None
    patch: str | None = None
    dataset_name: str = DEFAULT_DATASET_NAME
    split: str = DEFAULT_SPLIT


class Phase2TaskManifest(BaseModel):
    name: str
    source: str
    dataset_name: str = DEFAULT_DATASET_NAME
    split: str = DEFAULT_SPLIT
    instance_ids: list[str] = Field(default_factory=list)


def _slugify(value: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip())
    s = s.strip("_")
    return s or "task"


def _git(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )


def _import_datasets():
    try:
        from datasets import load_dataset  # type: ignore
    except Exception as e:  # pragma: no cover - explicit runtime guidance
        raise RuntimeError(
            "datasets dependency is required. Install research extras: `uv sync --extra research`."
        ) from e
    return load_dataset


def _paths_from_patch_text(patch_text: str) -> list[str]:
    if not patch_text.strip():
        return []
    try:
        changes = parse_patch_changes(patch_text)
    except Exception:
        return []
    paths = [p for c in changes for p in (c.old_path, c.new_path) if p]
    return list(dict.fromkeys(str(p) for p in paths))


def _paths_from_test_directives(directives: list[str], *, root: Path) -> list[str]:
    out: list[str] = []
    for raw in directives:
        token = str(raw).split("::", 1)[0].strip()
        if not token:
            continue
        if token.startswith("./"):
            token = token[2:]
        if token.endswith(".py"):
            p = root / token
            if p.exists() and p.is_file():
                out.append(token)
    return list(dict.fromkeys(out))


def _paths_from_problem_statement(*, problem_statement: str, root: Path) -> list[str]:
    text = str(problem_statement or "")
    if not text:
        return []

    out: list[str] = []

    def _add_if_exists(path_like: str) -> None:
        raw = str(path_like).strip().replace("\\", "/")
        if not raw or not raw.endswith(".py"):
            return

        for marker in ("site-packages/", "dist-packages/"):
            if marker in raw:
                raw = raw.split(marker, 1)[1]
                break

        if raw.startswith("./"):
            raw = raw[2:]
        raw = raw.lstrip("/")
        if not raw:
            return

        parts = Path(raw).parts
        for idx in range(len(parts)):
            rel = Path(*parts[idx:])
            if not str(rel) or str(rel) == ".":
                continue
            candidate = root / rel
            if candidate.exists() and candidate.is_file():
                out.append(str(rel))
                return

    for match in re.findall(r'File "([^"\n]+\.py)"', text):
        _add_if_exists(match)

    for match in re.findall(r"`([^`\n]+\.py)`", text):
        _add_if_exists(match)

    for match in re.findall(r"([A-Za-z0-9_./-]+\.py)", text):
        if "/" not in match:
            continue
        _add_if_exists(match)

    return list(dict.fromkeys(out))


def _source_candidates_from_test_paths(*, test_paths: list[str], root: Path) -> list[str]:
    out: list[str] = []
    for test_path in test_paths:
        rel = Path(str(test_path).strip())
        parts = list(rel.parts)
        if "tests" not in parts:
            continue
        tests_idx = parts.index("tests")
        module_dir = Path(*parts[:tests_idx]) if tests_idx > 0 else Path(".")
        file_name = rel.name

        if file_name.startswith("test_") and file_name.endswith(".py"):
            candidate = module_dir / file_name[len("test_") :]
            candidate_path = root / candidate
            if candidate_path.exists() and candidate_path.is_file():
                out.append(str(candidate))

        init_file = module_dir / "__init__.py"
        init_path = root / init_file
        if init_path.exists() and init_path.is_file():
            out.append(str(init_file))

        module_root = root / module_dir
        if module_root.exists() and module_root.is_dir():
            added = 0
            for p in sorted(module_root.glob("*.py")):
                if p.name.startswith("test_"):
                    continue
                out.append(str(p.relative_to(root)))
                added += 1
                if added >= 6:
                    break

    return list(dict.fromkeys(out))


def load_phase2_manifest(manifest_path: Path | str | None = None) -> Phase2TaskManifest:
    path = Path(manifest_path or DEFAULT_PHASE2_MANIFEST_PATH)
    return Phase2TaskManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_phase2_task_ids(manifest_path: Path | str | None = None) -> list[str]:
    manifest = load_phase2_manifest(manifest_path)
    ids = [str(x).strip() for x in manifest.instance_ids if str(x).strip()]
    seen: set[str] = set()
    out: list[str] = []
    for instance_id in ids:
        if instance_id in seen:
            continue
        seen.add(instance_id)
        out.append(instance_id)
    return out


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


def load_swebench_lite_instances_by_id(
    *,
    instance_ids: list[str],
    dataset_name: str = DEFAULT_DATASET_NAME,
    split: str = DEFAULT_SPLIT,
) -> list[SwebenchInstance]:
    if not instance_ids:
        return []

    load_dataset = _import_datasets()
    dataset = load_dataset(dataset_name, split=split)

    by_id: dict[str, dict[str, Any]] = {}
    requested = set(instance_ids)
    for row in dataset:
        instance_id = str(row.get("instance_id") or "").strip()
        if not instance_id or instance_id not in requested:
            continue
        by_id[instance_id] = {
            "instance_id": instance_id,
            "repo": str(row.get("repo") or ""),
            "base_commit": str(row.get("base_commit") or ""),
            "problem_statement": str(row.get("problem_statement") or "").strip(),
            "test_cmd": "python -m pytest -q target/tests",
            "hidden_test_cmd": "python -m pytest -q target/tests_hidden",
            "fail_to_pass": _decode_test_directives(row.get("FAIL_TO_PASS")),
            "pass_to_pass": _decode_test_directives(row.get("PASS_TO_PASS")),
            "test_patch": str(row.get("test_patch") or ""),
            "patch": str(row.get("patch") or ""),
            "hints_text": str(row.get("hints_text") or "").strip() or None,
            "source": "swebench_lite_real",
            "dataset_name": dataset_name,
            "split": split,
        }
        if len(by_id) == len(requested):
            break

    missing = [instance_id for instance_id in instance_ids if instance_id not in by_id]
    if missing:
        raise ValueError(f"missing SWE-bench instances: {missing}")

    return [SwebenchInstance.model_validate(by_id[instance_id]) for instance_id in instance_ids]


def _decode_test_directives(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(v) for v in parsed if str(v).strip()]


def ensure_repo_cache(*, repo: str, repo_cache_root: Path) -> Path:
    repo_cache_root.mkdir(parents=True, exist_ok=True)
    cache_dir = repo_cache_root / _slugify(repo)
    remote = f"https://github.com/{repo}.git"
    if not cache_dir.exists():
        _git("clone", "--filter=blob:none", remote, str(cache_dir))
    else:
        _git("fetch", "--all", "--tags", cwd=cache_dir)
    return cache_dir


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


def materialize_real_instance_workspace(
    instance: SwebenchInstance,
    *,
    workspace_root: Path,
    repo_cache_root: Path,
) -> Path:
    workspace_root = Path(workspace_root)
    dst = workspace_root / _slugify(instance.instance_id)
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)

    cache_dir = ensure_repo_cache(repo=instance.repo, repo_cache_root=repo_cache_root)

    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tmp:
        tar_path = Path(tmp.name)
    try:
        _git("archive", "-o", str(tar_path), instance.base_commit, cwd=cache_dir)
        with tarfile.open(tar_path) as tf:
            tf.extractall(path=dst)
    finally:
        tar_path.unlink(missing_ok=True)

    if instance.test_patch:
        from agent_economy.sandbox import apply_unified_diff_text

        apply_unified_diff_text(patch_text=instance.test_patch, cwd=dst)

    return dst


def suggest_files_hint(
    *,
    instance: SwebenchInstance,
    workspace_dir: Path,
    max_files: int = 24,
) -> list[str]:
    hints: list[str] = []
    root = Path(workspace_dir)

    test_paths: list[str] = []
    test_paths.extend(_paths_from_test_directives(instance.fail_to_pass, root=root))
    test_paths.extend(_paths_from_test_directives(instance.pass_to_pass, root=root))
    test_paths = list(dict.fromkeys(test_paths))

    hints.extend(test_paths)
    hints.extend(_source_candidates_from_test_paths(test_paths=test_paths, root=root))
    hints.extend(
        _paths_from_problem_statement(problem_statement=instance.problem_statement, root=root)
    )

    for path in _paths_from_patch_text(instance.test_patch or ""):
        p = root / path
        if p.exists() and p.is_file():
            hints.append(path)
    for path in _paths_from_patch_text(instance.patch or ""):
        p = root / path
        if p.exists() and p.is_file():
            hints.append(path)

    fallback_candidates = [
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "tox.ini",
        "pytest.ini",
        "README.md",
    ]
    for rel in fallback_candidates:
        p = root / rel
        if p.exists() and p.is_file():
            hints.append(rel)

    deduped: list[str] = []
    seen: set[str] = set()
    for rel in hints:
        rel = str(rel).strip()
        if not rel or rel in seen:
            continue
        seen.add(rel)
        deduped.append(rel)
        if len(deduped) >= max(1, int(max_files)):
            break

    if not deduped:
        for rel in ["README.md", "target/tests"]:
            p = root / rel
            if p.exists():
                deduped.append(rel)

    return deduped


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


def to_phase2_task_spec(
    instance: SwebenchInstance,
    *,
    files_hint: list[str],
    bounty: int = 90,
    max_attempts: int = 2,
    timeout_sec: int = 1800,
) -> TaskSpec:
    desc_lines = [
        "You are fixing a SWE-bench Lite issue.",
        "",
        f"Instance: {instance.instance_id}",
        f"Repository: {instance.repo}",
        f"Base commit: {instance.base_commit}",
        "",
        "Problem statement:",
        instance.problem_statement.strip(),
    ]
    if instance.hints_text:
        desc_lines.extend(["", "Hints:", instance.hints_text.strip()])

    eval_cmd = (
        "python -m agent_economy.research.swebench_eval "
        f"--instance-id {instance.instance_id} "
        "--patch-file ../patch.diff "
        f"--dataset-name {instance.dataset_name} "
        f"--split {instance.split} "
        f"--timeout-sec {int(timeout_sec)}"
    )

    return TaskSpec(
        id=instance.instance_id,
        title=f"SWE-bench fix: {instance.instance_id}",
        description="\n".join(desc_lines).strip(),
        deps=[],
        bounty=int(bounty),
        max_attempts=int(max_attempts),
        verify_mode=VerifyMode.COMMANDS,
        submission_kind="patch",
        acceptance=[CommandSpec(cmd=eval_cmd, infra_exit_codes=[2])],
        hidden_acceptance=[],
        allowed_paths=["./"],
        files_hint=list(files_hint),
    )


def phase2_planner_goal(
    *,
    instance: SwebenchInstance,
    task: TaskSpec,
    max_tasks: int,
) -> str:
    acceptance_cmd = task.acceptance[0].cmd if task.acceptance else ""
    hints = "\n".join(f"- {p}" for p in list(task.files_hint)[:32])
    if not hints:
        hints = "- (none)"
    lines = [
        "Decompose this SWE-bench repair into a task DAG for a market of coding agents.",
        "",
        f"Instance: {instance.instance_id}",
        f"Repository: {instance.repo}",
        f"Base commit: {instance.base_commit}",
        "",
        "Problem statement:",
        instance.problem_statement.strip(),
        "",
        "Known relevant files:",
        hints,
        "",
        "Constraints:",
        f"- max_tasks: {int(max_tasks)}",
        "- You may choose any DAG structure that helps solve the issue.",
        "- Prefer early subtasks that diagnose and localize the defect.",
        "- Include precise files_hint per subtask.",
        "- The final subtask MUST produce a patch verified by this command:",
        f"  {acceptance_cmd}",
    ]
    if instance.hints_text:
        lines.extend(["", "Additional hints:", instance.hints_text.strip()])
    return "\n".join(lines).strip()


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
                "acceptance": [c.model_dump(mode="json") for c in task.acceptance],
                "hidden_acceptance": [c.model_dump(mode="json") for c in task.hidden_acceptance],
                "allowed_paths": task.allowed_paths,
                "files_hint": task.files_hint,
            }
        ],
    }

    scenario_path.parent.mkdir(parents=True, exist_ok=True)
    scenario_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return scenario_path


def write_phase2_instance_scenario(
    *,
    instance: SwebenchInstance,
    scenario_path: Path,
    template_dir: Path,
    files_hint: list[str],
    bounty: int = 90,
    max_attempts: int = 2,
    timeout_sec: int = 1800,
) -> Path:
    task = to_phase2_task_spec(
        instance,
        files_hint=files_hint,
        bounty=bounty,
        max_attempts=max_attempts,
        timeout_sec=timeout_sec,
    )

    payload: dict[str, Any] = {
        "scenario_id": f"swebench_phase2_{_slugify(instance.instance_id)}",
        "title": f"SWE-bench Phase II: {instance.instance_id}",
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
                "submission_kind": task.submission_kind.value,
                "acceptance": [c.model_dump(mode="json") for c in task.acceptance],
                "hidden_acceptance": [c.model_dump(mode="json") for c in task.hidden_acceptance],
                "allowed_paths": task.allowed_paths,
                "files_hint": task.files_hint,
            }
        ],
    }

    scenario_path.parent.mkdir(parents=True, exist_ok=True)
    scenario_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return scenario_path
