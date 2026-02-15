from __future__ import annotations

import json
from pathlib import Path

from scripts.run_phase1 import _load_phase1_tasks


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
