from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from agent_economy.codex_worker import (
    DEFAULT_CODEX_MODEL,
    build_exec_prompt,
    load_task_payload,
    parse_usage_from_jsonl,
    run_codex_exec,
)


def _required_path(*, names: tuple[str, ...]) -> Path:
    for name in names:
        value = str(os.getenv(name) or "").strip()
        if value:
            return Path(value).resolve()
    raise SystemExit(f"missing required environment variable: {' or '.join(names)}")


def main() -> int:
    task_json_path = _required_path(names=("AE_TASK_JSON", "INST_TASK_JSON"))
    workspace_dir = _required_path(names=("AE_WORKSPACE_DIR", "INST_WORKSPACE_DIR"))
    artifacts_dir = _required_path(names=("AE_ARTIFACTS_DIR", "INST_ARTIFACTS_DIR"))
    model = str(os.getenv("AE_CODEX_MODEL") or os.getenv("INST_CODEX_MODEL") or "").strip()
    model = model or DEFAULT_CODEX_MODEL

    artifacts_dir.mkdir(parents=True, exist_ok=True)

    task_payload = load_task_payload(task_json_path=task_json_path)
    prompt = build_exec_prompt(task_payload=task_payload)

    codex_home_raw = str(os.getenv("AE_CODEX_HOME") or os.getenv("INST_CODEX_HOME") or "").strip()
    codex_home = Path(codex_home_raw).resolve() if codex_home_raw else None
    last_message_path = artifacts_dir / "codex_last_message.txt"
    proc = run_codex_exec(
        cwd=workspace_dir,
        prompt=prompt,
        output_path=last_message_path,
        model=model,
        codex_home=codex_home,
    )

    usage = parse_usage_from_jsonl(proc.stdout)
    if usage is not None:
        (artifacts_dir / "llm_usage.json").write_text(
            json.dumps(usage, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    if proc.stderr:
        sys.stderr.write(proc.stderr)

    final_message = ""
    if last_message_path.exists():
        final_message = last_message_path.read_text(encoding="utf-8").strip()
    elif proc.stdout:
        final_message = proc.stdout.strip()

    if final_message:
        sys.stdout.write(final_message + "\n")

    return int(proc.returncode)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
