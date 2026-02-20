from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agent_economy.engine import BidResult, ExecutionOutcome, ReadyTask
from agent_economy.schemas import (
    Bid,
    DiscussionMessage,
    TaskSpec,
    VerifyStatus,
    WorkerRuntime,
    WorkerType,
)


class MultiplexBidder:
    def __init__(self, *, model_bidder: Any | None, external_bidder: Any | None) -> None:
        self._model_bidder = model_bidder
        self._external_bidder = external_bidder

    def get_bids(
        self,
        *,
        worker: WorkerRuntime,
        ready_tasks: Sequence[ReadyTask],
        round_id: int,
        discussion_history: Sequence[DiscussionMessage] = (),
    ) -> BidResult:
        if worker.worker_type == WorkerType.EXTERNAL_WORKER:
            bidder = self._external_bidder
        else:
            bidder = self._model_bidder

        if bidder is None:
            return BidResult()
        resp = bidder.get_bids(
            worker=worker,
            ready_tasks=list(ready_tasks),
            round_id=round_id,
            discussion_history=list(discussion_history),
        )
        if isinstance(resp, BidResult):
            return resp
        return BidResult(bids=list(resp), model_ref=worker.model_ref)

    def set_worker_prompt_context(
        self,
        *,
        worker: WorkerRuntime,
        context_lines: Sequence[str],
    ) -> None:
        if worker.worker_type == WorkerType.EXTERNAL_WORKER:
            bidder = self._external_bidder
        else:
            bidder = self._model_bidder
        if bidder is None:
            return
        setter = getattr(bidder, "set_worker_prompt_context", None)
        if callable(setter):
            setter(worker_id=worker.worker_id, context_lines=list(context_lines))


class MultiplexExecutor:
    def __init__(self, *, model_executor: Any | None, external_executor: Any | None) -> None:
        self._model_executor = model_executor
        self._external_executor = external_executor

    def execute(
        self,
        *,
        worker: WorkerRuntime,
        task: TaskSpec,
        bid: Bid,
        round_id: int,
        discussion_history: Sequence[DiscussionMessage] = (),
    ) -> ExecutionOutcome:
        if worker.worker_type == WorkerType.EXTERNAL_WORKER:
            executor = self._external_executor
        else:
            executor = self._model_executor

        if executor is None:
            return ExecutionOutcome(status=VerifyStatus.INFRA, notes="missing_executor")
        return executor.execute(
            worker=worker,
            task=task,
            bid=bid,
            round_id=round_id,
            discussion_history=list(discussion_history),
        )

    def integrate(
        self,
        *,
        worker: WorkerRuntime,
        task: TaskSpec,
        bid: Bid,
        round_id: int,
        outcome: ExecutionOutcome,
    ) -> ExecutionOutcome:
        if worker.worker_type == WorkerType.EXTERNAL_WORKER:
            executor = self._external_executor
        else:
            executor = self._model_executor

        if executor is None:
            return outcome
        fn = getattr(executor, "integrate", None)
        if not callable(fn):
            return outcome
        return fn(worker=worker, task=task, bid=bid, round_id=round_id, outcome=outcome)

    def set_concurrency_barrier(self, barrier: Any | None) -> None:
        for executor in (self._model_executor, self._external_executor):
            if executor is None:
                continue
            fn = getattr(executor, "set_concurrency_barrier", None)
            if callable(fn):
                fn(barrier)
