from __future__ import annotations

import sys

from agent_economy.engine import ExecutionOutcome
from agent_economy.command_workers import CommandExecutor
from agent_economy.schemas import (
    ArtifactRef,
    Bid,
    CommandSpec,
    SubmissionKind,
    TaskSpec,
    VerifyStatus,
    WorkerRuntime,
    WorkerType,
)
from agent_economy.worker_specs import CommandWorkerSpec


def test_command_executor_text_submission_passes_without_workspace_patch(tmp_path) -> None:
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    (workspace_dir / "seed.txt").write_text("seed\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    worker = WorkerRuntime(worker_id="ext", worker_type=WorkerType.EXTERNAL_WORKER)
    spec = CommandWorkerSpec(
        worker_id="ext",
        exec_cmd="printf 'External worker answer'",
        fixed_bid={"ask": 1, "p_success": 0.8, "eta_minutes": 5},
    )
    task = TaskSpec(
        id="T1",
        title="Answer from external worker",
        bounty=5,
        submission_kind=SubmissionKind.TEXT,
        verify_mode="commands",
        acceptance=[CommandSpec(cmd="test -f .agent_economy/submission.txt")],
    )
    bid = Bid(task_id="T1", ask=1, self_assessed_p_success=0.8, eta_minutes=5)

    executor = CommandExecutor(
        workspace_dir=workspace_dir,
        run_dir=run_dir,
        workers=[worker],
        specs={"ext": spec},
    )
    outcome = executor.execute(worker=worker, task=task, bid=bid, round_id=0)
    assert outcome.status == VerifyStatus.PASS
    assert outcome.submission_kind == SubmissionKind.TEXT
    assert any(a.name == "submission.txt" for a in outcome.submission_artifacts)

    integrated = executor.integrate(worker=worker, task=task, bid=bid, round_id=0, outcome=outcome)
    assert integrated.status == VerifyStatus.PASS


def test_command_executor_loads_llm_usage_sidecar(tmp_path) -> None:
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    script_path = tmp_path / "worker.py"
    script_path.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "import json",
                "import os",
                "from pathlib import Path",
                "",
                "artifacts_dir = Path(os.environ['AE_ARTIFACTS_DIR'])",
                "(artifacts_dir / 'llm_usage.json').write_text(",
                "    json.dumps({'calls': 2, 'input_tokens': 11, 'output_tokens': 7}),",
                "    encoding='utf-8',",
                ")",
                "print('External worker answer')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    worker = WorkerRuntime(
        worker_id="ext",
        worker_type=WorkerType.EXTERNAL_WORKER,
        model_ref="openai:gpt-5.4",
    )
    spec = CommandWorkerSpec(
        worker_id="ext",
        model_ref="openai:gpt-5.4",
        exec_cmd=f"{sys.executable} {script_path}",
        fixed_bid={"ask": 1, "p_success": 0.8, "eta_minutes": 5},
    )
    task = TaskSpec(
        id="T1",
        title="Answer from external worker",
        bounty=5,
        submission_kind=SubmissionKind.TEXT,
        verify_mode="commands",
        acceptance=[CommandSpec(cmd="test -f .agent_economy/submission.txt")],
    )
    bid = Bid(task_id="T1", ask=1, self_assessed_p_success=0.8, eta_minutes=5)

    executor = CommandExecutor(
        workspace_dir=workspace_dir,
        run_dir=run_dir,
        workers=[worker],
        specs={"ext": spec},
    )
    outcome = executor.execute(worker=worker, task=task, bid=bid, round_id=0)

    assert outcome.status == VerifyStatus.PASS
    assert outcome.llm_usage == {"calls": 2, "input_tokens": 11, "output_tokens": 7}
    assert any(a.name == "llm_usage.json" for a in outcome.submission_artifacts)


def test_command_executor_skips_workspace_integration_for_swebench_tasks(tmp_path) -> None:
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    (workspace_dir / "seed.txt").write_text("seed\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    sandbox_dir = run_dir / "sandboxes" / "r0_T1_ext_20260410_000000"
    sandbox_dir.mkdir(parents=True)
    patch_path = sandbox_dir / "patch.diff"
    patch_path.write_text(
        "diff --git a/seed.txt b/seed.txt\n"
        "--- a/seed.txt\n"
        "+++ b/seed.txt\n"
        "@@ -1 +1 @@\n"
        "-seed\n"
        "+patched\n",
        encoding="utf-8",
    )

    worker = WorkerRuntime(worker_id="ext", worker_type=WorkerType.EXTERNAL_WORKER)
    spec = CommandWorkerSpec(
        worker_id="ext",
        exec_cmd="printf 'unused'",
        fixed_bid={"ask": 1, "p_success": 0.8, "eta_minutes": 5},
    )
    task = TaskSpec(
        id="T1",
        title="SWE-bench task",
        bounty=5,
        submission_kind=SubmissionKind.PATCH,
        verify_mode="commands",
        acceptance=[CommandSpec(cmd="python -m agent_economy.research.swebench_eval --instance-id x")],
        allowed_paths=["./"],
    )
    bid = Bid(task_id="T1", ask=1, self_assessed_p_success=0.8, eta_minutes=5)
    outcome = ExecutionOutcome(
        status=VerifyStatus.PASS,
        patch_artifacts=[
            ArtifactRef(name="patch.diff", path="sandboxes/r0_T1_ext_20260410_000000/patch.diff")
        ],
        sandbox_rel="sandboxes/r0_T1_ext_20260410_000000",
        patch_kind="diff",
        submission_kind=SubmissionKind.PATCH,
    )

    executor = CommandExecutor(
        workspace_dir=workspace_dir,
        run_dir=run_dir,
        workers=[worker],
        specs={"ext": spec},
    )

    integrated = executor.integrate(worker=worker, task=task, bid=bid, round_id=0, outcome=outcome)
    assert integrated.status == VerifyStatus.PASS
    assert (workspace_dir / "seed.txt").read_text(encoding="utf-8") == "seed\n"
