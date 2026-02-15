from __future__ import annotations

from pathlib import Path

from scripts.run_phase2 import build_run_matrix


def test_phase2_matrix_has_expected_48_runs() -> None:
    models = [
        "openai:gpt-5-mini",
        "openai:gpt-5.2",
        "openai:gpt-5.2-pro",
        "openai:gpt-4o",
        "anthropic:claude-sonnet-4-5",
        "google:gemini-2.5-pro",
    ]
    scenario_paths = {
        "swebench": Path("scenarios/swebench_semver_bug.yaml"),
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
