from __future__ import annotations

import os
import subprocess
import sys
import time
import json
from dataclasses import dataclass
from pathlib import Path

from agent_economy.schemas import CommandSpec, VerifyStatus


@dataclass(frozen=True)
class CommandResult:
    cmd: str
    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool
    expected_exit_codes: list[int]

    @property
    def passed(self) -> bool:
        return (not self.timed_out) and self.returncode in set(self.expected_exit_codes)


def _base_env(*, scrub_secrets: bool) -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONHASHSEED", "0")
    exe = Path(sys.executable)
    python_bin = str(exe.parent)
    if not exe.parent.exists():
        python_bin = str(exe.resolve().parent)
    path = env.get("PATH", "")
    env["PATH"] = python_bin + os.pathsep + path if path else python_bin
    pythonpath = env.get("PYTHONPATH", "")
    if pythonpath:
        normalized_entries: list[str] = []
        for raw_entry in pythonpath.split(os.pathsep):
            entry = str(raw_entry).strip()
            if not entry:
                continue
            candidate = Path(entry)
            if candidate.is_absolute():
                normalized_entries.append(str(candidate))
                continue
            normalized_entries.append(str((Path.cwd() / candidate).resolve()))
        if normalized_entries:
            env["PYTHONPATH"] = os.pathsep.join(normalized_entries)
        else:
            env.pop("PYTHONPATH", None)
    if scrub_secrets:
        for key in list(env.keys()):
            if key in {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"}:
                env.pop(key, None)
                continue
            if key.endswith("_API_KEY") or key.endswith("_TOKEN"):
                env.pop(key, None)
    return env


def run_commands(
    *,
    commands: list[CommandSpec],
    cwd: Path,
    scrub_secrets: bool = True,
) -> list[CommandResult]:
    results: list[CommandResult] = []
    for spec in commands:
        env = _base_env(scrub_secrets=scrub_secrets)
        if spec.env:
            env.update({str(k): str(v) for k, v in spec.env.items()})

        start = time.time()
        try:
            proc = subprocess.run(
                spec.cmd,
                cwd=cwd,
                env=env,
                shell=True,
                text=True,
                capture_output=True,
                timeout=spec.timeout_sec,
                check=False,
            )
            results.append(
                CommandResult(
                    cmd=spec.cmd,
                    returncode=int(proc.returncode),
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                    duration_s=time.time() - start,
                    timed_out=False,
                    expected_exit_codes=list(spec.expect_exit_codes),
                )
            )
        except subprocess.TimeoutExpired as e:
            results.append(
                CommandResult(
                    cmd=spec.cmd,
                    returncode=124,
                    stdout=str(e.stdout or ""),
                    stderr=str(e.stderr or ""),
                    duration_s=time.time() - start,
                    timed_out=True,
                    expected_exit_codes=list(spec.expect_exit_codes),
                )
            )
    return results


def all_passed(results: list[CommandResult]) -> bool:
    return all(r.passed for r in results)


def classify_command_status(
    *,
    commands: list[CommandSpec],
    results: list[CommandResult],
) -> VerifyStatus:
    if not commands:
        return VerifyStatus.PASS
    if len(results) != len(commands):
        return VerifyStatus.FAIL
    if any(r.timed_out for r in results):
        return VerifyStatus.TIMEOUT

    for spec, result in zip(commands, results):
        if int(result.returncode) in set(spec.infra_exit_codes):
            return VerifyStatus.INFRA

    return VerifyStatus.PASS if all_passed(results) else VerifyStatus.FAIL


def compact_verification_summary(
    *,
    public: list[CommandResult],
    hidden: list[CommandResult],
    max_chars: int = 1500,
    tail_chars_per_stream: int = 240,
) -> str | None:
    def _swebench_eval_stdout_summary(stdout: str) -> str | None:
        raw = (stdout or "").strip()
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        if "resolved" not in payload and "completed" not in payload:
            return None
        parts = [
            f"instance_id={payload.get('instance_id')}",
            f"completed={payload.get('completed')}",
            f"resolved={payload.get('resolved')}",
            f"returncode={payload.get('returncode')}",
        ]
        notes = payload.get("notes")
        if notes not in (None, ""):
            parts.append(f"notes={notes}")
        return " ".join(str(p) for p in parts if p is not None)

    lines: list[str] = []
    for scope, results in (("public", public), ("hidden", hidden)):
        for result in results:
            if result.passed:
                continue
            status = "TIMEOUT" if result.timed_out else f"rc={result.returncode}"
            lines.append(f"[{scope}] {status} :: {result.cmd}")
            err = (result.stderr or "").strip()
            out = (result.stdout or "").strip()
            if err:
                lines.append(f"stderr: {err[-tail_chars_per_stream:]}")
            if out:
                swe = _swebench_eval_stdout_summary(out)
                if swe:
                    lines.append(f"swebench_eval: {swe}")
                else:
                    lines.append(f"stdout: {out[-tail_chars_per_stream:]}")
            if not err and not out:
                lines.append("no_output")
    if not lines:
        return None
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    keep = max(0, int(max_chars) - 15)
    return text[:keep] + "\n...[truncated]"
