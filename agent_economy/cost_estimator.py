from __future__ import annotations

import math
from dataclasses import dataclass

from agent_economy.costing import Price, estimate_cost_units, price_for_model
from agent_economy.schemas import Bid, SubmissionKind, TaskSpec, WorkerRuntime
from agent_economy.worker_state import PersistedWorkerState


def _task_complexity_multiplier(task: TaskSpec) -> float:
    """Estimate task complexity as a multiplier (1.0 = baseline).

    Factors:
    - Description length: more text = more context tokens
    - Files hint count: more files = larger input context
    - Acceptance commands: more tests = potentially more complex task
    """
    # Description complexity: log scale to avoid explosion
    desc_chars = len(task.description or "")
    desc_factor = 1.0 + 0.1 * math.log1p(desc_chars / 100)  # +10% per ~e^1 * 100 chars

    # Files hint complexity: each file adds context
    files_count = len(task.files_hint or [])
    files_factor = 1.0 + 0.05 * files_count  # +5% per file

    # Acceptance complexity: more commands suggests more requirements
    accept_count = len(task.acceptance or []) + len(task.hidden_acceptance or [])
    accept_factor = 1.0 + 0.03 * accept_count  # +3% per command

    return desc_factor * files_factor * accept_factor


@dataclass(frozen=True)
class ExpectedCostEstimator:
    state: PersistedWorkerState
    pricing: dict[str, Price]
    default_patch_input_tokens: int = 1500
    default_patch_output_tokens: int = 1500
    default_text_input_tokens: int = 500
    default_text_output_tokens: int = 400

    def expected_cost(
        self,
        *,
        worker: WorkerRuntime,
        task: TaskSpec,
        bid: Bid,
        round_id: int,
    ) -> float:
        _ = bid, round_id
        if not worker.model_ref:
            return 0.0

        price = price_for_model(pricing=self.pricing, model_ref=worker.model_ref)

        stats = self.state.workers.get(worker.worker_id)
        is_patch_task = task.submission_kind == SubmissionKind.PATCH
        if is_patch_task:
            in_tok = (
                float(stats.patch_ema_input_tokens)
                if stats is not None and stats.patch_ema_input_tokens > 0
                else float(self.default_patch_input_tokens)
            )
            out_tok = (
                float(stats.patch_ema_output_tokens)
                if stats is not None and stats.patch_ema_output_tokens > 0
                else float(self.default_patch_output_tokens)
            )
        else:
            in_tok = float(self.default_text_input_tokens)
            out_tok = float(self.default_text_output_tokens)

        # Apply task complexity multiplier to account for task-specific factors.
        # For text/json subtasks we damp the multiplier to avoid over-penalizing
        # long instructions and file lists in planning-heavy runs.
        complexity = _task_complexity_multiplier(task)
        if not is_patch_task:
            complexity = 1.0 + 0.35 * max(0.0, complexity - 1.0)
            complexity = min(complexity, 2.5)
        in_tok *= complexity
        out_tok *= complexity

        return float(estimate_cost_units(input_tokens=in_tok, output_tokens=out_tok, price=price))

    def actual_cost(
        self,
        *,
        worker: WorkerRuntime,
        llm_usage: dict[str, int] | None,
    ) -> float:
        if not worker.model_ref or not llm_usage:
            return 0.0

        input_tokens = float(int(llm_usage.get("input_tokens", 0) or 0))
        output_tokens = float(int(llm_usage.get("output_tokens", 0) or 0))
        if input_tokens <= 0 and output_tokens <= 0:
            return 0.0

        price = price_for_model(pricing=self.pricing, model_ref=worker.model_ref)
        return float(
            estimate_cost_units(input_tokens=input_tokens, output_tokens=output_tokens, price=price)
        )
