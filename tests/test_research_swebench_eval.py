from __future__ import annotations

import json
import signal
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_economy.research.swebench_eval import (
    _build_run_id,
    _load_report,
    _load_summary,
    evaluate_with_harness,
)


def test_evaluate_with_harness_requires_patch_when_not_gold(tmp_path) -> None:
    result = evaluate_with_harness(
        instance_id="django__django-1",
        dataset_name="princeton-nlp/SWE-bench_Lite",
        split="test",
        timeout_sec=30,
        work_dir=tmp_path,
        run_id_prefix="ut",
        patch_text=None,
        gold=False,
    )
    assert result.completed is False
    assert result.resolved is False
    assert result.notes == "missing_patch_text"
    assert result.returncode == 2


def test_load_report_canonical_path(tmp_path) -> None:
    run_id = _build_run_id(
        prefix="ut", instance_id="django__django-1", patch_text="diff", gold=False
    )
    report_dir = tmp_path / "logs" / "run_evaluation" / run_id / "sub"
    report_dir.mkdir(parents=True)
    payload = {"django__django-1": {"resolved": True}}
    (report_dir / "report.json").write_text(json.dumps(payload))

    report, path = _load_report(work_dir=tmp_path, run_id=run_id)
    assert report == payload
    assert path is not None


def test_load_report_run_id_root(tmp_path) -> None:
    """Report written under {work_dir}/{run_id}/report.json."""
    run_id = _build_run_id(
        prefix="ut", instance_id="astropy__astropy-1", patch_text="p", gold=False
    )
    alt_dir = tmp_path / run_id
    alt_dir.mkdir(parents=True)
    payload = {"astropy__astropy-1": {"resolved": False}}
    (alt_dir / "report.json").write_text(json.dumps(payload))

    report, path = _load_report(work_dir=tmp_path, run_id=run_id)
    assert report == payload
    assert path is not None


def test_load_report_workdir_root(tmp_path) -> None:
    """Report written directly in work_dir."""
    run_id = "nonexistent_run_id"
    payload = {"matplotlib__matplotlib-1": {"resolved": True}}
    (tmp_path / "report.json").write_text(json.dumps(payload))

    report, path = _load_report(work_dir=tmp_path, run_id=run_id)
    assert report == payload
    assert path is not None


def test_load_report_returns_none_when_missing(tmp_path) -> None:
    report, path = _load_report(work_dir=tmp_path, run_id="no_such_run")
    assert report is None
    assert path is None


def test_load_report_skips_invalid_json(tmp_path) -> None:
    """Invalid JSON files are skipped, not returned as errors."""
    run_id = _build_run_id(prefix="ut", instance_id="django__django-2", patch_text="x", gold=False)
    report_dir = tmp_path / "logs" / "run_evaluation" / run_id
    report_dir.mkdir(parents=True)
    (report_dir / "report.json").write_text("not json {{{")

    report, path = _load_report(work_dir=tmp_path, run_id=run_id)
    assert report is None
    assert path is None


def test_load_summary_resolved_ids(tmp_path) -> None:
    run_id = _build_run_id(prefix="ut", instance_id="sympy__sympy-1", patch_text="p", gold=False)
    payload = {
        "resolved_ids": ["sympy__sympy-1"],
        "unresolved_ids": [],
        "error_ids": [],
        "incomplete_ids": [],
    }
    path = tmp_path / f"agent-economy-market.{run_id}.json"
    path.write_text(json.dumps(payload))

    summary, summary_path = _load_summary(work_dir=tmp_path, run_id=run_id)
    assert summary == payload
    assert summary_path == path


def test_load_summary_error_ids(tmp_path) -> None:
    run_id = _build_run_id(prefix="ut", instance_id="sympy__sympy-2", patch_text="p", gold=False)
    payload = {
        "resolved_ids": [],
        "unresolved_ids": [],
        "error_ids": ["sympy__sympy-2"],
        "incomplete_ids": [],
    }
    path = tmp_path / f"agent-economy-market.{run_id}.json"
    path.write_text(json.dumps(payload))

    summary, summary_path = _load_summary(work_dir=tmp_path, run_id=run_id)
    assert summary == payload
    assert summary_path == path


def test_load_summary_returns_none_when_missing(tmp_path) -> None:
    summary, summary_path = _load_summary(work_dir=tmp_path, run_id="missing")
    assert summary is None
    assert summary_path is None


def test_evaluate_with_harness_uses_summary_fallback_resolved(monkeypatch, tmp_path) -> None:
    instance_id = "django__django-111"
    run_id = _build_run_id(prefix="ut", instance_id=instance_id, patch_text="diff", gold=False)
    summary_path = tmp_path / f"agent-economy-market.{run_id}.json"
    summary = {
        "resolved_ids": [instance_id],
        "unresolved_ids": [],
        "error_ids": [],
        "incomplete_ids": [],
    }

    monkeypatch.setattr(
        "agent_economy.research.swebench_eval._run_harness_command",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        "agent_economy.research.swebench_eval._load_report",
        lambda **kwargs: (None, None),
    )
    monkeypatch.setattr(
        "agent_economy.research.swebench_eval._load_summary",
        lambda **kwargs: (summary, summary_path),
    )

    result = evaluate_with_harness(
        instance_id=instance_id,
        dataset_name="princeton-nlp/SWE-bench_Lite",
        split="test",
        timeout_sec=30,
        work_dir=tmp_path,
        run_id_prefix="ut",
        patch_text="diff",
        gold=False,
    )
    assert result.completed is True
    assert result.resolved is True
    assert result.returncode == 0
    assert result.notes is None
    assert result.report_path == str(summary_path)


def test_evaluate_with_harness_uses_summary_fallback_error(monkeypatch, tmp_path) -> None:
    instance_id = "django__django-222"
    run_id = _build_run_id(prefix="ut", instance_id=instance_id, patch_text="diff", gold=False)
    summary_path = tmp_path / f"agent-economy-market.{run_id}.json"
    summary = {
        "resolved_ids": [],
        "unresolved_ids": [],
        "error_ids": [instance_id],
        "incomplete_ids": [],
    }

    monkeypatch.setattr(
        "agent_economy.research.swebench_eval._run_harness_command",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        "agent_economy.research.swebench_eval._load_report",
        lambda **kwargs: (None, None),
    )
    monkeypatch.setattr(
        "agent_economy.research.swebench_eval._load_summary",
        lambda **kwargs: (summary, summary_path),
    )

    result = evaluate_with_harness(
        instance_id=instance_id,
        dataset_name="princeton-nlp/SWE-bench_Lite",
        split="test",
        timeout_sec=30,
        work_dir=tmp_path,
        run_id_prefix="ut",
        patch_text="diff",
        gold=False,
    )
    assert result.completed is False
    assert result.resolved is False
    assert result.returncode == 2
    assert result.notes == "evaluation_error"
    assert result.report_path == str(summary_path)


def test_evaluate_with_harness_runs_from_neutral_runner_dir(monkeypatch, tmp_path) -> None:
    calls: dict[str, object] = {}

    def _fake_run(*, cmd, cwd, timeout_sec):
        calls["cmd"] = cmd
        calls["cwd"] = cwd
        calls["timeout_sec"] = timeout_sec
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("agent_economy.research.swebench_eval._run_harness_command", _fake_run)
    monkeypatch.setattr(
        "agent_economy.research.swebench_eval._load_report",
        lambda **kwargs: ({"psf__requests-2317": {"resolved": False}}, tmp_path / "report.json"),
    )

    result = evaluate_with_harness(
        instance_id="psf__requests-2317",
        dataset_name="princeton-nlp/SWE-bench_Lite",
        split="test",
        timeout_sec=30,
        work_dir=tmp_path,
        run_id_prefix="ut",
        patch_text="diff",
        gold=False,
    )

    assert result.completed is True
    assert result.resolved is False
    assert calls["cwd"] == tmp_path / ".ae_harness_runner"
    assert Path(calls["cwd"]).is_dir()
    cmd = calls["cmd"]
    assert isinstance(cmd, list)
    report_dir_index = cmd.index("--report_dir")
    assert cmd[report_dir_index + 1] == str(tmp_path)
    assert calls["timeout_sec"] == 30


def test_evaluate_with_harness_marks_timeout(monkeypatch, tmp_path) -> None:
    def _fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=["python", "-m", "swebench.harness.run_evaluation"],
            timeout=330,
            output="partial stdout",
            stderr="partial stderr",
        )

    monkeypatch.setattr("agent_economy.research.swebench_eval._run_harness_command", _fake_run)
    monkeypatch.setattr(
        "agent_economy.research.swebench_eval._load_report",
        lambda **kwargs: (None, None),
    )

    result = evaluate_with_harness(
        instance_id="psf__requests-2317",
        dataset_name="princeton-nlp/SWE-bench_Lite",
        split="test",
        timeout_sec=30,
        work_dir=tmp_path,
        run_id_prefix="ut",
        patch_text="diff",
        gold=False,
    )

    assert result.completed is False
    assert result.resolved is False
    assert result.returncode == 2
    assert result.notes == "harness_timeout"

    harness_files = list(tmp_path.glob("harness_*.json"))
    assert len(harness_files) == 1
    payload = json.loads(harness_files[0].read_text())
    assert payload["timed_out"] is True
    assert payload["stdout"] == "partial stdout"
    assert payload["stderr"] == "partial stderr"


def test_run_harness_command_kills_process_group_on_timeout(monkeypatch, tmp_path) -> None:
    events: list[tuple[str, object]] = []

    class FakeProcess:
        pid = 1234
        returncode = None

        def communicate(self, timeout=None):
            events.append(("communicate", timeout))
            if timeout is not None:
                raise subprocess.TimeoutExpired(
                    cmd=["python", "-m", "swebench.harness.run_evaluation"],
                    timeout=timeout,
                )
            self.returncode = -9
            return ("after kill stdout", "after kill stderr")

    monkeypatch.setattr(
        "agent_economy.research.swebench_eval.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    monkeypatch.setattr(
        "agent_economy.research.swebench_eval.os.killpg",
        lambda pid, sig: events.append(("killpg", (pid, sig))),
    )

    from agent_economy.research.swebench_eval import _run_harness_command

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        _run_harness_command(
            cmd=["python", "-m", "swebench.harness.run_evaluation"],
            cwd=tmp_path,
            timeout_sec=30,
        )

    assert events[0] == ("communicate", 330)
    assert events[1] == ("killpg", (1234, signal.SIGKILL))
    assert events[2] == ("communicate", None)
    assert exc_info.value.output == "after kill stdout"
    assert exc_info.value.stderr == "after kill stderr"
