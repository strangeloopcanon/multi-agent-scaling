from __future__ import annotations

import os

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
