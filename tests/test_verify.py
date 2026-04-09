from __future__ import annotations

import os
import signal
import subprocess

import pytest

from agent_economy import verify
from agent_economy.schemas import CommandSpec, VerifyStatus


def test_base_env_prepends_sys_executable_parent(tmp_path, monkeypatch) -> None:
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    exe = venv_bin / "python"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(verify.sys, "executable", str(exe))
    monkeypatch.setenv("PATH", "/usr/bin")
    env = verify._base_env(scrub_secrets=False)
    first = env["PATH"].split(os.pathsep, 1)[0]
    assert first == str(venv_bin)


def test_base_env_normalizes_relative_pythonpath(tmp_path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.chdir(repo_root)
    monkeypatch.setenv("PYTHONPATH", f".{os.pathsep}src")

    env = verify._base_env(scrub_secrets=False)

    entries = env["PYTHONPATH"].split(os.pathsep)
    assert entries == [str(repo_root.resolve()), str((repo_root / "src").resolve())]


def test_compact_verification_summary_includes_failed_streams() -> None:
    failed = verify.CommandResult(
        cmd="pytest -q",
        returncode=1,
        stdout="",
        stderr="E assert 1 == 2",
        duration_s=1.0,
        timed_out=False,
        expected_exit_codes=[0],
    )
    timeout = verify.CommandResult(
        cmd="python long.py",
        returncode=124,
        stdout="partial output",
        stderr="",
        duration_s=10.0,
        timed_out=True,
        expected_exit_codes=[0],
    )
    summary = verify.compact_verification_summary(public=[failed], hidden=[timeout])
    assert summary is not None
    assert "[public] rc=1 :: pytest -q" in summary
    assert "E assert 1 == 2" in summary
    assert "[hidden] TIMEOUT :: python long.py" in summary
    assert "partial output" in summary


def test_classify_command_status_treats_configured_exit_code_as_infra() -> None:
    cmd = CommandSpec(cmd="run", expect_exit_codes=[0], infra_exit_codes=[2])
    result = verify.CommandResult(
        cmd="run",
        returncode=2,
        stdout="",
        stderr="",
        duration_s=0.1,
        timed_out=False,
        expected_exit_codes=[0],
    )
    status = verify.classify_command_status(commands=[cmd], results=[result])
    assert status == VerifyStatus.INFRA


def test_classify_command_status_timeout_precedence_over_infra() -> None:
    cmd = CommandSpec(cmd="run", expect_exit_codes=[0], infra_exit_codes=[124])
    result = verify.CommandResult(
        cmd="run",
        returncode=124,
        stdout="",
        stderr="",
        duration_s=3.0,
        timed_out=True,
        expected_exit_codes=[0],
    )
    status = verify.classify_command_status(commands=[cmd], results=[result])
    assert status == VerifyStatus.TIMEOUT


def test_command_spec_rejects_overlapping_expect_and_infra_exit_codes() -> None:
    with pytest.raises(ValueError):
        CommandSpec(cmd="run", expect_exit_codes=[2], infra_exit_codes=[2])


def test_run_command_kills_process_group_on_timeout(tmp_path, monkeypatch) -> None:
    events: list[tuple[str, object]] = []

    class FakeProcess:
        pid = 4321
        returncode = None
        calls = 0

        def communicate(self, timeout=None):
            events.append(("communicate", timeout))
            self.calls += 1
            if timeout is not None and self.calls <= 2:
                raise subprocess.TimeoutExpired(cmd="sleep 60", timeout=timeout)
            self.returncode = -9
            return ("after kill stdout", "after kill stderr")

    monkeypatch.setattr(
        verify.subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    monkeypatch.setattr(
        verify.os,
        "killpg",
        lambda pid, sig: events.append(("killpg", (pid, sig))),
    )

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        verify._run_command(
            cmd="sleep 60",
            cwd=tmp_path,
            env={"PATH": os.environ.get("PATH", "")},
            timeout_sec=30,
        )

    assert events[0] == ("communicate", 30)
    assert events[1] == ("killpg", (4321, signal.SIGTERM))
    assert events[2] == ("communicate", 5)
    assert events[3] == ("killpg", (4321, signal.SIGKILL))
    assert events[4] == ("communicate", None)
    assert exc_info.value.output == "after kill stdout"
    assert exc_info.value.stderr == "after kill stderr"
