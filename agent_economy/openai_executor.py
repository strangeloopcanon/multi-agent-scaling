from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from agent_economy.engine import ExecutionOutcome, ReadyTask
from agent_economy.judges import build_unified_diff_summary, run_judges_with_workers
from agent_economy.llm_router import LLMRouter
from agent_economy.openai_bidder import DEFAULT_PERSONAS
from agent_economy.prompts import patch_prompt, submission_prompt, system_prompt
from agent_economy.sandbox import (
    Sandbox,
    apply_file_blocks,
    apply_unified_diff,
    apply_unified_diff_path,
    artifact_for,
    build_patch_from_dirs,
    enforce_allowed_paths,
    extract_file_blocks,
    extract_git_diff,
    parse_patch_changes,
    prune_generated_workspace_artifacts,
    write_command_results_json,
    write_text_atomic,
)
from agent_economy.schemas import (
    Bid,
    DiscussionMessage,
    SubmissionKind,
    TaskRuntime,
    TaskSpec,
    VerifyMode,
    VerifyStatus,
    WorkerRuntime,
)
from agent_economy.submission import (
    normalize_submission_output,
    persist_submission,
    submission_media_type,
)
from agent_economy.verify import (
    classify_command_status,
    CommandResult,
    compact_verification_summary,
    run_commands,
)
from agent_economy.worker_refs import resolve_worker_refs
from agent_economy.worker_specs import CommandWorkerSpec

_MAX_HINT_FILES = 24
_MAX_HINT_FILE_CHARS = 12000
_MAX_HINT_TOTAL_CHARS = 48000


def _is_swebench_task(task: TaskSpec) -> bool:
    return any(
        "agent_economy.research.swebench_eval" in cmd.cmd or "swebench" in cmd.cmd.lower()
        for cmd in task.acceptance
    )


def _truncate_hint_text(*, text: str, limit: int) -> str:
    if limit <= 0:
        return "<omitted: prompt budget exhausted>"
    if len(text) <= limit:
        return text
    marker = f"\n... <truncated for prompt budget: {len(text) - limit} chars omitted>\n"
    keep = max(0, limit - len(marker))
    return text[:keep] + marker


def _read_hint_files(*, root: Path, rel_paths: list[str]) -> dict[str, str]:
    files: dict[str, str] = {}
    remaining_total = _MAX_HINT_TOTAL_CHARS
    for index, rel_path in enumerate(rel_paths):
        if index >= _MAX_HINT_FILES:
            files[rel_path] = "<omitted: too many hint files>"
            continue
        name = Path(rel_path).name
        if name == ".env" or name.startswith(".env."):
            files[rel_path] = "<redacted>"
            continue
        p = root / rel_path
        if p.exists():
            if p.is_file():
                try:
                    text = p.read_text(encoding="utf-8")
                except Exception:
                    text = "<unreadable>"
                budget = min(_MAX_HINT_FILE_CHARS, remaining_total)
                clipped = _truncate_hint_text(text=text, limit=budget)
                files[rel_path] = clipped
                remaining_total = max(0, remaining_total - len(clipped))
            elif p.is_dir():
                children = [
                    str(child.relative_to(root))
                    for child in sorted(p.rglob("*"))
                    if child.is_file()
                ]
                preview = "\n".join(children[:50])
                if len(children) > 50:
                    preview += f"\n... ({len(children) - 50} more files)"
                text = f"<directory>\n{preview}\n"
                clipped = _truncate_hint_text(text=text, limit=remaining_total)
                files[rel_path] = clipped
                remaining_total = max(0, remaining_total - len(clipped))
            else:
                files[rel_path] = "<not a regular file>"
        else:
            files[rel_path] = "<missing>"
    return files


def _strip_markdown_fences(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return raw
    raw = re.sub(r"^\s*```[^\n]*\n", "", raw)
    raw = re.sub(r"\n```+\s*$", "", raw)
    return raw.strip()


def _strip_patch_wrappers(text: str) -> str:
    raw = _strip_markdown_fences(text)
    if not raw:
        return raw
    lines = raw.splitlines()
    while lines and re.fullmatch(r"\s*<patch>\s*", lines[0]):
        lines.pop(0)
    while lines and re.fullmatch(r"\s*</patch>\s*", lines[-1]):
        lines.pop()
    return "\n".join(lines).strip()


def _looks_like_unified_diff(text: str) -> bool:
    raw = str(text or "")
    return "--- " in raw and "+++ " in raw and "@@" in raw


def _synthesize_git_headers_from_unified_diff(text: str) -> str:
    lines = str(text or "").splitlines(keepends=True)
    if any(line.startswith("diff --git ") for line in lines):
        return str(text or "")

    out: list[str] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if line.startswith("--- ") and idx + 1 < len(lines) and lines[idx + 1].startswith("+++ "):
            old_path = lines[idx][4:].strip().split("\t", 1)[0]
            new_path = lines[idx + 1][4:].strip().split("\t", 1)[0]
            out.append(f"diff --git {old_path} {new_path}\n")
            out.append(lines[idx])
            out.append(lines[idx + 1])
            idx += 2
            continue
        out.append(line)
        idx += 1
    return "".join(out)


def _extract_patch_text(raw_output: str) -> str | None:
    raw = str(raw_output or "")
    stripped = _strip_patch_wrappers(raw)
    if "diff --git " in raw:
        return extract_git_diff(raw)
    if "diff --git " in stripped:
        return extract_git_diff(stripped)

    cleaned = _strip_markdown_fences(raw)
    if "diff --git " in cleaned:
        return extract_git_diff(cleaned)

    cleaned = _strip_patch_wrappers(cleaned)
    if "diff --git " in cleaned:
        return extract_git_diff(cleaned)

    if _looks_like_unified_diff(cleaned):
        return _synthesize_git_headers_from_unified_diff(cleaned)

    return None


@dataclass(frozen=True)
class ExecutorSettings:
    max_patch_output_tokens: int = 6000
    scrub_secrets_in_verification: bool = True
    judge_workers: list[str] = field(default_factory=list)
    judge_max_output_tokens: int = 1200
    judge_include_self: bool = True


class OpenAIExecutor:
    def __init__(
        self,
        *,
        llm: LLMRouter,
        workspace_dir: Path,
        run_dir: Path,
        workers: list[WorkerRuntime],
        command_specs: dict[str, CommandWorkerSpec] | None = None,
        settings: ExecutorSettings | None = None,
    ) -> None:
        self._llm = llm
        self._workspace_dir = workspace_dir
        self._run_dir = run_dir
        self._workers = list(workers)
        self._command_specs = dict(command_specs or {})
        self._settings = settings or ExecutorSettings()
        self._sandbox = Sandbox(run_dir=run_dir)

    def execute(
        self,
        *,
        worker: WorkerRuntime,
        task: TaskSpec,
        bid: Bid,
        round_id: int,
        discussion_history: list[DiscussionMessage],
    ) -> ExecutionOutcome:
        _ = bid
        if not worker.model_ref:
            return ExecutionOutcome(status=VerifyStatus.INFRA, notes="missing model_ref")

        sandbox_dir = self._sandbox.create(
            task_id=task.id, worker_id=worker.worker_id, round_id=round_id
        )
        try:
            sandbox_rel = str(sandbox_dir.relative_to(self._run_dir))
        except Exception:
            sandbox_rel = str(sandbox_dir)
        work_dir = sandbox_dir / "workspace"
        self._sandbox.copy_workspace(workspace_dir=self._workspace_dir, sandbox_dir=work_dir)
        prune_generated_workspace_artifacts(workspace_dir=work_dir)

        hint_files = _read_hint_files(root=work_dir, rel_paths=list(task.files_hint))
        ready = ReadyTask(
            spec=task,
            runtime=TaskRuntime(
                task_id=task.id, bounty_current=task.bounty, bounty_original=task.bounty
            ),
        )

        persona = DEFAULT_PERSONAS.get(worker.worker_id)
        sys = system_prompt(worker=worker, persona=None if persona is None else persona.persona)
        if task.submission_kind == SubmissionKind.PATCH:
            user = patch_prompt(task=ready, files=hint_files, discussion_history=discussion_history)
        else:
            user = submission_prompt(
                task=ready, files=hint_files, discussion_history=discussion_history
            )

        write_text_atomic(sandbox_dir / "prompt_system.txt", sys)
        write_text_atomic(sandbox_dir / "prompt_user.txt", user)

        submission_artifacts = [
            artifact_for(
                sandbox_dir / "prompt_system.txt",
                name="prompt_system.txt",
                media_type="text/plain",
                root=self._run_dir,
            ),
            artifact_for(
                sandbox_dir / "prompt_user.txt",
                name="prompt_user.txt",
                media_type="text/plain",
                root=self._run_dir,
            ),
        ]

        try:
            raw, usage = self._llm.call_text(
                model_ref=worker.model_ref,
                system=sys,
                user=user,
                max_output_tokens=self._settings.max_patch_output_tokens,
            )
        except Exception as e:
            err_path = sandbox_dir / "llm_error.txt"
            write_text_atomic(err_path, f"{type(e).__name__}: {e}\n")
            submission_artifacts.append(
                artifact_for(
                    err_path,
                    name="llm_error.txt",
                    media_type="text/plain",
                    root=self._run_dir,
                )
            )
            return ExecutionOutcome(
                status=VerifyStatus.INFRA,
                notes="llm_call_failed",
                patch_artifacts=list(submission_artifacts),
                submission_artifacts=list(submission_artifacts),
                sandbox_rel=sandbox_rel,
                patch_kind="none",
                submission_kind=task.submission_kind,
            )

        llm_usage = {
            "calls": int(getattr(usage, "calls", 0) or 0),
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        }

        write_text_atomic(sandbox_dir / "model_raw.txt", raw)
        submission_artifacts.append(
            artifact_for(
                sandbox_dir / "model_raw.txt",
                name="model_raw.txt",
                media_type="text/plain",
                root=self._run_dir,
            )
        )

        # Build submission artifacts.
        applied_kind = "none"
        touched: list[str] = []
        submission_text_for_judges: str | None = None
        if task.submission_kind == SubmissionKind.PATCH:
            try:
                patch_text = _extract_patch_text(raw)
                cleaned = _strip_markdown_fences(raw)
                if patch_text is not None:
                    diff_changes = parse_patch_changes(patch_text)
                    touched = sorted(
                        {
                            p
                            for ch in diff_changes
                            for p in [ch.old_path, ch.new_path]
                            if p is not None
                        }
                    )
                    enforce_allowed_paths(paths=touched, allowed=task.allowed_paths)
                    patch_path = apply_unified_diff(
                        patch_text=patch_text, cwd=work_dir, patch_path=sandbox_dir / "patch.diff"
                    )
                    submission_artifacts.append(
                        artifact_for(
                            patch_path,
                            name="patch.diff",
                            media_type="text/x-diff",
                            root=self._run_dir,
                        )
                    )
                    applied_kind = "diff"
                elif "BEGIN_FILE " in cleaned:
                    files = extract_file_blocks(cleaned)
                    touched = sorted(files.keys())
                    enforce_allowed_paths(paths=touched, allowed=task.allowed_paths)
                    apply_file_blocks(files=files, cwd=work_dir)
                    fileblocks_path = sandbox_dir / "patch_files.json"
                    write_text_atomic(
                        fileblocks_path, json.dumps(files, ensure_ascii=False, indent=2) + "\n"
                    )
                    submission_artifacts.append(
                        artifact_for(
                            fileblocks_path,
                            name="patch_files.json",
                            media_type="application/json",
                            root=self._run_dir,
                        )
                    )
                    patch = build_patch_from_dirs(base_dir=self._workspace_dir, work_dir=work_dir)
                    if not patch.patch_text.strip():
                        return ExecutionOutcome(
                            status=VerifyStatus.FAIL,
                            notes="no workspace changes produced",
                            patch_artifacts=list(submission_artifacts),
                            submission_artifacts=list(submission_artifacts),
                            sandbox_rel=sandbox_rel,
                            patch_kind="files",
                            submission_kind=task.submission_kind,
                            llm_usage=llm_usage,
                        )
                    enforce_allowed_paths(
                        paths=list(patch.touched_paths), allowed=task.allowed_paths
                    )
                    patch_path = sandbox_dir / "patch.diff"
                    write_text_atomic(patch_path, patch.patch_text)
                    submission_artifacts.append(
                        artifact_for(
                            patch_path,
                            name="patch.diff",
                            media_type="text/x-diff",
                            root=self._run_dir,
                        )
                    )
                    applied_kind = "files"
                else:
                    return ExecutionOutcome(
                        status=VerifyStatus.FAIL,
                        notes="no patch found (expected diff --git or BEGIN_FILE blocks)",
                        patch_artifacts=list(submission_artifacts),
                        submission_artifacts=list(submission_artifacts),
                        sandbox_rel=sandbox_rel,
                        patch_kind="none",
                        submission_kind=task.submission_kind,
                        llm_usage=llm_usage,
                    )
            except Exception as e:
                err_path = sandbox_dir / "patch_apply_error.txt"
                msg = f"{type(e).__name__}: {e}\n"
                if isinstance(e, subprocess.CalledProcessError):
                    if e.stderr:
                        msg += f"\n--- stderr ---\n{e.stderr}\n"
                    if e.stdout:
                        msg += f"\n--- stdout ---\n{e.stdout}\n"
                write_text_atomic(err_path, msg)
                submission_artifacts.append(
                    artifact_for(
                        err_path,
                        name="patch_apply_error.txt",
                        media_type="text/plain",
                        root=self._run_dir,
                    )
                )
                return ExecutionOutcome(
                    status=VerifyStatus.FAIL,
                    notes="patch apply failed",
                    patch_artifacts=list(submission_artifacts),
                    submission_artifacts=list(submission_artifacts),
                    sandbox_rel=sandbox_rel,
                    patch_kind=applied_kind,
                    submission_kind=task.submission_kind,
                    llm_usage=llm_usage,
                )
        else:
            try:
                normalized = normalize_submission_output(
                    raw_output=raw,
                    kind=task.submission_kind,
                )
                submission_path, _ = persist_submission(
                    sandbox_dir=sandbox_dir,
                    work_dir=work_dir,
                    normalized_output=normalized,
                    kind=task.submission_kind,
                )
                submission_artifacts.append(
                    artifact_for(
                        submission_path,
                        name=submission_path.name,
                        media_type=submission_media_type(kind=task.submission_kind),
                        root=self._run_dir,
                    )
                )
                submission_text_for_judges = normalized
            except Exception as e:
                err_path = sandbox_dir / "submission_error.txt"
                write_text_atomic(err_path, f"{type(e).__name__}: {e}\n")
                submission_artifacts.append(
                    artifact_for(
                        err_path,
                        name="submission_error.txt",
                        media_type="text/plain",
                        root=self._run_dir,
                    )
                )
                return ExecutionOutcome(
                    status=VerifyStatus.FAIL,
                    notes="submission_parse_failed",
                    patch_artifacts=list(submission_artifacts),
                    submission_artifacts=list(submission_artifacts),
                    sandbox_rel=sandbox_rel,
                    patch_kind="none",
                    submission_kind=task.submission_kind,
                    llm_usage=llm_usage,
                )

        public: list[CommandResult] = []
        hidden: list[CommandResult] = []
        status = VerifyStatus.PASS
        verification_summary: str | None = None

        if task.verify_mode == VerifyMode.MANUAL:
            if task.acceptance:
                public = run_commands(
                    commands=list(task.acceptance),
                    cwd=work_dir,
                    scrub_secrets=self._settings.scrub_secrets_in_verification,
                )
            status = VerifyStatus.MANUAL_REVIEW
        else:
            if task.acceptance:
                public = run_commands(
                    commands=list(task.acceptance),
                    cwd=work_dir,
                    scrub_secrets=self._settings.scrub_secrets_in_verification,
                )
                status = classify_command_status(commands=list(task.acceptance), results=public)

            if status == VerifyStatus.PASS and task.hidden_acceptance:
                hidden = run_commands(
                    commands=list(task.hidden_acceptance),
                    cwd=work_dir,
                    scrub_secrets=self._settings.scrub_secrets_in_verification,
                )
                status = classify_command_status(
                    commands=list(task.hidden_acceptance),
                    results=hidden,
                )

            if status in {VerifyStatus.FAIL, VerifyStatus.TIMEOUT, VerifyStatus.INFRA}:
                verification_summary = compact_verification_summary(public=public, hidden=hidden)

        verify_path = sandbox_dir / "verify.json"
        write_command_results_json(verify_path, public=public, hidden=hidden)
        verification_artifacts = [
            artifact_for(
                verify_path, name="verify.json", media_type="application/json", root=self._run_dir
            )
        ]

        if status == VerifyStatus.PASS and task.verify_mode == VerifyMode.JUDGES:
            judge_spec = task.judges
            refs = (
                list(judge_spec.workers)
                if judge_spec is not None and judge_spec.workers
                else list(self._settings.judge_workers)
            )
            include_self = (
                bool(judge_spec.include_self)
                if judge_spec is not None
                else self._settings.judge_include_self
            )
            min_passes = None if judge_spec is None else judge_spec.min_passes

            judge_workers = resolve_worker_refs(refs, workers=self._workers)
            if include_self:
                judge_workers = [worker] + [
                    w for w in judge_workers if w.worker_id != worker.worker_id
                ]

            if not judge_workers:
                status = VerifyStatus.INFRA
            else:
                required_passes = (
                    int(min_passes) if min_passes is not None else (len(judge_workers) // 2 + 1)
                )
                required_passes = max(1, min(required_passes, len(judge_workers)))

                diff_text = "(non-patch submission)"
                if task.submission_kind == SubmissionKind.PATCH:
                    diff_text = build_unified_diff_summary(
                        workspace_dir=self._workspace_dir,
                        sandbox_dir=work_dir,
                        rel_paths=touched,
                    )
                    diff_path = sandbox_dir / "diff_for_judges.diff"
                    write_text_atomic(diff_path, diff_text)
                    verification_artifacts.append(
                        artifact_for(
                            diff_path,
                            name="diff_for_judges.diff",
                            media_type="text/x-diff",
                            root=self._run_dir,
                        )
                    )

                try:
                    judge_status, judge_calls = run_judges_with_workers(
                        llm=self._llm,
                        judge_workers=judge_workers,
                        command_specs=self._command_specs,
                        task=task,
                        public=public,
                        hidden=hidden,
                        diff_text=diff_text,
                        submission_kind=task.submission_kind,
                        submission_text=submission_text_for_judges,
                        required_passes=required_passes,
                        max_output_tokens=self._settings.judge_max_output_tokens,
                        cwd=work_dir,
                    )
                except Exception as e:
                    err_path = sandbox_dir / "judges_error.txt"
                    write_text_atomic(err_path, f"{type(e).__name__}: {e}\n")
                    verification_artifacts.append(
                        artifact_for(
                            err_path,
                            name="judges_error.txt",
                            media_type="text/plain",
                            root=self._run_dir,
                        )
                    )
                    status = VerifyStatus.INFRA
                else:
                    judges_path = sandbox_dir / "judges.json"
                    passes = sum(1 for c in judge_calls if c.decision.verdict == "PASS")
                    payload = {
                        "status": judge_status.value,
                        "judge_workers": [w.worker_id for w in judge_workers],
                        "required_passes": required_passes,
                        "votes_total": len(judge_calls),
                        "passes": passes,
                        "decisions": [
                            {
                                "worker_id": c.worker_id,
                                "worker_type": c.worker_type,
                                **c.decision.model_dump(),
                            }
                            for c in list(judge_calls)
                        ],
                    }
                    write_text_atomic(
                        judges_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
                    )
                    verification_artifacts.append(
                        artifact_for(
                            judges_path,
                            name="judges.json",
                            media_type="application/json",
                            root=self._run_dir,
                        )
                    )
                    for call in judge_calls:
                        safe = "".join(
                            ch if (ch.isalnum() or ch in "-._") else "_" for ch in call.worker_id
                        )
                        raw_path = sandbox_dir / f"judge_{safe}.txt"
                        write_text_atomic(raw_path, call.raw_text)
                        verification_artifacts.append(
                            artifact_for(
                                raw_path,
                                name=raw_path.name,
                                media_type="text/plain",
                                root=self._run_dir,
                            )
                        )
                    status = judge_status

        return ExecutionOutcome(
            status=status,
            notes=f"patch_applied={applied_kind}",
            patch_artifacts=list(submission_artifacts),
            submission_artifacts=list(submission_artifacts),
            verification_artifacts=verification_artifacts,
            sandbox_rel=sandbox_rel,
            patch_kind=applied_kind,
            submission_kind=task.submission_kind,
            submission_preview=submission_text_for_judges,
            llm_usage=llm_usage,
            verification_summary=verification_summary,
        )

    def integrate(
        self,
        *,
        worker: WorkerRuntime,
        task: TaskSpec,
        bid: Bid,
        round_id: int,
        outcome: ExecutionOutcome,
    ) -> ExecutionOutcome:
        _ = worker, bid, round_id
        if outcome.status != VerifyStatus.PASS:
            return outcome
        if task.submission_kind != SubmissionKind.PATCH:
            return outcome
        if _is_swebench_task(task):
            # SWE-bench tasks are independent. Keep the shared workspace pristine.
            return outcome

        by_name = {a.name: a for a in list(outcome.patch_artifacts)}
        a = by_name.get("patch.diff")
        if a is None or not a.path:
            return ExecutionOutcome(
                status=VerifyStatus.INFRA,
                notes="missing_patch_diff_for_integration",
                patch_artifacts=list(outcome.patch_artifacts),
                submission_artifacts=list(outcome.submission_artifacts),
                verification_artifacts=list(outcome.verification_artifacts),
                sandbox_rel=outcome.sandbox_rel,
                patch_kind=outcome.patch_kind,
                submission_kind=outcome.submission_kind,
                llm_usage=outcome.llm_usage,
            )

        patch_path = self._run_dir / a.path
        sandbox_dir = self._run_dir / (outcome.sandbox_rel or "")

        try:
            enforce_allowed_paths(
                paths=[
                    p
                    for ch in parse_patch_changes(patch_path.read_text(encoding="utf-8"))
                    for p in (ch.old_path, ch.new_path)
                    if p is not None
                ],
                allowed=task.allowed_paths,
            )
            apply_unified_diff_path(patch_path=patch_path, cwd=self._workspace_dir)
        except Exception as e:
            err_path = sandbox_dir / "integrate_error.txt"
            write_text_atomic(err_path, f"{type(e).__name__}: {e}\n")
            verification_artifacts = list(outcome.verification_artifacts)
            verification_artifacts.append(
                artifact_for(
                    err_path,
                    name="integrate_error.txt",
                    media_type="text/plain",
                    root=self._run_dir,
                )
            )
            return ExecutionOutcome(
                status=VerifyStatus.INFRA,
                notes="workspace_apply_failed",
                patch_artifacts=list(outcome.patch_artifacts),
                submission_artifacts=list(outcome.submission_artifacts),
                verification_artifacts=verification_artifacts,
                sandbox_rel=outcome.sandbox_rel,
                patch_kind=outcome.patch_kind,
                submission_kind=outcome.submission_kind,
                llm_usage=outcome.llm_usage,
            )

        return outcome
