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
