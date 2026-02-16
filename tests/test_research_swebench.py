from __future__ import annotations

import json
from pathlib import Path

from agent_economy.research.swebench import (
    SwebenchInstance,
    load_swebench_subset,
    materialize_instance_workspace,
    to_task_spec,
)


def test_load_swebench_subset_and_limit(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "instances": [
                    {
                        "instance_id": "a",
                        "repo": "org/repo-a",
                        "base_commit": "abc",
                        "problem_statement": "fix A",
                        "test_cmd": "pytest -q",
                    },
                    {
                        "instance_id": "b",
                        "repo": "org/repo-b",
                        "base_commit": "def",
                        "problem_statement": "fix B",
                        "test_cmd": "pytest -q",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    rows = load_swebench_subset(manifest, limit=1)
    assert len(rows) == 1
    assert rows[0].instance_id == "a"


def test_materialize_instance_workspace_copies_template(tmp_path: Path) -> None:
    src = tmp_path / "template"
    src.mkdir(parents=True)
    (src / "README.md").write_text("hello", encoding="utf-8")

    inst = SwebenchInstance(
        instance_id="x-1",
        repo="org/repo",
        base_commit="abc123",
        problem_statement="fix it",
        test_cmd="pytest -q",
        template_dir=str(src),
    )

    workspace = materialize_instance_workspace(inst, workspace_root=tmp_path / "workspaces")
    assert workspace.exists()
    assert (workspace / "README.md").read_text(encoding="utf-8") == "hello"


def test_to_task_spec_roundtrip() -> None:
    inst = SwebenchInstance(
        instance_id="my-case",
        repo="org/repo",
        base_commit="abc123",
        problem_statement="fix comparator",
        test_cmd="pytest -q target/tests",
        hidden_test_cmd="pytest -q target/tests_hidden",
    )
    task = to_task_spec(inst)
    assert task.id.startswith("T1_")
    assert task.verify_mode.value == "commands"
    assert task.acceptance[0].cmd == "pytest -q target/tests"
    assert task.hidden_acceptance[0].cmd == "pytest -q target/tests_hidden"
