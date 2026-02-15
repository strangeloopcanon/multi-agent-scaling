from __future__ import annotations

import pytest

from agent_economy.research.calibration_metrics import (
    brier_score,
    expected_calibration_error,
    reliability_bins,
    summarize_calibration,
)


def test_brier_score_exact_fixture() -> None:
    rows = [
        {"p_success": 0.9, "outcome": 1},
        {"p_success": 0.8, "outcome": 1},
        {"p_success": 0.2, "outcome": 0},
        {"p_success": 0.1, "outcome": 0},
    ]
    assert brier_score(rows) == pytest.approx(0.025)


def test_expected_calibration_error_exact_fixture() -> None:
    rows = [
        {"p_success": 0.9, "outcome": 1},
        {"p_success": 0.8, "outcome": 1},
        {"p_success": 0.2, "outcome": 0},
        {"p_success": 0.1, "outcome": 0},
    ]
    assert expected_calibration_error(rows, num_bins=2) == pytest.approx(0.15)


def test_reliability_bins_shape() -> None:
    rows = [
        {"p_success": 0.9, "outcome": 1},
        {"p_success": 0.2, "outcome": 0},
    ]
    bins = reliability_bins(rows, num_bins=5)
    assert len(bins) == 5
    assert sum(int(b["count"]) for b in bins) == 2


def test_summarize_calibration_by_model_and_strategy() -> None:
    rows = [
        {
            "model_ref": "openai:gpt-5-mini",
            "strategy": "direct",
            "p_success": 0.9,
            "outcome": 1,
            "input_tokens": 100,
            "output_tokens": 20,
        },
        {
            "model_ref": "openai:gpt-5-mini",
            "strategy": "anchored",
            "p_success": 0.4,
            "outcome": 0,
            "input_tokens": 120,
            "output_tokens": 25,
        },
        {
            "model_ref": "openai:gpt-5.2",
            "strategy": "direct",
            "p_success": 0.7,
            "outcome": 1,
            "input_tokens": 110,
            "output_tokens": 30,
        },
    ]

    summary = summarize_calibration(rows)
    assert summary["overall"]["count"] == 3
    assert "openai:gpt-5-mini" in summary["by_model"]
    assert "openai:gpt-5.2" in summary["by_model"]
    assert "openai:gpt-5-mini::direct" in summary["by_model_strategy"]
