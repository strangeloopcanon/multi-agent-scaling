from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from agent_economy.llm_router import LLMRouter


class PromptStrategy(str, Enum):
    DIRECT = "direct"
    ANCHORED = "anchored"
    COT = "cot"


class CalibrationResponse(BaseModel):
    p_success: float = Field(ge=0.0, le=1.0)
    rationale: str | None = None


class CalibrationRecord(BaseModel):
    benchmark: str
    task_id: str
    model_ref: str
    strategy: PromptStrategy
    p_success: float = Field(ge=0.0, le=1.0)
    outcome: int | None = Field(default=None, ge=0, le=1)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    rationale: str | None = None


def build_calibration_prompt(
    *,
    task_id: str,
    task_title: str,
    task_description: str,
    acceptance_commands: list[str],
    strategy: PromptStrategy,
) -> str:
    lines = [
        "Estimate the probability that you could complete this task correctly in one attempt.",
        "Return JSON only with fields: p_success (0..1), rationale.",
        "",
        f"Task ID: {task_id}",
        f"Title: {task_title}",
        "Description:",
        task_description.strip() or "(none)",
    ]

    if acceptance_commands:
        lines.extend(["", "Acceptance commands:"])
        for cmd in acceptance_commands:
            lines.append(f"- {cmd}")

    lines.extend(["", "Strategy guidance:"])
    if strategy == PromptStrategy.DIRECT:
        lines.append("Give your direct best estimate with a concise rationale.")
    elif strategy == PromptStrategy.ANCHORED:
        lines.append("Anchor around 0.50, then adjust up/down only based on concrete task signals.")
    elif strategy == PromptStrategy.COT:
        lines.append(
            "Think through likely implementation and verification failure modes before estimating."
        )
    else:
        raise ValueError(f"unsupported strategy: {strategy}")

    return "\n".join(lines)


def elicit_calibration(
    *,
    llm: LLMRouter,
    model_ref: str,
    benchmark: str,
    task_id: str,
    task_title: str,
    task_description: str,
    acceptance_commands: list[str],
    strategy: PromptStrategy,
    max_output_tokens: int = 500,
) -> CalibrationRecord:
    system = "You are a calibration evaluator. Output strict JSON only."
    user = build_calibration_prompt(
        task_id=task_id,
        task_title=task_title,
        task_description=task_description,
        acceptance_commands=acceptance_commands,
        strategy=strategy,
    )
    response, usage, _raw = llm.call_json(
        model_ref=model_ref,
        system=system,
        user=user,
        schema=CalibrationResponse,
        max_output_tokens=max_output_tokens,
        temperature=0.0,
    )
    return CalibrationRecord(
        benchmark=benchmark,
        task_id=task_id,
        model_ref=model_ref,
        strategy=strategy,
        p_success=float(response.p_success),
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        rationale=response.rationale,
    )
