from __future__ import annotations

import json
from pathlib import Path

import yaml

from agent_economy.ledger import HashChainedLedger
from agent_economy.schemas import EventType
from agent_economy.research.calibration import PromptStrategy
from scripts.run_phase1 import (
    _apply_outcomes_to_rows,
    _first_attempt_outcomes,
    _load_phase1_inputs,
    _load_phase1_tasks,
    _run_calibration,
    _safe_model_tag,
    _write_swebench_phase1_scenario,
)


def test_load_phase1_tasks_combines_swebench_and_synthesis(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "instances": [
                    {
                        "instance_id": "inst-1",
                        "repo": "org/repo",
                        "base_commit": "abc",
                        "problem_statement": "fix bug",
                        "test_cmd": "pytest -q",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    template_dir = tmp_path / "tmpl"
    template_dir.mkdir(parents=True)

    scenario = tmp_path / "synthesis.yaml"
    scenario.write_text(
        "\n".join(
            [
                "scenario_id: synth",
                "title: synth",
                f"template_dir: {template_dir}",
                "tasks:",
                "  - id: DR1",
                "    title: task",
                "    description: desc",
                "    bounty: 10",
                "    verify_mode: judges",
                "    submission_kind: text",
                "    acceptance: []",
            ]
        ),
        encoding="utf-8",
    )

    tasks = _load_phase1_tasks(swe_manifest=manifest, swe_limit=20, synthesis_scenario=scenario)
    assert len(tasks) == 2
    benchmarks = sorted(t["benchmark"] for t in tasks)
    assert benchmarks == ["swebench", "synthesis"]


def test_safe_model_tag_sanitizes_provider_and_slashes() -> None:
    assert (
        _safe_model_tag("google:models/gemini-3-pro-preview")
        == "google_models_gemini-3-pro-preview"
    )
    assert _safe_model_tag("openai:gpt-5.2-pro-2025-12-11") == "openai_gpt-5.2-pro-2025-12-11"


def test_first_attempt_outcomes_uses_terminal_attempt_and_censors_nonterminal(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    ledger = HashChainedLedger(run_dir / "ledger.jsonl")
    run_id = "phase1-test"

    ledger.append(EventType.RUN_CREATED, run_id=run_id, round_id=0, payload={"payment_rule": "ask"})
    ledger.append(
        EventType.TASK_COMPLETED,
        run_id=run_id,
        round_id=1,
        payload={
            "task_id": "DR1",
            "worker_id": "w1",
            "success": True,
            "verify_status": "PASS",
        },
    )
    # Second completion for same task should be ignored for first-attempt labeling.
    ledger.append(
        EventType.TASK_COMPLETED,
        run_id=run_id,
        round_id=2,
        payload={
            "task_id": "DR1",
            "worker_id": "w1",
            "success": False,
            "verify_status": "FAIL",
        },
    )
    ledger.append(
        EventType.TASK_COMPLETED,
        run_id=run_id,
        round_id=1,
        payload={
            "task_id": "DR2",
            "worker_id": "w1",
            "success": False,
            "verify_status": "INFRA",
        },
    )

    outcomes = _first_attempt_outcomes(run_dir=run_dir, task_ids=["DR1", "DR2", "DR3"])
    assert outcomes["DR1"] == {"outcome": 1, "outcome_status": "pass", "attempted": True}
    assert outcomes["DR2"] == {"outcome": None, "outcome_status": "infra", "attempted": True}
    assert outcomes["DR3"] == {
        "outcome": None,
        "outcome_status": "not_attempted",
        "attempted": False,
    }


def test_write_swebench_phase1_scenario_emits_subset(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "instances": [
                    {
                        "instance_id": "inst-1",
                        "repo": "org/repo",
                        "base_commit": "abc",
                        "problem_statement": "fix bug",
                        "test_cmd": "pytest -q",
                        "template_dir": "templates/swebench_semver",
                    },
                    {
                        "instance_id": "inst-2",
                        "repo": "org/repo2",
                        "base_commit": "def",
                        "problem_statement": "fix bug 2",
                        "test_cmd": "pytest -q",
                        "template_dir": "templates/swebench_semver",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    scenario_path = _write_swebench_phase1_scenario(
        output_root=tmp_path,
        manifest_path=manifest,
        swe_limit=1,
    )
    assert scenario_path is not None
    payload = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
    assert payload["scenario_id"] == "swebench_phase1_subset"
    assert len(payload["tasks"]) == 1
    assert str(payload["tasks"][0]["id"]).startswith("T1_")


def test_load_phase1_inputs_external_covered_lite(monkeypatch, tmp_path: Path) -> None:
    def _fake_builder(*, model_refs, task_limit, leaderboard_url):
        assert model_refs == ["openai:gpt-5.2-2025-12-11"]
        assert task_limit == 30
        assert leaderboard_url == "https://example.com/leaderboard.json"
        tasks = [
            {
                "benchmark": "swebench",
                "task_id": "astropy__astropy-12907",
                "title": "SWE-bench fix: astropy__astropy-12907",
                "description": "desc",
                "acceptance": ["FAIL_TO_PASS: t1"],
            }
        ]
        labels = {
            ("swebench", "astropy__astropy-12907", "openai:gpt-5.2-2025-12-11"): {
                "outcome": 1,
                "outcome_status": "pass",
                "attempted": True,
                "outcome_source": "external_exact",
            }
        }
        manifest = {"selected_task_count": 1}
        return tasks, labels, manifest

    monkeypatch.setattr("scripts.run_phase1.build_external_covered_lite_phase1", _fake_builder)

    tasks, labels, manifest = _load_phase1_inputs(
        task_source="external_covered_lite",
        swe_manifest=tmp_path / "unused.json",
        swe_limit=20,
        synthesis_scenario=tmp_path / "unused.yaml",
        model_refs=["openai:gpt-5.2-2025-12-11"],
        tasks_limit=30,
        external_evidence_url="https://example.com/leaderboard.json",
    )
    assert len(tasks) == 1
    assert len(labels) == 1
    assert manifest == {"selected_task_count": 1}


def test_apply_outcomes_to_rows_sets_joined_fields() -> None:
    rows = [
        {
            "benchmark": "swebench",
            "task_id": "django__django-15104",
            "model_ref": "openai:gpt-5.2-2025-12-11",
            "p_success": 0.8,
        }
    ]
    outcomes = {
        ("swebench", "django__django-15104", "openai:gpt-5.2-2025-12-11"): {
            "outcome": 1,
            "outcome_status": "pass",
            "attempted": True,
            "outcome_source": "external_exact",
            "external_row_name": "GPT-5.2 (2025-12-11)",
        }
    }
    out = _apply_outcomes_to_rows(rows=rows, outcomes=outcomes)
    assert out[0]["outcome"] == 1
    assert out[0]["outcome_status"] == "pass"
    assert out[0]["attempted"] is True
    assert out[0]["outcome_source"] == "external_exact"
    assert out[0]["external_row_name"] == "GPT-5.2 (2025-12-11)"


def test_run_calibration_writes_quality_checks_on_cadence(tmp_path: Path) -> None:
    tasks = [
        {
            "benchmark": "swebench",
            "task_id": "T1",
            "title": "t1",
            "description": "d1",
            "acceptance": [],
        },
        {
            "benchmark": "swebench",
            "task_id": "T2",
            "title": "t2",
            "description": "d2",
            "acceptance": [],
        },
        {
            "benchmark": "swebench",
            "task_id": "T3",
            "title": "t3",
            "description": "d3",
            "acceptance": [],
        },
    ]
    quality_path = tmp_path / "quality_checks.jsonl"
    records = _run_calibration(
        execute_calibration=False,
        llm=None,
        models=["openai:gpt-5.2-2025-12-11"],
        tasks=tasks,
        strategies=[PromptStrategy.DIRECT],
        calibration_concurrency=1,
        check_every=2,
        quality_checks_path=quality_path,
    )
    assert len(records) == 3
    lines = [json.loads(line) for line in quality_path.read_text(encoding="utf-8").splitlines()]
    assert [row["type"] for row in lines] == ["checkpoint", "final"]
    assert lines[0]["completed"] == 2
    assert lines[1]["completed"] == 3
