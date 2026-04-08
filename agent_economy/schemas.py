from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


class EventType(str, Enum):
    RUN_CREATED = "run_created"
    RUN_HALTED = "run_halted"
    ROUND_ADVANCED = "round_advanced"

    WORKER_REGISTERED = "worker_registered"
    TASK_CREATED = "task_created"

    BOUNTY_ADJUSTED = "bounty_adjusted"
    BID_SUBMITTED = "bid_submitted"
    ROUTER_DECISION = "router_decision"
    MARKET_CLEARED = "market_cleared"
    TASK_ASSIGNED = "task_assigned"
    TASK_RELEASED = "task_released"

    PATCH_SUBMITTED = "patch_submitted"
    VERIFICATION_PASSED = "verification_passed"
    VERIFICATION_FAILED = "verification_failed"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"

    PAYMENT_MADE = "payment_made"
    PENALTY_APPLIED = "penalty_applied"
    TASK_COMPLETED = "task_completed"
    PLAN_REVISION_REQUESTED = "plan_revision_requested"
    DISCUSSION_POST = "discussion_post"


class DiscussionMessage(BaseModel):
    sender: str
    message: str
    ts: datetime


class WorkerType(str, Enum):
    MODEL_AGENT = "model_agent"
    EXTERNAL_WORKER = "external_worker"


class PaymentRule(str, Enum):
    ASK = "ask"
    BOUNTY = "bounty"


class VerifyMode(str, Enum):
    COMMANDS = "commands"
    MANUAL = "manual"
    JUDGES = "judges"


class SubmissionKind(str, Enum):
    PATCH = "patch"
    TEXT = "text"
    JSON = "json"


class VerifyStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INFRA = "INFRA"
    TIMEOUT = "TIMEOUT"
    FLAKE_SUSPECTED = "FLAKE_SUSPECTED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class ArtifactRef(BaseModel):
    name: str
    sha256: str | None = None
    path: str | None = None
    media_type: str | None = None


class LedgerEvent(BaseModel):
    schema_version: int = Field(default=1, ge=1)
    event_id: str
    prev_hash: str | None = None
    hash: str
    ts: datetime
    run_id: str
    round_id: int = Field(ge=0)
    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[ArtifactRef] = Field(default_factory=list)


class CommandSpec(BaseModel):
    cmd: str
    timeout_sec: int | None = Field(default=None, ge=1, le=24 * 60 * 60)
    name: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    expect_exit_codes: list[int] = Field(default_factory=lambda: [0])
    infra_exit_codes: list[int] = Field(default_factory=list)

    @field_validator("cmd")
    @classmethod
    def _non_empty_cmd(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("cmd must be a non-empty string")
        return v

    @field_validator("expect_exit_codes", "infra_exit_codes", mode="before")
    @classmethod
    def _coerce_exit_codes(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, (int, float, str)):
            v = [v]
        if not isinstance(v, list):
            return v
        out: list[int] = []
        seen: set[int] = set()
        for raw in v:
            try:
                code = int(raw)
            except Exception:
                continue
            if code in seen:
                continue
            seen.add(code)
            out.append(code)
        return out

    @model_validator(mode="after")
    def _validate_exit_code_sets(self) -> "CommandSpec":
        overlap = set(self.expect_exit_codes) & set(self.infra_exit_codes)
        if overlap:
            raise ValueError("expect_exit_codes and infra_exit_codes must be disjoint")
        return self


class ContextSpec(BaseModel):
    include_globs: list[str] = Field(default_factory=list)
    exclude_globs: list[str] = Field(default_factory=list)
    max_bytes: int = Field(default=400_000, ge=1)


class JudgeSpec(BaseModel):
    # Workers that participate in settlement voting for verify_mode=judges.
    # Back-compat: accept legacy "models" field, but treat values as worker refs.
    workers: list[str] = Field(
        default_factory=list, validation_alias=AliasChoices("workers", "models")
    )
    min_passes: int | None = Field(default=None, ge=1)
    include_self: bool = True


class TaskSpec(BaseModel):
    id: str
    title: str
    description: str = ""
    deps: list[str] = Field(default_factory=list)

    bounty: int = Field(ge=1)
    max_attempts: int = Field(default=3, ge=1, le=20)
    verify_mode: VerifyMode = VerifyMode.COMMANDS
    submission_kind: SubmissionKind = SubmissionKind.PATCH
    acceptance: list[CommandSpec] = Field(default_factory=list)
    hidden_acceptance: list[CommandSpec] = Field(default_factory=list)
    judges: JudgeSpec | None = None

    allowed_paths: list[str] = Field(default_factory=lambda: ["./"])
    context: ContextSpec | None = None
    files_hint: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_acceptance(self) -> "TaskSpec":
        if self.verify_mode == VerifyMode.COMMANDS and not self.acceptance:
            raise ValueError("acceptance commands must be non-empty when verify_mode=commands")
        return self


class Bid(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    task_id: str
    ask: int = Field(ge=1)
    self_assessed_p_success: float = Field(
        validation_alias=AliasChoices("self_assessed_p_success", "confidence", "p_success"),
        ge=0.0,
        le=1.0,
    )
    eta_minutes: int = Field(ge=1, le=240)
    notes: str | None = None

    @field_validator("ask", "eta_minutes", mode="before")
    @classmethod
    def _coerce_int_fields(cls, v: Any) -> Any:
        if v is None:
            return v
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            if not math.isfinite(v):
                return v
            return int(max(1, round(v)))
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return v
            try:
                f = float(s)
            except Exception:
                return v
            if not math.isfinite(f):
                return v
            return int(max(1, round(f)))
        return v


TaskStatus = Literal["TODO", "ASSIGNED", "REVIEW", "DONE"]


class TaskRuntime(BaseModel):
    task_id: str
    status: TaskStatus = "TODO"
    bounty_current: int = Field(ge=1)
    bounty_original: int = Field(ge=1)
    fail_count: int = Field(default=0, ge=0)
    assigned_worker: str | None = None


class WorkerRuntime(BaseModel):
    worker_id: str
    worker_type: WorkerType = WorkerType.MODEL_AGENT
    model_ref: str | None = None

    balance: float = 0.0
    reputation: float = Field(default=1.0, ge=0.0, le=2.0)
    assigned_task: str | None = None

    wins: int = 0
    completions: int = 0
    failures: int = 0


class DerivedState(BaseModel):
    run_id: str
    round_id: int = 0
    tasks: dict[str, TaskRuntime] = Field(default_factory=dict)
    workers: dict[str, WorkerRuntime] = Field(default_factory=dict)
    payment_rule: PaymentRule = PaymentRule.ASK
    discussion_history: list[DiscussionMessage] = Field(default_factory=list)
