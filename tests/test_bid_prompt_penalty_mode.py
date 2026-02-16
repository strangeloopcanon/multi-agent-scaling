from __future__ import annotations

from datetime import UTC, datetime

from agent_economy.engine import ReadyTask
from agent_economy.openai_bidder import OpenAIBidder
from agent_economy.prompts import bid_prompt
from agent_economy.schemas import (
    DiscussionMessage,
    PaymentRule,
    TaskRuntime,
    TaskSpec,
    WorkerRuntime,
)


def _ready_task() -> list[ReadyTask]:
    spec = TaskSpec(
        id="T1",
        title="Task 1",
        description="Do work",
        bounty=100,
        verify_mode="manual",
        acceptance=[],
    )
    rt = TaskRuntime(task_id="T1", bounty_current=100, bounty_original=100)
    return [ReadyTask(spec=spec, runtime=rt)]


def test_bid_prompt_reputation_formula() -> None:
    worker = WorkerRuntime(worker_id="w1", model_ref="openai:gpt-5.2")
    prompt = bid_prompt(
        worker=worker,
        ready_tasks=_ready_task(),
        payment_rule=PaymentRule.ASK,
        max_bids=2,
        discussion_history=[],
        penalty_mode="reputation",
        penalty_fraction=0.1,
    )
    assert "Settlement mode: reputation" in prompt
    assert "score = rep*p_success*bounty - ask - expected_cost" in prompt
    assert "failure_penalty = 0.5*bounty*clamp((rep-0.5)/0.75, 0, 1)" in prompt
    assert "Reputation does not enter score in this mode." not in prompt


def test_bid_prompt_direct_penalty_formula() -> None:
    worker = WorkerRuntime(worker_id="w1", model_ref="openai:gpt-5.2")
    prompt = bid_prompt(
        worker=worker,
        ready_tasks=_ready_task(),
        payment_rule=PaymentRule.ASK,
        max_bids=2,
        discussion_history=[],
        penalty_mode="direct_penalty",
        penalty_fraction=0.1,
    )
    assert "Settlement mode: direct_penalty" in prompt
    assert "score = p_success*bounty - ask - expected_cost" in prompt
    assert "failure_penalty = 0.100*bounty*(0.5 + p_success)" in prompt
    assert "Reputation does not enter score in this mode." in prompt


class _FakeLLM:
    def __init__(self) -> None:
        self.last_user: str | None = None

    def call_json(self, **kwargs):
        self.last_user = kwargs.get("user")
        schema = kwargs["schema"]
        payload = {"bids": [], "discussion": "ok"}
        usage = type("Usage", (), {"calls": 1, "input_tokens": 1, "output_tokens": 1})()
        return schema.model_validate(payload), usage, "{}"


def test_openai_bidder_passes_penalty_mode_to_prompt() -> None:
    llm = _FakeLLM()
    bidder = OpenAIBidder(
        llm=llm,
        payment_rule=PaymentRule.ASK,
        max_bids=1,
        penalty_mode="direct_penalty",
        penalty_fraction=0.2,
    )
    worker = WorkerRuntime(worker_id="gpt-5.2", model_ref="openai:gpt-5.2")
    _ = bidder.get_bids(
        worker=worker,
        ready_tasks=_ready_task(),
        round_id=1,
        discussion_history=[
            # Ensure full prompt path executes with discussion history too.
            DiscussionMessage(sender="planner", message="sync", ts=datetime.now(tz=UTC))
        ],
    )
    assert llm.last_user is not None
    assert "Settlement mode: direct_penalty" in llm.last_user
    assert "failure_penalty = 0.200*bounty*(0.5 + p_success)" in llm.last_user
