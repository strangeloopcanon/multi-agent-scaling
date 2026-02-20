from __future__ import annotations

from pathlib import Path

from agent_economy.scenario import load_scenario


def test_load_scenario_respects_max_attempts(tmp_path: Path) -> None:
    tpl = tmp_path / "template"
    tpl.mkdir(parents=True)
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text(
        "\n".join(
            [
                "scenario_id: s1",
                "title: test",
                f"template_dir: {tpl}",
                "tasks:",
                "  - id: T1",
                "    title: one",
                "    bounty: 10",
                "    max_attempts: 2",
                "    verify_mode: commands",
                "    acceptance:",
                "      - cmd: 'true'",
            ]
        ),
        encoding="utf-8",
    )

    scenario = load_scenario(scenario_path)
    assert scenario.tasks[0].max_attempts == 2
