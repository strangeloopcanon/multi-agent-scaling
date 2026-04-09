from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from agent_economy.engine import BidResult, ReadyTask
from agent_economy.llm_router import LLMRouter
from agent_economy.prompts import bid_prompt, system_prompt
from agent_economy.schemas import Bid, DiscussionMessage, PaymentRule, WorkerRuntime


@dataclass(frozen=True)
class WorkerPersona:
    worker_id: str
    label: str
    persona: str


DEFAULT_PERSONAS: dict[str, WorkerPersona] = {
    "gpt-4o": WorkerPersona(
        worker_id="gpt-4o",
        label="generalist",
        persona=(
            "You are a pragmatic generalist engineer. You prefer tasks with clear specs and "
            "measurable acceptance, and you avoid risky overconfidence."
        ),
    ),
    "gpt-5-mini": WorkerPersona(
        worker_id="gpt-5-mini",
        label="fast/cheap",
        persona=(
            "You are a fast, cost-efficient assistant. You bid only on tasks you can complete "
            "reliably with small patches. You communicate risks early."
        ),
    ),
    "gpt-5.2": WorkerPersona(
        worker_id="gpt-5.2",
        label="balanced",
        persona=(
            "You are a balanced engineer focused on reliable delivery. You avoid speculative "
            "bids and prefer clear verification paths."
        ),
    ),
    "gpt-5.2-pro": WorkerPersona(
        worker_id="gpt-5.2-pro",
        label="reasoning heavy",
        persona=(
            "You are a high-reasoning specialist. You handle ambiguity and edge cases well, "
            "but bid conservatively when acceptance is underspecified."
        ),
    ),
    "gpt-5.2-auto": WorkerPersona(
        worker_id="gpt-5.2-auto",
        label="generalist+",
        persona=(
            "You are a balanced senior engineer. You like integration tasks and coordinating "
            "via short, actionable messages."
        ),
    ),
    "gpt-5.1-codex": WorkerPersona(
        worker_id="gpt-5.1-codex",
        label="coding specialist",
        persona=(
            "You are a careful coding specialist. You keep patches minimal and correct, and you "
            "follow specs even when tests are incomplete."
        ),
    ),
    "gpt-5.2-xhigh": WorkerPersona(
        worker_id="gpt-5.2-xhigh",
        label="reasoning heavy",
        persona=(
            "You are a meticulous reasoning-heavy engineer. You focus on edge cases and hidden "
            "checks. You bid higher when requirements are subtle."
        ),
    ),
    "claude-sonnet-4-5": WorkerPersona(
        worker_id="claude-sonnet-4-5",
        label="careful analyst",
        persona=(
            "You are a careful analytical engineer. You prioritize correctness and explicit "
            "reasoning over aggressive bidding."
        ),
    ),
    "claude-opus-4-5": WorkerPersona(
        worker_id="claude-opus-4-5",
        label="deep reasoner",
        persona=(
            "You are a deep-reasoning engineer. You target high-ambiguity tasks and estimate "
            "confidence conservatively when acceptance criteria are underspecified."
        ),
    ),
    "claude-opus-4-6": WorkerPersona(
        worker_id="claude-opus-4-6",
        label="deep reasoner",
        persona=(
            "You are a deep-reasoning engineer. You target high-ambiguity tasks and estimate "
            "confidence conservatively when acceptance criteria are underspecified."
        ),
    ),
    "claude-haiku-4-5": WorkerPersona(
        worker_id="claude-haiku-4-5",
        label="fast concise",
        persona=(
            "You are a fast, concise engineer. You bid on scoped tasks with clear acceptance "
            "signals and avoid speculative commitments."
        ),
    ),
    "gemini-2.5-pro": WorkerPersona(
        worker_id="gemini-2.5-pro",
        label="broad reasoner",
        persona=(
            "You are a broad-reasoning engineer. You excel at synthesis tasks and bid with "
            "clear confidence calibration."
        ),
    ),
    "gemini-3-pro-preview": WorkerPersona(
        worker_id="gemini-3-pro-preview",
        label="preview reasoner",
        persona=(
            "You are a preview-model reasoner. You can synthesize complex requirements but "
            "should account for output variability when estimating success."
        ),
    ),
}


class BidEnvelope(BaseModel):
    bids: list[Bid] = Field(default_factory=list)
    discussion: str | None = None


class OpenAIBidder:
    def __init__(
        self,
        *,
        llm: LLMRouter,
        payment_rule: PaymentRule,
        max_bids: int,
        penalty_mode: str = "reputation",
        penalty_fraction: float = 0.10,
        force_bid_for_ready_tasks: bool = False,
        retry_score_penalty_fraction: float = 0.0,
    ) -> None:
        self._llm = llm
        self._payment_rule = payment_rule
        self._max_bids = max_bids
        self._penalty_mode = str(penalty_mode or "reputation")
        self._penalty_fraction = float(penalty_fraction)
        self._force_bid_for_ready_tasks = bool(force_bid_for_ready_tasks)
        self._retry_score_penalty_fraction = float(retry_score_penalty_fraction)
        self._worker_market_context: dict[str, list[str]] = {}
        self._worker_static_market_context: dict[str, list[str]] = {}

    def set_worker_prompt_context(self, *, worker_id: str, context_lines: list[str]) -> None:
        self._worker_market_context[str(worker_id)] = [
            str(line) for line in list(context_lines or []) if str(line).strip()
        ]

    def set_worker_static_prompt_context(
        self,
        *,
        worker_id: str,
        context_lines: list[str],
    ) -> None:
        self._worker_static_market_context[str(worker_id)] = [
            str(line) for line in list(context_lines or []) if str(line).strip()
        ]

    def get_bids(
        self,
        *,
        worker: WorkerRuntime,
        ready_tasks: list[ReadyTask],
        round_id: int,
        discussion_history: list[DiscussionMessage],
    ) -> BidResult:
        _ = round_id
        if not worker.model_ref:
            return BidResult()

        persona = DEFAULT_PERSONAS.get(worker.worker_id)
        sys = system_prompt(worker=worker, persona=None if persona is None else persona.persona)
        context_lines = list(self._worker_static_market_context.get(worker.worker_id, []))
        context_lines.extend(self._worker_market_context.pop(worker.worker_id, []))
        user = bid_prompt(
            worker=worker,
            ready_tasks=ready_tasks,
            payment_rule=self._payment_rule,
            max_bids=self._max_bids,
            discussion_history=discussion_history,
            penalty_mode=self._penalty_mode,
            penalty_fraction=self._penalty_fraction,
            force_bid_for_ready_tasks=self._force_bid_for_ready_tasks,
            retry_score_penalty_fraction=self._retry_score_penalty_fraction,
            worker_market_context_lines=context_lines,
        )
        resp, usage, _raw = self._llm.call_json(
            model_ref=worker.model_ref,
            system=sys,
            user=user,
            schema=BidEnvelope,
            max_output_tokens=1200,
        )
        envelope = resp if isinstance(resp, BidEnvelope) else BidEnvelope.model_validate(resp)
        bids: list[Bid] = list(envelope.bids)[: self._max_bids]

        ready_ids = {t.spec.id for t in ready_tasks}
        out: list[Bid] = []
        seen: set[str] = set()
        for bid in bids:
            if bid.task_id not in ready_ids:
                continue
            if bid.task_id in seen:
                continue
            seen.add(bid.task_id)
            out.append(bid)
        return BidResult(
            bids=out,
            llm_usage={
                "calls": int(getattr(usage, "calls", 0) or 0),
                "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            },
            model_ref=worker.model_ref,
            discussion=envelope.discussion,
        )
