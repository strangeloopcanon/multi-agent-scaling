from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from agent_economy.llm_router import LLMRouter
from agent_economy.planner import PlannedTask
from agent_economy.schemas import (
    CommandSpec,
    EventType,
    SubmissionKind,
    TaskRuntime,
    TaskSpec,
    WorkerRuntime,
)
from scripts.run_phase2 import (
    _all_tasks_terminal_or_exhausted,
    _load_worker_calibration_context,
    _run_prepared_mode,
    _planner_subtasks_to_specs,
    _rewrite_task_execution_timeouts,
    _write_csv,
    build_run_matrix,
    load_prepared_task_specs,
)


def test_phase2_matrix_has_expected_48_runs() -> None:
    models = [
        "openai:gpt-5-mini-2025-08-07",
        "openai:gpt-5.2-2025-12-11",
        "openai:gpt-5.2-pro-2025-12-11",
        "anthropic:claude-sonnet-4-5-20250929",
        "anthropic:claude-opus-4-5-20251101",
        "google:models/gemini-3-pro-preview",
    ]
    scenario_paths = {
        "swebench": Path("scenarios/swebench_pilot_v1.yaml"),
        "synthesis": Path("scenarios/synthesis_reasoning_pilot.yaml"),
    }

    matrix = build_run_matrix(
        benchmarks=["swebench", "synthesis"],
        models=models,
        repeats=3,
        scenario_paths=scenario_paths,
    )

    assert len(matrix) == 48

    solo = [r for r in matrix if r.mode == "solo"]
    market = [r for r in matrix if r.mode == "market"]
    assert len(solo) == 36
    assert len(market) == 12

    market_modes = sorted({r.settlement_mode for r in market})
    assert market_modes == ["direct_penalty", "reputation"]


def test_load_prepared_task_specs_slices_offset_limit(tmp_path: Path) -> None:
    manifest = tmp_path / "prepared_manifest.json"
    rows = [
        {"instance_id": "a", "scenario_path": str(tmp_path / "a.yaml")},
        {"instance_id": "b", "scenario_path": str(tmp_path / "b.yaml")},
        {"instance_id": "c", "scenario_path": str(tmp_path / "c.yaml")},
    ]
    manifest.write_text(json.dumps({"rows": rows}), encoding="utf-8")

    specs = load_prepared_task_specs(
        prepared_manifest=manifest,
        task_offset=1,
        task_limit=1,
    )
    assert len(specs) == 1
    assert specs[0].instance_id == "b"
    assert specs[0].scenario_path == tmp_path / "b.yaml"


def test_load_worker_calibration_context_hard_prior_mode(tmp_path: Path) -> None:
    calibration = tmp_path / "baseline.jsonl"
    rows = [
        {
            "task_id": "repo__one",
            "model_ref": "google:models/gemini-3-pro-preview",
            "strategy": "direct",
            "outcome": 1,
            "p_success": 0.9,
            "estimated_tokens_total": 1000,
        },
        {
            "task_id": "repo__two",
            "model_ref": "google:models/gemini-3-pro-preview",
            "strategy": "direct",
            "outcome": 0,
            "p_success": 0.9,
            "estimated_tokens_total": 1000,
        },
        {
            "task_id": "repo__three",
            "model_ref": "google:models/gemini-3-pro-preview",
            "strategy": "direct",
            "outcome": 1,
            "p_success": 0.8,
            "estimated_tokens_total": 1000,
        },
    ]
    calibration.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    context = _load_worker_calibration_context(
        calibration_source=calibration,
        workers=[
            WorkerRuntime(
                worker_id="gemini-3-pro-preview",
                model_ref="google:models/gemini-3-pro-preview",
            )
        ],
        task_id="repo__three",
        pricing_by_model={},
        calibration_style="hard_prior_v1",
    )

    lines = context["gemini-3-pro-preview"]
    joined = "\n".join(lines)
    assert "Across 2 earlier held-out tasks:" in joined
    assert "For this bid, start from p_success = 0.15." in joined
    assert "ask must be at least 50% of bounty." in joined
    assert "return no bid" in joined


def test_rewrite_task_execution_timeouts_updates_swebench_eval_commands() -> None:
    task = TaskSpec(
        id="demo",
        title="demo",
        bounty=1,
        deps=[],
        acceptance=[
            CommandSpec(
                cmd=(
                    "python -m agent_economy.research.swebench_eval "
                    "--instance-id demo --timeout-sec 1800"
                )
            ),
            CommandSpec(cmd="pytest -q"),
        ],
        hidden_acceptance=[
            CommandSpec(
                cmd=(
                    "python -m agent_economy.research.swebench_eval "
                    "--instance-id demo --timeout-sec=1800"
                )
            )
        ],
    )

    rewritten = _rewrite_task_execution_timeouts(
        tasks=[task],
        execution_timeout_seconds=900.0,
    )

    assert rewritten[0].acceptance[0].cmd.endswith("--timeout-sec 900")
    assert rewritten[0].acceptance[0].timeout_sec == 1230
    assert rewritten[0].acceptance[1].cmd == "pytest -q"
    assert rewritten[0].acceptance[1].timeout_sec is None
    assert rewritten[0].hidden_acceptance[0].cmd.endswith("--timeout-sec=900")
    assert rewritten[0].hidden_acceptance[0].timeout_sec == 1230


def test_planner_subtasks_to_specs_maps_planned_final_to_single_patch_task() -> None:
    base = TaskSpec(
        id="instance-1",
        title="final fix",
        bounty=90,
        deps=[],
        submission_kind=SubmissionKind.PATCH,
        verify_mode="commands",
        acceptance=[
            CommandSpec(cmd="python -m agent_economy.research.swebench_eval --instance-id x")
        ],
        files_hint=["lib/core.py"],
    )
    planned = [
        PlannedTask(
            id="T1", title="Diagnose", description="diag", deps=[], files_hint=["lib/core.py"]
        ),
        PlannedTask(
            id="T2",
            title="Final fix",
            description="fix",
            deps=["T1"],
            files_hint=["lib/core.py"],
            acceptance=["python -m agent_economy.research.swebench_eval --instance-id x"],
        ),
    ]
    tasks = _planner_subtasks_to_specs(plan_tasks=planned, goal="goal", base_task=base)
    patch_tasks = [t for t in tasks if t.submission_kind == SubmissionKind.PATCH]
    assert len(patch_tasks) == 1
    assert patch_tasks[0].id == "instance-1"
    text_tasks = [t for t in tasks if t.submission_kind == SubmissionKind.TEXT]
    assert text_tasks
    assert text_tasks[0].acceptance[0].cmd == "test -f .agent_economy/submission.txt"


def test_planner_subtasks_assigns_majority_bounty_to_final_patch() -> None:
    base = TaskSpec(
        id="instance-2",
        title="final fix",
        bounty=100,
        deps=[],
        submission_kind=SubmissionKind.PATCH,
        verify_mode="commands",
        acceptance=[
            CommandSpec(cmd="python -m agent_economy.research.swebench_eval --instance-id y")
        ],
        files_hint=["lib/core.py"],
    )
    planned = [
        PlannedTask(id="T1", title="Diagnose", description="diag", deps=[]),
        PlannedTask(
            id="T2",
            title="Fix",
            description="fix",
            deps=["T1"],
            acceptance=["python -m agent_economy.research.swebench_eval --instance-id y"],
        ),
        PlannedTask(id="T3", title="Validate", description="validate", deps=["T2"]),
    ]
    tasks = _planner_subtasks_to_specs(plan_tasks=planned, goal="goal", base_task=base)
    patch = [t for t in tasks if t.submission_kind == SubmissionKind.PATCH][0]
    text = [t for t in tasks if t.submission_kind == SubmissionKind.TEXT]
    assert patch.bounty > max(t.bounty for t in text)
    assert sum(int(t.bounty) for t in tasks) == 100


def test_write_csv_allows_rows_with_extra_keys(tmp_path: Path) -> None:
    out = tmp_path / "rows.csv"
    _write_csv(
        out,
        [
            {"task_id": "a", "status": "done"},
            {"task_id": "b", "status": "failed", "error": "planner failed"},
        ],
    )
    text = out.read_text(encoding="utf-8")
    assert "task_id,status,error" in text
    assert "b,failed,planner failed" in text


def test_all_tasks_terminal_or_exhausted_counts_infra_attempts() -> None:
    state = SimpleNamespace(
        tasks={
            "T1": TaskRuntime(
                task_id="T1",
                bounty_current=90,
                bounty_original=90,
                status="TODO",
                fail_count=0,
            )
        }
    )
    task_specs = {
        "T1": TaskSpec(
            id="T1",
            title="one",
            bounty=90,
            max_attempts=2,
            deps=[],
            acceptance=[CommandSpec(cmd="true")],
        )
    }

    one_infra = [
        SimpleNamespace(
            type=EventType.TASK_COMPLETED,
            payload={"task_id": "T1", "verify_status": "INFRA"},
        )
    ]
    assert not _all_tasks_terminal_or_exhausted(
        state=state, task_specs=task_specs, events=one_infra
    )

    two_infra = [
        SimpleNamespace(
            type=EventType.TASK_COMPLETED,
            payload={"task_id": "T1", "verify_status": "INFRA"},
        ),
        SimpleNamespace(
            type=EventType.TASK_COMPLETED,
            payload={"task_id": "T1", "verify_status": "INFRA"},
        ),
    ]
    assert _all_tasks_terminal_or_exhausted(state=state, task_specs=task_specs, events=two_infra)


def test_run_prepared_mode_supports_central_router(tmp_path: Path, monkeypatch) -> None:
    manifest = tmp_path / "prepared_manifest.json"
    scenario = tmp_path / "scenario.yaml"
    scenario.write_text("name: demo\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "rows": [
                    {"instance_id": "demo-1", "scenario_path": str(scenario)},
                ]
            }
        ),
        encoding="utf-8",
    )

    workers_path = tmp_path / "workers.json"
    workers_path.write_text(
        json.dumps(
            [
                {
                    "worker_id": "gpt-5.2",
                    "model_ref": "openai:gpt-5.2-2025-12-11",
                }
            ]
        ),
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    def fake_require_credentials(models: list[str]) -> None:
        captured["credential_models"] = list(models)

    def fake_llm_router_for_workers(*, settings, workers):
        _ = settings, workers
        return LLMRouter()

    def fake_run_one_spec(**kwargs):
        captured["spec_mode"] = kwargs["spec"].mode
        captured["assignment_policy_type"] = type(kwargs["assignment_policy"]).__name__
        captured["extra_run_config"] = dict(kwargs["extra_run_config"])
        run_dir = Path(kwargs["run_dir"])
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "run_config.json").write_text("{}", encoding="utf-8")
        return run_dir

    def fake_summaries(*, run_dirs):
        return [
            {
                "run_dir": str(run_dirs[0]),
                "pass_rate": 1.0,
                "tasks_done": 1,
                "tasks_total": 1,
                "tokens": {"total": 123},
                "penalties": {"total": 4.0},
                "workers": [],
            }
        ]

    monkeypatch.setattr("scripts.run_phase2._require_credentials", fake_require_credentials)
    monkeypatch.setattr("scripts.run_phase2._llm_router_for_workers", fake_llm_router_for_workers)
    monkeypatch.setattr("scripts.run_phase2._run_one_spec", fake_run_one_spec)
    monkeypatch.setattr("scripts.run_phase2.summarize_market_runs", fake_summaries)

    args = SimpleNamespace(
        task_manifest=manifest,
        prepared_mode="central_router",
        task_offset=0,
        task_limit=0,
        isolate_state=True,
        output_root=tmp_path / "out",
        execute=True,
        workers=workers_path,
        settlement_mode="direct_penalty",
        rounds=2,
        concurrency=1,
        bid_timeout_seconds=30.0,
        execution_timeout_seconds=60.0,
        require_bid_barrier=True,
        dag_mode="off",
        force_bids=False,
        retry_score_penalty_fraction=0.0,
        exclude_failed_workers=True,
        replan=False,
        planner_max_tasks=8,
        resume=False,
        overwrite=False,
        continue_on_error=False,
        check_every=25,
        router_model_ref="openai:gpt-5.2-pro-2025-12-11",
    )

    _run_prepared_mode(args, models=["openai:gpt-5.2-2025-12-11"])

    final_summary = json.loads(
        (args.output_root / "final_summary.json").read_text(encoding="utf-8")
    )
    assert captured["spec_mode"] == "central_router"
    assert captured["assignment_policy_type"] == "CentralRouterPolicy"
    assert captured["extra_run_config"] == {
        "prepared_mode": "central_router",
        "router_model_ref": "openai:gpt-5.2-pro-2025-12-11",
    }
    assert captured["credential_models"] == [
        "openai:gpt-5.2-2025-12-11",
        "openai:gpt-5.2-pro-2025-12-11",
    ]
    assert final_summary["mode"] == "prepared_central_router_only"
