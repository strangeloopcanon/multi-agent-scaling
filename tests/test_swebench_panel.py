from __future__ import annotations

import json
from pathlib import Path

from agent_economy.research.swebench_panel import (
    attempt_rows_from_ledger,
    find_swebench_ledgers,
    task_family_from_instance_id,
)


def _append_event(path: Path, event_type: str, payload: dict, *, round_id: int = 0) -> None:
    event = {
        "type": event_type,
        "payload": payload,
        "round_id": round_id,
        "run_id": "swebench_market_direct_penalty_001_astropy__astropy-14182",
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event))
        f.write("\n")


def test_task_family_from_instance_id() -> None:
    assert task_family_from_instance_id("astropy__astropy-14182") == "astropy/astropy"
    assert task_family_from_instance_id("pylint-dev__pylint-7080") == "pylint-dev/pylint"
    assert (
        task_family_from_instance_id("scikit-learn__scikit-learn-10297")
        == "scikit-learn/scikit-learn"
    )
    assert task_family_from_instance_id("bad-id") == "unknown"


def test_attempt_rows_from_ledger_pairs_completion_with_patch_usage(tmp_path: Path) -> None:
    run_dir = tmp_path / "source_run" / "swebench_market_direct_penalty_001_astropy__astropy-14182"
    run_dir.mkdir(parents=True)
    ledger = run_dir / "ledger.jsonl"

    _append_event(
        ledger,
        "worker_registered",
        {"worker_id": "gpt-5.2", "model_ref": "openai:gpt-5.2-2025-12-11"},
    )
    _append_event(
        ledger,
        "patch_submitted",
        {
            "task_id": "astropy__astropy-14182",
            "worker_id": "gpt-5.2",
            "model_ref": "openai:gpt-5.2-2025-12-11",
            "llm_usage": {"input_tokens": 10, "output_tokens": 3, "calls": 1},
        },
        round_id=1,
    )
    _append_event(
        ledger,
        "task_completed",
        {
            "task_id": "astropy__astropy-14182",
            "worker_id": "gpt-5.2",
            "success": True,
            "verify_status": "PASS",
        },
        round_id=1,
    )

    rows = attempt_rows_from_ledger(ledger)
    assert len(rows) == 1
    assert rows[0].model == "openai:gpt-5.2-2025-12-11"
    assert rows[0].task == "astropy__astropy-14182"
    assert rows[0].task_family == "astropy/astropy"
    assert rows[0].success is True
    assert rows[0].token_consumption == 13


def test_attempt_rows_from_ledger_keeps_retry_attempts(tmp_path: Path) -> None:
    run_dir = tmp_path / "source_run" / "swebench_market_direct_penalty_001_django__django-11179"
    run_dir.mkdir(parents=True)
    ledger = run_dir / "ledger.jsonl"

    for worker_id, model_ref in [
        ("gemini", "google:models/gemini-3-pro-preview"),
        ("gpt-5.2", "openai:gpt-5.2-2025-12-11"),
    ]:
        _append_event(
            ledger,
            "worker_registered",
            {"worker_id": worker_id, "model_ref": model_ref},
        )

    _append_event(
        ledger,
        "patch_submitted",
        {
            "task_id": "django__django-11179",
            "worker_id": "gemini",
            "model_ref": "google:models/gemini-3-pro-preview",
            "llm_usage": {"input_tokens": 100, "output_tokens": 7},
        },
        round_id=1,
    )
    _append_event(
        ledger,
        "task_completed",
        {
            "task_id": "django__django-11179",
            "worker_id": "gemini",
            "success": False,
            "verify_status": "FAIL",
        },
        round_id=1,
    )
    _append_event(
        ledger,
        "patch_submitted",
        {
            "task_id": "django__django-11179",
            "worker_id": "gpt-5.2",
            "model_ref": "openai:gpt-5.2-2025-12-11",
            "llm_usage": {"input_tokens": 50, "output_tokens": 5},
        },
        round_id=3,
    )
    _append_event(
        ledger,
        "task_completed",
        {
            "task_id": "django__django-11179",
            "worker_id": "gpt-5.2",
            "success": True,
            "verify_status": "PASS",
        },
        round_id=3,
    )

    rows = attempt_rows_from_ledger(ledger)
    assert [row.attempt_index for row in rows] == [1, 2]
    assert [row.success for row in rows] == [False, True]
    assert [row.token_consumption for row in rows] == [107, 55]


def test_find_swebench_ledgers_skips_invalid_by_default(tmp_path: Path) -> None:
    valid = tmp_path / "run" / "swebench_market_direct_penalty_001_a__b-1"
    invalid = tmp_path / "_invalid" / "run" / "swebench_market_direct_penalty_001_a__b-1"
    valid.mkdir(parents=True)
    invalid.mkdir(parents=True)
    (valid / "ledger.jsonl").write_text("", encoding="utf-8")
    (invalid / "ledger.jsonl").write_text("", encoding="utf-8")

    assert find_swebench_ledgers([tmp_path]) == [valid / "ledger.jsonl"]
    assert find_swebench_ledgers([tmp_path], include_invalid=True) == [
        invalid / "ledger.jsonl",
        valid / "ledger.jsonl",
    ]
