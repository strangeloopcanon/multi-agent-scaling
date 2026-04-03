from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


DEFAULT_CODEX_MODEL = "gpt-5.2"
_USAGE_KEYS = ("input_tokens", "output_tokens", "cached_input_tokens")


def load_task_payload(*, task_json_path: Path) -> dict[str, Any]:
    return json.loads(Path(task_json_path).read_text(encoding="utf-8"))


def build_exec_prompt(*, task_payload: dict[str, Any]) -> str:
    task = dict(task_payload.get("task") or {})
    bid = dict(task_payload.get("bid") or {})

    description = str(task.get("description") or "").strip() or "(no extra description)"
    allowed_paths = [str(path) for path in list(task.get("allowed_paths") or ["./"])]
    files_hint = [str(path) for path in list(task.get("files_hint") or []) if str(path).strip()]
    acceptance = list(task.get("acceptance") or [])

    lines = [
        "You are Codex running as an external worker inside a benchmark harness.",
        "Work directly in the current workspace to complete the task.",
        "You may inspect files, edit files, and run local commands/tests as needed.",
        "Do not ask for user input. Finish the task or stop if you cannot make safe progress.",
        "This is an ephemeral benchmark sandbox, not a normal repo work session.",
        "Do not initialize bd, create or update issues, make commits, push branches, or do session-cleanup chores.",
        "",
        f"Task ID: {task.get('id', '')}",
        f"Title: {task.get('title', '')}",
        f"Target ask: {bid.get('ask', '')}",
        f"Target ETA (minutes): {bid.get('eta_minutes', '')}",
        "",
        "Description:",
        description,
        "",
        "Allowed paths:",
    ]

    lines.extend(f"- {path}" for path in allowed_paths)

    if files_hint:
        lines.extend(["", "Files hint:"])
        lines.extend(f"- {path}" for path in files_hint)

    if acceptance:
        lines.extend(["", "Acceptance commands:"])
        for idx, command in enumerate(acceptance, start=1):
            if isinstance(command, dict):
                cmd = str(command.get("cmd") or "").strip()
            else:
                cmd = str(command).strip()
            if not cmd:
                continue
            lines.append(f"{idx}. {cmd}")

    lines.extend(
        [
            "",
            "Important:",
            "- Keep changes as small and readable as you can.",
            "- Stay within the allowed paths.",
            "- Do not initialize task trackers or create housekeeping files like .beads/ or .codex/ unless the task explicitly requires them.",
            "- Before finishing, run the most relevant acceptance command(s) when feasible.",
            "- Final response: a short plain-English summary of what you changed and what happened.",
        ]
    )

    return "\n".join(lines).strip() + "\n"


def build_codex_exec_command(
    *,
    cwd: Path,
    output_path: Path,
    model: str | None,
) -> list[str]:
    command = [
        "codex",
        "exec",
        "--json",
        "--full-auto",
        "--skip-git-repo-check",
        "--ephemeral",
        "--color",
        "never",
        "-C",
        str(cwd),
        "-o",
        str(output_path),
        "-",
    ]
    if model:
        command[2:2] = ["-m", model]
    return command


def parse_usage_from_jsonl(text: str) -> dict[str, int] | None:
    totals = {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_input_tokens": 0,
    }
    saw_usage = False

    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or payload.get("type") != "turn.completed":
            continue

        usage = payload.get("usage")
        if not isinstance(usage, dict):
            continue

        saw_usage = True
        totals["calls"] += 1
        for key in _USAGE_KEYS:
            value = usage.get(key, 0)
            try:
                totals[key] += int(value or 0)
            except Exception:
                continue

    if not saw_usage:
        return None

    return {
        "calls": int(totals["calls"]),
        "input_tokens": int(totals["input_tokens"]),
        "output_tokens": int(totals["output_tokens"]),
    }


def run_codex_exec(
    *,
    cwd: Path,
    prompt: str,
    output_path: Path,
    model: str | None,
    codex_home: Path | None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if codex_home is not None:
        env["CODEX_HOME"] = str(codex_home)
        Path(codex_home).mkdir(parents=True, exist_ok=True)

    return subprocess.run(
        build_codex_exec_command(cwd=cwd, output_path=output_path, model=model),
        input=prompt,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
