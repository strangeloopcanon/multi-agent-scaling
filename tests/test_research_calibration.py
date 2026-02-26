from __future__ import annotations

from pydantic import BaseModel

from agent_economy.research.calibration import (
    CalibrationResponse,
    PromptStrategy,
    build_calibration_prompt,
    elicit_calibration,
)


def test_build_calibration_prompt_mentions_estimated_tokens_total() -> None:
    prompt = build_calibration_prompt(
        task_id="T1",
        task_title="Title",
        task_description="Description",
        acceptance_commands=["pytest -q"],
        strategy=PromptStrategy.DIRECT,
    )
    assert "estimated_tokens_total" in prompt
    assert "p_success" in prompt


def test_build_calibration_prompt_direct_bid_mentions_ask() -> None:
    prompt = build_calibration_prompt(
        task_id="T1",
        task_title="Title",
        task_description="Description",
        acceptance_commands=["pytest -q"],
        strategy=PromptStrategy.DIRECT_BID,
    )
    assert "ask" in prompt


class _FakeUsage:
    def __init__(self) -> None:
        self.input_tokens = 12
        self.output_tokens = 34


class _FakeLLM:
    def call_json(
        self,
        *,
        model_ref: str,
        system: str,
        user: str,
        schema: type[BaseModel],
        max_output_tokens: int,
        temperature: float,
    ):
        _ = model_ref, system, user, schema, max_output_tokens, temperature
        resp = CalibrationResponse(
            p_success=0.61,
            estimated_tokens_total=900,
            rationale="ok",
        )
        return resp, _FakeUsage(), "{}"


def test_elicit_calibration_captures_estimated_tokens_total() -> None:
    rec = elicit_calibration(
        llm=_FakeLLM(),  # type: ignore[arg-type]
        model_ref="openai:gpt-5.2-2025-12-11",
        benchmark="swebench",
        task_id="T1",
        task_title="Title",
        task_description="Description",
        acceptance_commands=["pytest -q"],
        strategy=PromptStrategy.DIRECT,
    )
    assert rec.p_success == 0.61
    assert rec.estimated_tokens_total == 900
    assert rec.input_tokens == 12
    assert rec.output_tokens == 34


def test_build_calibration_prompt_informed_bid_includes_economics() -> None:
    prompt = build_calibration_prompt(
        task_id="T1",
        task_title="Title",
        task_description="Description",
        acceptance_commands=["pytest -q"],
        strategy=PromptStrategy.INFORMED_BID,
        reserve_shown=5.0,
        penalty=1.0,
        price_per_token=0.00001,
    )
    assert "ask" in prompt
    assert "$5.00" in prompt
    assert "$1.00" in prompt
    assert "0.000010" in prompt
    assert "bidding" in prompt.lower()


def test_build_calibration_prompt_informed_bid_no_reserve() -> None:
    prompt = build_calibration_prompt(
        task_id="T1",
        task_title="Title",
        task_description="Description",
        acceptance_commands=[],
        strategy=PromptStrategy.INFORMED_BID,
        reserve_shown=None,
    )
    assert "ask" in prompt
    assert "budget" not in prompt.lower()


def test_elicit_calibration_informed_bid_records_reserve() -> None:
    class _FakeLLMWithAsk:
        def call_json(self, **kwargs):
            resp = CalibrationResponse(
                p_success=0.85,
                estimated_tokens_total=1000,
                ask=3.50,
                rationale="ok",
            )
            return resp, type("U", (), {"input_tokens": 10, "output_tokens": 20})(), "{}"

    rec = elicit_calibration(
        llm=_FakeLLMWithAsk(),  # type: ignore[arg-type]
        model_ref="openai:gpt-5.2-2025-12-11",
        benchmark="swebench",
        task_id="T1",
        task_title="Title",
        task_description="Description",
        acceptance_commands=["pytest -q"],
        strategy=PromptStrategy.INFORMED_BID,
        reserve_shown=5.0,
    )
    assert rec.ask == 3.50
    assert rec.reserve_shown == 5.0
    assert rec.strategy == PromptStrategy.INFORMED_BID


def test_elicit_calibration_direct_bid_captures_ask() -> None:
    class _FakeLLMWithAsk(_FakeLLM):
        def call_json(self, **kwargs):  # type: ignore[override]
            _ = kwargs
            resp = CalibrationResponse(
                p_success=0.77,
                estimated_tokens_total=1200,
                ask=3.25,
                rationale="ok",
            )
            return resp, _FakeUsage(), "{}"

    rec = elicit_calibration(
        llm=_FakeLLMWithAsk(),  # type: ignore[arg-type]
        model_ref="openai:gpt-5.2-2025-12-11",
        benchmark="swebench",
        task_id="T1",
        task_title="Title",
        task_description="Description",
        acceptance_commands=["pytest -q"],
        strategy=PromptStrategy.DIRECT_BID,
    )
    assert rec.ask == 3.25
