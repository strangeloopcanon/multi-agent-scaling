from __future__ import annotations

from agent_economy.research.study_join import join_phase_metrics


def test_join_phase_metrics_smoke() -> None:
    phase1 = {
        "by_model": {
            "openai:gpt-5-mini": {"brier": 0.2, "ece": 0.1, "accuracy": 0.6},
            "openai:gpt-5.2": {"brier": 0.1, "ece": 0.05, "accuracy": 0.8},
        }
    }
    phase2 = [
        {
            "workers": [
                {
                    "worker_id": "mini",
                    "model_ref": "openai:gpt-5-mini",
                    "wins": 3,
                    "completions": 2,
                    "penalties": 1.0,
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
                {
                    "worker_id": "gpt52",
                    "model_ref": "openai:gpt-5.2",
                    "wins": 5,
                    "completions": 4,
                    "penalties": 0.5,
                    "usage": {"input_tokens": 120, "output_tokens": 80},
                },
            ]
        }
    ]

    joined = join_phase_metrics(calibration_summary=phase1, market_run_summaries=phase2)
    assert len(joined["rows"]) == 2
    assert "brier_vs_wins" in joined["correlations"]
