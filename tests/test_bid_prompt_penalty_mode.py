from __future__ import annotations

from datetime import UTC, datetime

from agent_economy.engine import ReadyTask
from agent_economy.openai_bidder import OpenAIBidder
from agent_economy.prompts import bid_prompt, patch_prompt
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


def test_bid_prompt_includes_force_and_exclusion_policy_context() -> None:
    worker = WorkerRuntime(worker_id="w1", model_ref="openai:gpt-5.2")
    prompt = bid_prompt(
        worker=worker,
        ready_tasks=_ready_task(),
        payment_rule=PaymentRule.ASK,
        max_bids=2,
        discussion_history=[],
        penalty_mode="direct_penalty",
        penalty_fraction=0.1,
        force_bid_for_ready_tasks=True,
        retry_score_penalty_fraction=0.0,
        worker_market_context_lines=["- T1: expected_cost_hint≈2.10"],
    )
    assert "Do not return an empty bids array." in prompt
    assert "Workers who previously failed a task cannot bid on it again." in prompt
    assert "Private Market Context (only for your bidding):" in prompt
    assert "expected_cost_hint≈2.10" in prompt


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


def test_openai_bidder_includes_worker_market_context_in_prompt() -> None:
    llm = _FakeLLM()
    bidder = OpenAIBidder(
        llm=llm,
        payment_rule=PaymentRule.ASK,
        max_bids=1,
        penalty_mode="direct_penalty",
        penalty_fraction=0.1,
        force_bid_for_ready_tasks=True,
        retry_score_penalty_fraction=0.0,
    )
    bidder.set_worker_prompt_context(
        worker_id="gpt-5.2",
        context_lines=["- T1: expected_cost_hint≈1.50"],
    )
    worker = WorkerRuntime(worker_id="gpt-5.2", model_ref="openai:gpt-5.2")
    _ = bidder.get_bids(
        worker=worker,
        ready_tasks=_ready_task(),
        round_id=1,
        discussion_history=[],
    )
    assert llm.last_user is not None
    assert "Private Market Context (only for your bidding):" in llm.last_user
    assert "expected_cost_hint≈1.50" in llm.last_user


def test_openai_bidder_keeps_static_market_context_across_rounds() -> None:
    llm = _FakeLLM()
    bidder = OpenAIBidder(
        llm=llm,
        payment_rule=PaymentRule.ASK,
        max_bids=1,
        penalty_mode="direct_penalty",
        penalty_fraction=0.1,
    )
    bidder.set_worker_static_prompt_context(
        worker_id="gpt-5.2",
        context_lines=["- Historical pass rate: 66.0%"],
    )
    worker = WorkerRuntime(worker_id="gpt-5.2", model_ref="openai:gpt-5.2")

    _ = bidder.get_bids(
        worker=worker,
        ready_tasks=_ready_task(),
        round_id=1,
        discussion_history=[],
    )
    assert llm.last_user is not None
    assert "Historical pass rate: 66.0%" in llm.last_user

    bidder.set_worker_prompt_context(
        worker_id="gpt-5.2",
        context_lines=["- T1: expected_cost_hint≈1.50"],
    )
    _ = bidder.get_bids(
        worker=worker,
        ready_tasks=_ready_task(),
        round_id=2,
        discussion_history=[],
    )
    assert llm.last_user is not None
    assert "Historical pass rate: 66.0%" in llm.last_user
    assert "expected_cost_hint≈1.50" in llm.last_user


def test_patch_prompt_prefers_diff_for_swebench_tasks() -> None:
    spec = TaskSpec(
        id="astropy__astropy-14995",
        title="SWE task",
        description="Fix bug",
        bounty=90,
        verify_mode="commands",
        acceptance=[
            {
                "cmd": "python -m agent_economy.research.swebench_eval --instance-id astropy__astropy-14995"
            }
        ],
    )
    task = ReadyTask(
        spec=spec,
        runtime=TaskRuntime(
            task_id=spec.id, bounty_current=spec.bounty, bounty_original=spec.bounty
        ),
    )
    prompt = patch_prompt(task=task, files={}, discussion_history=[])
    assert "Preferred: a unified diff starting with 'diff --git'." in prompt
    assert "BEGIN_FILE" in prompt
    assert "Output your patch now and nothing else." in prompt


def test_patch_prompt_keeps_begin_file_for_non_swebench_tasks() -> None:
    spec = TaskSpec(
        id="T2",
        title="General patch task",
        description="Fix bug",
        bounty=20,
        verify_mode="commands",
        acceptance=[{"cmd": "pytest -q"}],
    )
    task = ReadyTask(
        spec=spec,
        runtime=TaskRuntime(
            task_id=spec.id, bounty_current=spec.bounty, bounty_original=spec.bounty
        ),
    )
    prompt = patch_prompt(task=task, files={}, discussion_history=[])
    assert "BEGIN_FILE <relative_path>" in prompt
    assert "Output the patch now (BEGIN_FILE blocks preferred) and nothing else." in prompt
