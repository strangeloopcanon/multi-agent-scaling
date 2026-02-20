from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from agent_economy.ledger import HashChainedLedger
from agent_economy.schemas import EventType
from agent_economy.state import replay_ledger


def _as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _as_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def summarize_market_run(*, run_dir: Path) -> dict[str, Any]:
    run_dir = Path(run_dir)
    ledger = HashChainedLedger(run_dir / "ledger.jsonl")
    events = list(ledger.iter_events())
    state = replay_ledger(events=events)

    total_input_tokens = 0
    total_output_tokens = 0

    penalties_by_reason: dict[str, float] = defaultdict(float)
    penalties_by_worker: dict[str, float] = defaultdict(float)

    wins_by_worker: dict[str, int] = defaultdict(int)
    completions_by_worker: dict[str, int] = defaultdict(int)
    usage_by_worker: dict[str, dict[str, int]] = defaultdict(
        lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0}
    )

    for event in events:
        payload = event.payload or {}

        if event.type in {EventType.BID_SUBMITTED, EventType.PATCH_SUBMITTED}:
            worker_id = str(payload.get("worker_id") or "")
            usage = payload.get("llm_usage") if isinstance(payload.get("llm_usage"), dict) else {}
            inp = _as_int((usage or {}).get("input_tokens"), default=0)
            out = _as_int((usage or {}).get("output_tokens"), default=0)
            calls = _as_int((usage or {}).get("calls"), default=0)
            total_input_tokens += inp
            total_output_tokens += out
            if worker_id:
                usage_by_worker[worker_id]["calls"] += calls
                usage_by_worker[worker_id]["input_tokens"] += inp
                usage_by_worker[worker_id]["output_tokens"] += out

        if event.type == EventType.TASK_ASSIGNED:
            worker_id = str(payload.get("worker_id") or "")
            if worker_id:
                wins_by_worker[worker_id] += 1

        if event.type == EventType.TASK_COMPLETED:
            worker_id = str(payload.get("worker_id") or "")
            status = str(payload.get("verify_status") or "")
            if worker_id and status == "PASS":
                completions_by_worker[worker_id] += 1

        if event.type == EventType.PENALTY_APPLIED:
            worker_id = str(payload.get("worker_id") or "")
            amount = _as_float(payload.get("amount"), default=0.0)
            reason = str(payload.get("reason") or "unknown")
            penalties_by_reason[reason] += amount
            if worker_id:
                penalties_by_worker[worker_id] += amount

    tasks_total = len(state.tasks)
    tasks_done = sum(1 for task in state.tasks.values() if task.status == "DONE")
    pass_rate = (tasks_done / tasks_total) if tasks_total > 0 else 0.0
    total_tokens = total_input_tokens + total_output_tokens

    total_penalty = sum(penalties_by_reason.values())
    usage_cost_total = penalties_by_reason.get("usage_cost", 0.0) + penalties_by_reason.get(
        "bid_usage_cost", 0.0
    )
    verification_fail_penalty_total = penalties_by_reason.get("verification_fail", 0.0)

    per_worker: list[dict[str, Any]] = []
    total_wins = sum(wins_by_worker.values())
    for worker_id in sorted(state.workers.keys()):
        wins = wins_by_worker.get(worker_id, 0)
        done = completions_by_worker.get(worker_id, 0)
        penalties = penalties_by_worker.get(worker_id, 0.0)
        usage = usage_by_worker[worker_id]
        worker = state.workers[worker_id]
        per_worker.append(
            {
                "worker_id": worker_id,
                "model_ref": worker.model_ref,
                "wins": wins,
                "completions": done,
                "win_share": (wins / total_wins) if total_wins > 0 else 0.0,
                "penalties": penalties,
                "usage": usage,
            }
        )

    result: dict[str, Any] = {
        "run_dir": str(run_dir),
        "run_id": state.run_id,
        "rounds": int(state.round_id),
        "tasks_total": tasks_total,
        "tasks_done": tasks_done,
        "pass_rate": pass_rate,
        "tokens": {
            "input": total_input_tokens,
            "output": total_output_tokens,
            "total": total_tokens,
        },
        "penalties": {
            "total": total_penalty,
            "verification_fail": verification_fail_penalty_total,
            "usage_cost": penalties_by_reason.get("usage_cost", 0.0),
            "bid_usage_cost": penalties_by_reason.get("bid_usage_cost", 0.0),
            "by_reason": dict(sorted(penalties_by_reason.items())),
        },
        "usage_cost_total": usage_cost_total,
        "verification_fail_penalty_total": verification_fail_penalty_total,
        "cost_per_pass": (usage_cost_total / tasks_done) if tasks_done > 0 else 0.0,
        "tokens_per_pass": (total_tokens / tasks_done) if tasks_done > 0 else 0.0,
        "workers": per_worker,
    }

    cfg_path = run_dir / "run_config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            result["config"] = {
                "scenario_path": cfg.get("scenario_path"),
                "payment_rule": cfg.get("payment_rule"),
                "mode": cfg.get("mode"),
                "settlement_mode": cfg.get("settlement_mode"),
                "benchmark": cfg.get("benchmark"),
                "repeat": cfg.get("repeat"),
                "model_ref": cfg.get("model_ref"),
            }
        except Exception:
            pass

    return result


def summarize_market_runs(*, run_dirs: list[Path]) -> list[dict[str, Any]]:
    return [summarize_market_run(run_dir=Path(d)) for d in run_dirs]
