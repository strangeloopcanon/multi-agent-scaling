from __future__ import annotations

from enum import Enum
import math

from pydantic import BaseModel, Field

from agent_economy.llm_router import LLMRouter


class PromptStrategy(str, Enum):
    INFORMED_BID = "informed_bid"
    SECOND_PRICE_INFORMED_BID = "second_price_informed_bid"
    FORMULA_SECOND_PRICE = "formula_second_price"
    DIRECT_BID = "direct_bid"
    PROB_TOKENS = "prob_tokens"
    PLAN_PROB_TOKENS = "plan_prob_tokens"
    # Legacy aliases retained for backward compatibility.
    DIRECT = "direct"
    ANCHORED = "anchored"
    COT = "cot"


class CalibrationResponse(BaseModel):
    p_success: float = Field(ge=0.0, le=1.0)
    estimated_tokens_total: int = Field(ge=0)
    ask: float | None = Field(default=None, ge=0.0)
    rationale: str | None = None


class CalibrationRecord(BaseModel):
    benchmark: str
    task_id: str
    model_ref: str
    strategy: PromptStrategy
    p_success: float = Field(ge=0.0, le=1.0)
    estimated_tokens_total: int | None = Field(default=None, ge=0)
    ask: float | None = Field(default=None, ge=0.0)
    reserve_shown: float | None = Field(default=None, ge=0.0)
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
    reserve_shown: float | None = None,
    penalty: float = 1.0,
    price_per_token: float = 0.00001,
) -> str:
    strategy_mode = str(strategy.value)
    wants_ask = strategy_mode in {
        PromptStrategy.DIRECT_BID.value,
        PromptStrategy.INFORMED_BID.value,
        PromptStrategy.SECOND_PRICE_INFORMED_BID.value,
        PromptStrategy.FORMULA_SECOND_PRICE.value,
    }

    if strategy == PromptStrategy.FORMULA_SECOND_PRICE:
        lines = [
            "You are bidding on a software engineering task in a second-price sealed-bid auction.",
            "",
            "Payment rule: if you win, you are paid the second-lowest ask "
            "(or the client's budget if you are the only bidder), not your own ask.",
            "",
            "Economics:",
            f"- Price per token: {price_per_token:.10f}".rstrip("0").rstrip(".") + " $/token",
            f"- Failure penalty: ${penalty:.2f}",
            "- Your breakeven cost = (estimated_tokens x price_per_token) + penalty x (1 - p_success)",
            "",
            "Dominant strategy: in a second-price auction, the dominant strategy is to bid "
            "your true expected cost. Bidding above or below cannot improve your outcome.",
        ]
        if reserve_shown is not None:
            lines.append(f"The client's budget cap for this task is ${reserve_shown:.2f}.")
        lines.extend(
            [
                "",
                "Return JSON only with fields: ask (your price in dollars), "
                "p_success (0..1), estimated_tokens_total "
                "(total model tokens for one full solve attempt), rationale (optional).",
            ]
        )
    elif strategy in {PromptStrategy.INFORMED_BID, PromptStrategy.SECOND_PRICE_INFORMED_BID}:
        if strategy == PromptStrategy.SECOND_PRICE_INFORMED_BID:
            payment_line = (
                "If you are selected and solve it, you are paid the second-lowest ask "
                "(or the client's budget if you are the only bidder), not your own ask. "
                "Bidding your true cost is optimal."
            )
        else:
            payment_line = "If you are selected and solve it, you are paid your ask price."
        lines = [
            "You are bidding on a software engineering task.",
            payment_line,
            f"If you fail, you pay a penalty of ${penalty:.2f}.",
            f"Your compute costs approximately ${price_per_token:f} per token.",
        ]
        if reserve_shown is not None:
            lines.append(f"The client's maximum budget for this task is ${reserve_shown:.2f}.")
        lines.extend(
            [
                "",
                "Return JSON only with fields: ask (your price in dollars), "
                "p_success (0..1), estimated_tokens_total "
                "(total model tokens for one full solve attempt), rationale (optional).",
            ]
        )
    elif wants_ask:
        lines = [
            "Estimate the probability that you could complete this task correctly in one attempt.",
            "Return JSON only with fields: p_success (0..1), estimated_tokens_total "
            "(total model tokens for one full solve attempt), ask (non-negative bid in dollars), "
            "rationale (optional).",
        ]
    else:
        lines = [
            "Estimate the probability that you could complete this task correctly in one attempt.",
            "Return JSON only with fields: p_success (0..1), estimated_tokens_total "
            "(total model tokens for one full solve attempt), rationale (optional).",
        ]

    lines.extend(
        [
            "",
            f"Task ID: {task_id}",
            f"Title: {task_title}",
            "Description:",
            task_description.strip() or "(none)",
        ]
    )

    if acceptance_commands:
        lines.extend(["", "Acceptance commands:"])
        for cmd in acceptance_commands:
            lines.append(f"- {cmd}")

    lines.extend(["", "Strategy guidance:"])
    if strategy == PromptStrategy.FORMULA_SECOND_PRICE:
        lines.append(
            "Estimate your token usage and probability of success, then apply the "
            "breakeven formula above to determine your ask."
        )
    elif strategy == PromptStrategy.SECOND_PRICE_INFORMED_BID:
        lines.append(
            "In this second-price auction, you should bid your true cost "
            "(compute costs plus expected penalty risk). "
            "You will never be paid less than your ask, so there is no benefit to inflating it."
        )
    elif strategy == PromptStrategy.INFORMED_BID:
        lines.append(
            "Consider your compute costs and probability of success to set a price "
            "that covers your expected costs and gives you a reasonable margin."
        )
    elif strategy == PromptStrategy.DIRECT_BID:
        lines.append(
            "Estimate p_success and token usage, then provide a direct ask that you would bid "
            "without seeing a reserve price."
        )
    elif strategy in {PromptStrategy.PROB_TOKENS, PromptStrategy.DIRECT}:
        lines.append("Give your direct best estimate with a concise rationale.")
    elif strategy == PromptStrategy.ANCHORED:
        lines.append("Anchor around 0.50, then adjust up/down only based on concrete task signals.")
    elif strategy in {PromptStrategy.PLAN_PROB_TOKENS, PromptStrategy.COT}:
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
    reserve_shown: float | None = None,
    penalty: float = 1.0,
    price_per_token: float = 0.00001,
    max_output_tokens: int = 1200,
) -> CalibrationRecord:
    system = "You are a calibration evaluator. Output strict JSON only."
    user = build_calibration_prompt(
        task_id=task_id,
        task_title=task_title,
        task_description=task_description,
        acceptance_commands=acceptance_commands,
        strategy=strategy,
        reserve_shown=reserve_shown,
        penalty=penalty,
        price_per_token=price_per_token,
    )
    response, usage, _raw = llm.call_json(
        model_ref=model_ref,
        system=system,
        user=user,
        schema=CalibrationResponse,
        max_output_tokens=max_output_tokens,
        temperature=0.0,
    )
    ask = float(response.ask) if response.ask is not None else None
    if ask is not None and not math.isfinite(ask):
        ask = None
    return CalibrationRecord(
        benchmark=benchmark,
        task_id=task_id,
        model_ref=model_ref,
        strategy=strategy,
        p_success=float(response.p_success),
        estimated_tokens_total=int(response.estimated_tokens_total),
        ask=ask,
        reserve_shown=reserve_shown,
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        rationale=response.rationale,
    )
