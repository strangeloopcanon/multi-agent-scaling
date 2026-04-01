from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agent_economy.engine import BidResult, ExecutionOutcome, ReadyTask
from agent_economy.json_extract import extract_json_object
from agent_economy.judges import build_unified_diff_summary, run_judges_with_workers
from agent_economy.llm_router import LLMRouter
from agent_economy.sandbox import (
    Sandbox,
    apply_unified_diff_path,
    artifact_for,
    build_patch_from_dirs,
    enforce_allowed_paths,
    parse_patch_changes,
    write_command_results_json,
    write_text_atomic,
)
from agent_economy.schemas import (
    Bid,
    DiscussionMessage,
    PaymentRule,
    SubmissionKind,
    TaskSpec,
    VerifyMode,
    VerifyStatus,
    WorkerRuntime,
    WorkerType,
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
from agent_economy.worker_specs import CommandWorkerSpec
from agent_economy.worker_refs import resolve_worker_refs

_HOST_REPO_ROOT = Path(__file__).resolve().parents[1]


class BidEnvelope(BaseModel):
    bids: list[Bid] = Field(default_factory=list)


def _worker_env(*, spec: CommandWorkerSpec) -> dict[str, str]:
    env = dict(os.environ)
    env.update({str(k): str(v) for k, v in (spec.env or {}).items()})
    return env


def _tail(text: str, *, max_chars: int = 2000) -> str:
    text = text or ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 16] + "\n... (truncated)\n"


def _load_llm_usage_sidecar(*, sandbox_dir: Path) -> dict[str, int] | None:
    usage_path = sandbox_dir / "llm_usage.json"
    if not usage_path.exists():
        return None

    try:
        payload = json.loads(usage_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    usage: dict[str, int] = {}
    for key in ("calls", "input_tokens", "output_tokens"):
        try:
            value = int(payload.get(key, 0) or 0)
        except Exception:
            return None
        if value < 0:
            return None
        usage[key] = value
    return usage


def _run_json_command(
    *,
    cmd: str,
    cwd: Path,
    env: dict[str, str],
    payload: dict[str, Any],
    timeout_sec: int | None,
) -> dict[str, Any]:
    raw_in = json.dumps(payload, ensure_ascii=False) + "\n"
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        shell=True,
        text=True,
        input=raw_in,
        capture_output=True,
        timeout=timeout_sec,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed: rc={proc.returncode}\nstdout:\n{_tail(proc.stdout)}\nstderr:\n{_tail(proc.stderr)}"
        )
    parsed = extract_json_object(proc.stdout)
    return dict(parsed)


class CommandBidder:
    def __init__(
        self,
        *,
        workspace_dir: Path,
        payment_rule: PaymentRule,
        specs: dict[str, CommandWorkerSpec],
        max_bids: int,
    ) -> None:
        self._workspace_dir = workspace_dir
        self._payment_rule = payment_rule
        self._specs = dict(specs)
        self._max_bids = int(max_bids)

    def get_bids(
        self,
        *,
        worker: WorkerRuntime,
        ready_tasks: list[ReadyTask],
        round_id: int,
        discussion_history: list[DiscussionMessage] | None = None,
    ) -> BidResult:
        _ = discussion_history
        if worker.worker_type != WorkerType.EXTERNAL_WORKER:
            return BidResult()
        spec = self._specs.get(worker.worker_id)
        if spec is None:
            return BidResult()

        ready_ids = {t.spec.id for t in ready_tasks}
        if spec.fixed_bid is not None:
            out: list[Bid] = []
            for t in ready_tasks:
                if t.spec.id not in ready_ids:
                    continue
                out.append(
                    Bid(
                        task_id=t.spec.id,
                        ask=int(spec.fixed_bid.ask),
                        self_assessed_p_success=float(spec.fixed_bid.p_success),
                        eta_minutes=int(spec.fixed_bid.eta_minutes),
                        notes=spec.fixed_bid.notes,
                    )
                )
                if len(out) >= self._max_bids:
                    break
            return BidResult(bids=out, model_ref=worker.model_ref)

        if not spec.bid_cmd:
            return BidResult()

        env = _worker_env(spec=spec)
        env.setdefault("AE_WORKER_ID", worker.worker_id)
        env.setdefault("AE_PAYMENT_RULE", self._payment_rule.value)
        env.setdefault("AE_HOST_PYTHON", sys.executable)
        env.setdefault("AE_HOST_REPO_ROOT", str(_HOST_REPO_ROOT))
        env.setdefault("INST_WORKER_ID", worker.worker_id)
        env.setdefault("INST_PAYMENT_RULE", self._payment_rule.value)
        env.setdefault("INST_HOST_PYTHON", sys.executable)
        env.setdefault("INST_HOST_REPO_ROOT", str(_HOST_REPO_ROOT))

        payload: dict[str, Any] = {
            "schema_version": 1,
            "worker_id": worker.worker_id,
            "round_id": round_id,
            "payment_rule": self._payment_rule.value,
            "max_bids": self._max_bids,
            "ready_tasks": [
                {
                    "spec": t.spec.model_dump(mode="json"),
                    "runtime": t.runtime.model_dump(mode="json"),
                }
                for t in ready_tasks
            ],
        }
        raw = _run_json_command(
            cmd=str(spec.bid_cmd),
            cwd=self._workspace_dir,
            env=env,
            payload=payload,
            timeout_sec=spec.timeout_sec,
        )
        envelope = BidEnvelope.model_validate(raw)
        bids: list[Bid] = list(envelope.bids)[: self._max_bids]

        out2: list[Bid] = []
        seen: set[str] = set()
        for bid in bids:
            if bid.task_id not in ready_ids:
                continue
            if bid.task_id in seen:
                continue
            seen.add(bid.task_id)
            out2.append(bid)
        return BidResult(bids=out2, model_ref=worker.model_ref)


@dataclass(frozen=True)
class CommandExecutorSettings:
    scrub_secrets_in_verification: bool = True
    judge_workers: list[str] = field(default_factory=list)
    judge_max_output_tokens: int = 1200
    judge_include_self: bool = True


class CommandExecutor:
    def __init__(
        self,
        *,
        workspace_dir: Path,
        run_dir: Path,
        workers: list[WorkerRuntime],
        specs: dict[str, CommandWorkerSpec],
        settings: CommandExecutorSettings | None = None,
        llm: LLMRouter | None = None,
    ) -> None:
        self._workspace_dir = workspace_dir
        self._run_dir = run_dir
        self._workers = list(workers)
        self._specs = dict(specs)
        self._settings = settings or CommandExecutorSettings()
        self._llm = llm
        self._sandbox = Sandbox(run_dir=run_dir)

    def execute(
        self,
        *,
        worker: WorkerRuntime,
        task: TaskSpec,
        bid: Bid,
        round_id: int,
        discussion_history: list[DiscussionMessage] | None = None,
    ) -> ExecutionOutcome:
        _ = discussion_history
        if worker.worker_type != WorkerType.EXTERNAL_WORKER:
            return ExecutionOutcome(status=VerifyStatus.INFRA, notes="wrong_worker_type")
        spec = self._specs.get(worker.worker_id)
        if spec is None:
            return ExecutionOutcome(status=VerifyStatus.INFRA, notes="missing_command_worker_spec")

        sandbox_dir = self._sandbox.create(
            task_id=task.id, worker_id=worker.worker_id, round_id=round_id
        )
        try:
            sandbox_rel = str(sandbox_dir.relative_to(self._run_dir))
        except Exception:
            sandbox_rel = str(sandbox_dir)

        work_dir = sandbox_dir / "workspace"
        self._sandbox.copy_workspace(workspace_dir=self._workspace_dir, sandbox_dir=work_dir)

        task_payload = {
            "schema_version": 1,
            "worker": worker.model_dump(mode="json"),
            "task": task.model_dump(mode="json"),
            "bid": bid.model_dump(mode="json"),
            "round_id": int(round_id),
        }
        task_json_path = sandbox_dir / "task.json"
        write_text_atomic(
            task_json_path, json.dumps(task_payload, ensure_ascii=False, indent=2) + "\n"
        )

        env = _worker_env(spec=spec)
        env.update(
            {
                "AE_WORKER_ID": worker.worker_id,
                "AE_TASK_ID": task.id,
                "AE_ROUND_ID": str(round_id),
                "AE_TASK_JSON": str(task_json_path.resolve()),
                "AE_SANDBOX_DIR": str(sandbox_dir.resolve()),
                "AE_WORKSPACE_DIR": str(work_dir.resolve()),
                "AE_ARTIFACTS_DIR": str(sandbox_dir.resolve()),
                "AE_HOST_PYTHON": sys.executable,
                "AE_HOST_REPO_ROOT": str(_HOST_REPO_ROOT),
                "INST_WORKER_ID": worker.worker_id,
                "INST_TASK_ID": task.id,
                "INST_ROUND_ID": str(round_id),
                "INST_TASK_JSON": str(task_json_path.resolve()),
                "INST_SANDBOX_DIR": str(sandbox_dir.resolve()),
                "INST_WORKSPACE_DIR": str(work_dir.resolve()),
                "INST_ARTIFACTS_DIR": str(sandbox_dir.resolve()),
                "INST_HOST_PYTHON": sys.executable,
                "INST_HOST_REPO_ROOT": str(_HOST_REPO_ROOT),
            }
        )

        submission_artifacts = [
            artifact_for(
                task_json_path,
                name="task.json",
                media_type="application/json",
                root=self._run_dir,
            )
        ]

        try:
            proc = subprocess.run(
                str(spec.exec_cmd),
                cwd=work_dir,
                env=env,
                shell=True,
                text=True,
                capture_output=True,
                timeout=spec.timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            out_path = sandbox_dir / "worker_timeout.txt"
            write_text_atomic(out_path, f"TimeoutExpired: {e}\n")
            submission_artifacts.append(
                artifact_for(
                    out_path,
                    name="worker_timeout.txt",
                    media_type="text/plain",
                    root=self._run_dir,
                )
            )
            return ExecutionOutcome(
                status=VerifyStatus.TIMEOUT,
                notes="exec_cmd_timeout",
                patch_artifacts=list(submission_artifacts),
                submission_artifacts=list(submission_artifacts),
                sandbox_rel=sandbox_rel,
                patch_kind="diff" if task.submission_kind == SubmissionKind.PATCH else "none",
                submission_kind=task.submission_kind,
            )

        stdout_path = sandbox_dir / "worker_stdout.txt"
        stderr_path = sandbox_dir / "worker_stderr.txt"
        write_text_atomic(stdout_path, proc.stdout or "")
        write_text_atomic(stderr_path, proc.stderr or "")
        submission_artifacts.extend(
            [
                artifact_for(
                    stdout_path,
                    name="worker_stdout.txt",
                    media_type="text/plain",
                    root=self._run_dir,
                ),
                artifact_for(
                    stderr_path,
                    name="worker_stderr.txt",
                    media_type="text/plain",
                    root=self._run_dir,
                ),
            ]
        )
        llm_usage = _load_llm_usage_sidecar(sandbox_dir=sandbox_dir)
        if llm_usage is not None:
            submission_artifacts.append(
                artifact_for(
                    sandbox_dir / "llm_usage.json",
                    name="llm_usage.json",
                    media_type="application/json",
                    root=self._run_dir,
                )
            )

        if proc.returncode != 0:
            return ExecutionOutcome(
                status=VerifyStatus.FAIL,
                notes=f"exec_cmd_failed rc={proc.returncode}",
                patch_artifacts=list(submission_artifacts),
                submission_artifacts=list(submission_artifacts),
                sandbox_rel=sandbox_rel,
                patch_kind="diff" if task.submission_kind == SubmissionKind.PATCH else "none",
                submission_kind=task.submission_kind,
                llm_usage=llm_usage,
            )

        patch_kind = "none"
        patch_touched_paths: list[str] = []
        submission_text_for_judges: str | None = None
        if task.submission_kind == SubmissionKind.PATCH:
            patch_kind = "diff"
            try:
                patch = build_patch_from_dirs(base_dir=self._workspace_dir, work_dir=work_dir)
                if not patch.patch_text.strip():
                    return ExecutionOutcome(
                        status=VerifyStatus.FAIL,
                        notes="no workspace changes produced",
                        patch_artifacts=list(submission_artifacts),
                        submission_artifacts=list(submission_artifacts),
                        sandbox_rel=sandbox_rel,
                        patch_kind=patch_kind,
                        submission_kind=task.submission_kind,
                        llm_usage=llm_usage,
                    )
                patch_touched_paths = list(patch.touched_paths)
                enforce_allowed_paths(paths=patch_touched_paths, allowed=task.allowed_paths)
            except Exception as e:
                err_path = sandbox_dir / "patch_build_error.txt"
                write_text_atomic(err_path, f"{type(e).__name__}: {e}\n")
                submission_artifacts.append(
                    artifact_for(
                        err_path,
                        name="patch_build_error.txt",
                        media_type="text/plain",
                        root=self._run_dir,
                    )
                )
                return ExecutionOutcome(
                    status=VerifyStatus.FAIL,
                    notes="patch_build_failed",
                    patch_artifacts=list(submission_artifacts),
                    submission_artifacts=list(submission_artifacts),
                    sandbox_rel=sandbox_rel,
                    patch_kind=patch_kind,
                    submission_kind=task.submission_kind,
                    llm_usage=llm_usage,
                )

            patch_path = sandbox_dir / "patch.diff"
            write_text_atomic(patch_path, patch.patch_text)
            submission_artifacts.append(
                artifact_for(
                    patch_path, name="patch.diff", media_type="text/x-diff", root=self._run_dir
                )
            )
        else:
            try:
                normalized = normalize_submission_output(
                    raw_output=proc.stdout or "",
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
                    patch_kind=patch_kind,
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
                # Only include the patch worker if it actually supports judging.
                can_self_judge = bool(spec.judge_cmd)
                if can_self_judge:
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
                        rel_paths=patch_touched_paths,
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
                        command_specs=self._specs,
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
            notes="exec_cmd_ok",
            patch_artifacts=list(submission_artifacts),
            submission_artifacts=list(submission_artifacts),
            verification_artifacts=verification_artifacts,
            sandbox_rel=sandbox_rel,
            patch_kind=patch_kind,
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
        _ = worker, task, bid, round_id
        if outcome.status != VerifyStatus.PASS:
            return outcome
        if task.submission_kind != SubmissionKind.PATCH:
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
