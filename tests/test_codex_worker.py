from __future__ import annotations

from agent_economy.codex_worker import (
    DEFAULT_CODEX_MODEL,
    build_exec_prompt,
    parse_usage_from_jsonl,
)


def test_build_exec_prompt_includes_core_task_details() -> None:
    prompt = build_exec_prompt(
        task_payload={
            "task": {
                "id": "T1",
                "title": "Fix bug",
                "description": "Repair the failing parser.",
                "allowed_paths": ["src/", "tests/"],
                "files_hint": ["src/parser.py", "tests/test_parser.py"],
                "acceptance": [{"cmd": "pytest tests/test_parser.py -q"}],
            },
            "bid": {"ask": 12, "eta_minutes": 30},
        }
    )

    assert "Task ID: T1" in prompt
    assert "Title: Fix bug" in prompt
    assert "Repair the failing parser." in prompt
    assert "- src/parser.py" in prompt
    assert "1. pytest tests/test_parser.py -q" in prompt
    assert "This is an ephemeral benchmark sandbox" in prompt
    assert "Do not initialize bd" in prompt
    assert "Keep changes as small and readable as you can." in prompt


def test_default_codex_model_matches_relaxed_gpt52_baseline() -> None:
    assert DEFAULT_CODEX_MODEL == "gpt-5.2"


def test_parse_usage_from_jsonl_collects_turn_usage() -> None:
    usage = parse_usage_from_jsonl(
        "\n".join(
            [
                '{"type":"thread.started","thread_id":"abc"}',
                '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":2,"output_tokens":3}}',
                '{"type":"turn.completed","usage":{"input_tokens":5,"cached_input_tokens":1,"output_tokens":7}}',
            ]
        )
    )

    assert usage == {"calls": 2, "input_tokens": 15, "output_tokens": 10}
