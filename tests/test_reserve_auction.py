"""Tests for the reserve-price procurement auction simulation."""

from __future__ import annotations

import math

import pytest

from agent_economy.research.reserve_auction import (
    compute_breakeven_bid,
    simulate_reserve_auction,
    summarize_auction_results,
)


# ---------------------------------------------------------------------------
# compute_breakeven_bid
# ---------------------------------------------------------------------------


class TestComputeBreakevenBid:
    def test_basic_math(self):
        # token_cost=0.50, penalty=1.0, p=0.8
        # breakeven = (0.50 + 1.0 * 0.2) / 0.8 = 0.70 / 0.8 = 0.875
        bid = compute_breakeven_bid(p_success=0.8, token_cost=0.50, penalty=1.0)
        assert bid == pytest.approx(0.875)

    def test_zero_p_success_returns_inf(self):
        bid = compute_breakeven_bid(p_success=0.0, token_cost=0.10, penalty=1.0)
        assert math.isinf(bid)

    def test_perfect_confidence_no_penalty(self):
        # p=1.0 → breakeven = token_cost / 1.0
        bid = compute_breakeven_bid(p_success=1.0, token_cost=0.50, penalty=1.0)
        assert bid == pytest.approx(0.50)

    def test_zero_costs(self):
        # token_cost=0, penalty=0 → breakeven = 0 for any p > 0
        bid = compute_breakeven_bid(p_success=0.5, token_cost=0.0, penalty=0.0)
        assert bid == pytest.approx(0.0)

    def test_high_penalty_low_confidence(self):
        # p=0.1, penalty=10.0, token_cost=0
        # breakeven = (0 + 10 * 0.9) / 0.1 = 90.0
        bid = compute_breakeven_bid(p_success=0.1, token_cost=0.0, penalty=10.0)
        assert bid == pytest.approx(90.0)


# ---------------------------------------------------------------------------
# simulate_reserve_auction
# ---------------------------------------------------------------------------


class TestSimulateReserveAuction:
    def test_always_wins_when_bid_is_zero(self):
        draws = simulate_reserve_auction(
            breakeven_bid=0.0,
            p_success=0.5,
            max_reserve=10.0,
            n_draws=50,
            seed=1,
        )
        assert len(draws) == 50
        assert all(d["won"] == 1.0 for d in draws)
        assert all(d["profit"] >= 0.0 for d in draws)

    def test_never_wins_when_bid_exceeds_max(self):
        draws = simulate_reserve_auction(
            breakeven_bid=100.0,
            p_success=0.5,
            max_reserve=10.0,
            n_draws=50,
            seed=1,
        )
        assert all(d["won"] == 0.0 for d in draws)
        assert all(d["profit"] == 0.0 for d in draws)

    def test_deterministic_with_same_seed(self):
        a = simulate_reserve_auction(
            breakeven_bid=5.0, p_success=0.7, max_reserve=10.0, n_draws=20, seed=99
        )
        b = simulate_reserve_auction(
            breakeven_bid=5.0, p_success=0.7, max_reserve=10.0, n_draws=20, seed=99
        )
        assert a == b

    def test_different_seeds_differ(self):
        a = simulate_reserve_auction(
            breakeven_bid=5.0, p_success=0.7, max_reserve=10.0, n_draws=20, seed=1
        )
        b = simulate_reserve_auction(
            breakeven_bid=5.0, p_success=0.7, max_reserve=10.0, n_draws=20, seed=2
        )
        assert a != b

    def test_profit_formula(self):
        draws = simulate_reserve_auction(
            breakeven_bid=3.0, p_success=0.6, max_reserve=10.0, n_draws=10, seed=7
        )
        for d in draws:
            if d["won"] == 1.0:
                assert d["profit"] == pytest.approx((d["reserve"] - 3.0) * 0.6)
            else:
                assert d["profit"] == 0.0


# ---------------------------------------------------------------------------
# summarize_auction_results
# ---------------------------------------------------------------------------


class TestSummarizeAuctionResults:
    def _make_record(
        self,
        *,
        model_ref: str = "test-model",
        strategy: str = "direct",
        task_id: str = "T1",
        p_success: float = 0.8,
        estimated_tokens_total: int = 10000,
        outcome: int | None = None,
    ) -> dict:
        return {
            "model_ref": model_ref,
            "strategy": strategy,
            "task_id": task_id,
            "benchmark": "test",
            "p_success": p_success,
            "estimated_tokens_total": estimated_tokens_total,
            "outcome": outcome,
        }

    def test_empty_records(self):
        result = summarize_auction_results([])
        assert result["overall"]["count"] == 0

    def test_single_record_no_outcome(self):
        records = [self._make_record()]
        result = summarize_auction_results(records, penalty=1.0, max_reserve=10.0)
        overall = result["overall"]
        assert overall["count"] == 1
        assert overall["mean_win_rate"] > 0.0
        assert overall["mean_expected_profit"] > 0.0
        assert overall["mean_realized_profit"] is None
        assert overall["mean_calibration_cost"] is None

    def test_perfectly_calibrated_model(self):
        # A model that says p=1.0 and actually succeeds (outcome=1)
        # should have expected_profit == realized_profit.
        records = [
            self._make_record(p_success=1.0, estimated_tokens_total=0, outcome=1),
        ]
        result = summarize_auction_results(
            records, penalty=1.0, max_reserve=10.0, price_per_token=0.0
        )
        overall = result["overall"]
        assert overall["mean_realized_profit"] is not None
        assert overall["mean_expected_profit"] == pytest.approx(overall["mean_realized_profit"])
        assert overall["mean_calibration_cost"] == pytest.approx(0.0)

    def test_overconfident_model(self):
        # Model claims p=0.9 but actually fails (outcome=0).
        # Realized profit should be worse than expected.
        records = [
            self._make_record(p_success=0.9, estimated_tokens_total=0, outcome=0),
        ]
        result = summarize_auction_results(
            records, penalty=1.0, max_reserve=10.0, price_per_token=0.0
        )
        overall = result["overall"]
        assert overall["mean_expected_profit"] > 0.0
        assert overall["mean_realized_profit"] == pytest.approx(0.0)
        assert overall["mean_calibration_cost"] is not None
        assert overall["mean_calibration_cost"] > 0.0

    def test_underconfident_model(self):
        # Model claims p=0.3 but actually succeeds (outcome=1).
        # Realized profit should exceed expected.
        records = [
            self._make_record(p_success=0.3, estimated_tokens_total=0, outcome=1),
        ]
        result = summarize_auction_results(
            records, penalty=1.0, max_reserve=10.0, price_per_token=0.0
        )
        overall = result["overall"]
        assert overall["mean_realized_profit"] is not None
        assert overall["mean_realized_profit"] > overall["mean_expected_profit"]
        assert overall["mean_calibration_cost"] is not None
        assert overall["mean_calibration_cost"] < 0.0  # negative = left money on table

    def test_by_model_slicing(self):
        records = [
            self._make_record(model_ref="model-a", task_id="T1"),
            self._make_record(model_ref="model-b", task_id="T2"),
        ]
        result = summarize_auction_results(records)
        assert "model-a" in result["by_model"]
        assert "model-b" in result["by_model"]
        assert result["by_model"]["model-a"]["count"] == 1
        assert result["by_model"]["model-b"]["count"] == 1

    def test_by_model_strategy_slicing(self):
        records = [
            self._make_record(model_ref="m", strategy="direct", task_id="T1"),
            self._make_record(model_ref="m", strategy="cot", task_id="T2"),
        ]
        result = summarize_auction_results(records)
        assert "m::direct" in result["by_model_strategy"]
        assert "m::cot" in result["by_model_strategy"]

    def test_parameters_recorded(self):
        result = summarize_auction_results(
            [self._make_record()],
            price_per_token=0.00002,
            penalty=2.0,
            max_reserve=20.0,
            n_draws=50,
            seed=7,
        )
        params = result["parameters"]
        assert params["price_per_token"] == 0.00002
        assert params["penalty"] == 2.0
        assert params["max_reserve"] == 20.0
        assert params["n_draws"] == 50
        assert params["seed"] == 7

    def test_rows_included(self):
        records = [
            self._make_record(task_id="T1"),
            self._make_record(task_id="T2"),
        ]
        result = summarize_auction_results(records)
        assert len(result["rows"]) == 2
        assert result["rows"][0]["task_id"] == "T1"
        assert result["rows"][1]["task_id"] == "T2"

    def test_breakeven_in_rows(self):
        # p=0.8, tokens=10000, price_per_token=0.00001, penalty=1.0
        # token_cost = 0.10
        # breakeven = (0.10 + 1.0 * 0.2) / 0.8 = 0.30 / 0.8 = 0.375
        records = [self._make_record(p_success=0.8, estimated_tokens_total=10000)]
        result = summarize_auction_results(records, price_per_token=0.00001, penalty=1.0)
        row = result["rows"][0]
        assert row["breakeven_bid"] == pytest.approx(0.375)
