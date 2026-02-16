from __future__ import annotations

from pathlib import Path

from scripts.run_phase2 import build_run_matrix


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
