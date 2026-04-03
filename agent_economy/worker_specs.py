from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from agent_economy.schemas import WorkerRuntime, WorkerType


class FixedBidSpec(BaseModel):
    ask: int = Field(ge=1)
    p_success: float = Field(ge=0.0, le=1.0)
    eta_minutes: int = Field(ge=1, le=240)
    notes: str | None = None


class CommandWorkerSpec(BaseModel):
    worker_id: str
    exec_cmd: str
    model_ref: str | None = None

    # How this worker produces bids. Provide either:
    # - bid_cmd: a command that reads JSON on stdin and prints JSON on stdout, or
    # - fixed_bid: a constant bid template applied to ready tasks.
    bid_cmd: str | None = None
    fixed_bid: FixedBidSpec | None = None

    # Optional: command to act as a judge for verify_mode=judges tasks.
    # Reads a JSON object on stdin and prints a JudgeDecision JSON object on stdout.
    judge_cmd: str | None = None

    # Optional: command to act as a planner for --decompose.
    # Reads a JSON object on stdin and prints a DecompositionPlan JSON object on stdout.
    plan_cmd: str | None = None

    env: dict[str, str] = Field(default_factory=dict)
    timeout_sec: int | None = Field(default=None, ge=1, le=24 * 60 * 60)

    @field_validator("exec_cmd")
    @classmethod
    def _non_empty_exec_cmd(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("exec_cmd must be non-empty")
        return v

    @field_validator("model_ref")
    @classmethod
    def _non_empty_model_ref(cls, v: str | None) -> str | None:
        if v is None:
            return None
        value = v.strip()
        if not value:
            raise ValueError("model_ref must be non-empty when provided")
        return value

    @model_validator(mode="after")
    def _require_bid_source(self) -> "CommandWorkerSpec":
        if not (self.bid_cmd and self.bid_cmd.strip()) and self.fixed_bid is None:
            raise ValueError("command worker must provide bid_cmd or fixed_bid")
        return self


class OpenAIWorkerSpec(BaseModel):
    worker_id: str
    model_ref: str

    @field_validator("model_ref")
    @classmethod
    def _non_empty_model_ref(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("model_ref must be non-empty")
        return v


@dataclass(frozen=True)
class WorkerPool:
    workers: list[WorkerRuntime]
    command_specs: dict[str, CommandWorkerSpec]


def _parse_worker_item(item: Any) -> tuple[WorkerRuntime, CommandWorkerSpec | None]:
    if not isinstance(item, dict):
        raise ValueError("each worker spec must be an object")
    if "exec_cmd" in item:
        spec = CommandWorkerSpec.model_validate(item)
        rt = WorkerRuntime(
            worker_id=spec.worker_id,
            worker_type=WorkerType.EXTERNAL_WORKER,
            model_ref=spec.model_ref,
            balance=0.0,
            reputation=1.0,
        )
        return rt, spec

    spec = OpenAIWorkerSpec.model_validate(item)
    rt = WorkerRuntime(
        worker_id=spec.worker_id,
        worker_type=WorkerType.MODEL_AGENT,
        model_ref=spec.model_ref,
        balance=0.0,
        reputation=1.0,
    )
    return rt, None


def load_worker_pool_from_json(data: Any) -> WorkerPool:
    items: list[Any]

    if isinstance(data, dict) and "workers" in data:
        items = data.get("workers")  # type: ignore[assignment]
        if not isinstance(items, list):
            raise ValueError("workers must be a list")
    elif isinstance(data, list):
        items = data
    elif isinstance(data, dict) and data and all(isinstance(v, str) for v in data.values()):
        # Back-compat: allow {"worker_id": "model_ref"} mapping.
        items = [{"worker_id": str(k), "model_ref": str(v)} for k, v in data.items()]
    else:
        raise ValueError("invalid workers spec (expected list, {workers:[...]}, or {id:model_ref})")

    workers: list[WorkerRuntime] = []
    cmd_specs: dict[str, CommandWorkerSpec] = {}
    seen: set[str] = set()
    for item in items:
        rt, cmd = _parse_worker_item(item)
        if rt.worker_id in seen:
            raise ValueError(f"duplicate worker_id: {rt.worker_id}")
        seen.add(rt.worker_id)
        workers.append(rt)
        if cmd is not None:
            cmd_specs[rt.worker_id] = cmd

    if not workers:
        raise ValueError("workers list must be non-empty")

    return WorkerPool(workers=workers, command_specs=cmd_specs)


def load_worker_pool_from_path(path: Path) -> WorkerPool:
    data = json.loads(path.read_text(encoding="utf-8"))
    return load_worker_pool_from_json(data)
