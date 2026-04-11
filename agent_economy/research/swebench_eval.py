from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_DATASET_NAME = "princeton-nlp/SWE-bench_Lite"
DEFAULT_SPLIT = "test"
_HARNESS_TIMEOUT_GRACE_SECONDS = 300
_ACTIVE_HARNESS_PGID: int | None = None


@dataclass(frozen=True)
class HarnessEvalResult:
    completed: bool
    resolved: bool
    report_path: str | None
    run_id: str
    returncode: int
    notes: str | None = None


def _safe_token(value: str) -> str:
    out = "".join(ch if (ch.isalnum() or ch in "-_.") else "_" for ch in value.strip())
    return out or "task"


def _build_run_id(*, prefix: str, instance_id: str, patch_text: str | None, gold: bool) -> str:
    tag = "gold" if gold else hashlib.sha256((patch_text or "").encode("utf-8")).hexdigest()[:10]
    return f"{_safe_token(prefix)}_{_safe_token(instance_id)}_{tag}"


def _load_report(*, work_dir: Path, run_id: str) -> tuple[dict, Path] | tuple[None, None]:
    wd = Path(work_dir)
    runner_dir = _runner_dir(work_dir=wd)
    search_roots = [
        runner_dir / "logs" / "run_evaluation" / run_id,
        wd / "logs" / "run_evaluation" / run_id,
        runner_dir / run_id,
        wd / run_id,
        runner_dir,
        wd,
    ]
    for root in search_roots:
        if not root.is_dir():
            continue
        reports = sorted(root.glob("**/report.json"))
        for report_path in reports:
            try:
                payload = json.loads(report_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, dict):
                return payload, report_path
    return None, None


def _load_summary(*, work_dir: Path, run_id: str) -> tuple[dict, Path] | tuple[None, None]:
    wd = Path(work_dir)
    runner_dir = _runner_dir(work_dir=wd)
    seen: set[Path] = set()
    candidates = [
        wd / f"agent-economy-market.{run_id}.json",
        runner_dir / f"agent-economy-market.{run_id}.json",
        *sorted(wd.glob(f"*.{run_id}.json")),
        *sorted(runner_dir.glob(f"*.{run_id}.json")),
    ]
    for summary_path in candidates:
        if summary_path in seen:
            continue
        seen.add(summary_path)
        if not summary_path.is_file():
            continue
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload, summary_path
    return None, None


def _result_from_summary(
    *,
    instance_id: str,
    run_id: str,
    summary: dict | None,
    summary_path: Path | None,
) -> HarnessEvalResult | None:
    if summary is None:
        return None

    resolved_ids = _summary_ids(summary=summary, key="resolved_ids")
    unresolved_ids = _summary_ids(summary=summary, key="unresolved_ids")
    error_ids = _summary_ids(summary=summary, key="error_ids")
    incomplete_ids = _summary_ids(summary=summary, key="incomplete_ids")

    if instance_id in resolved_ids:
        return HarnessEvalResult(
            completed=True,
            resolved=True,
            report_path=None if summary_path is None else str(summary_path),
            run_id=run_id,
            returncode=0,
            notes=None,
        )
    if instance_id in unresolved_ids:
        return HarnessEvalResult(
            completed=True,
            resolved=False,
            report_path=None if summary_path is None else str(summary_path),
            run_id=run_id,
            returncode=1,
            notes=None,
        )
    if instance_id in error_ids or instance_id in incomplete_ids:
        return HarnessEvalResult(
            completed=False,
            resolved=False,
            report_path=None if summary_path is None else str(summary_path),
            run_id=run_id,
            returncode=2,
            notes="evaluation_error",
        )
    return None


def _summary_ids(*, summary: dict, key: str) -> set[str]:
    raw = summary.get(key)
    if not isinstance(raw, list):
        return set()
    out: set[str] = set()
    for item in raw:
        value = str(item).strip()
        if value:
            out.add(value)
    return out


def _resolved_from_report(*, report: dict, instance_id: str) -> bool:
    row = report.get(instance_id)
    if isinstance(row, dict):
        return bool(row.get("resolved"))
    return False


def _runner_dir(*, work_dir: Path) -> Path:
    runner_dir = Path(work_dir) / ".ae_harness_runner"
    runner_dir.mkdir(parents=True, exist_ok=True)
    return runner_dir


def _docker_config_path(*, env: dict[str, str] | None = None) -> Path:
    env_map = os.environ if env is None else env
    raw = str(env_map.get("DOCKER_CONFIG") or "").strip()
    if raw:
        return Path(raw) / "config.json"
    return Path.home() / ".docker" / "config.json"


def _docker_helper_binaries(*, env: dict[str, str] | None = None) -> list[str]:
    config_path = _docker_config_path(env=env)
    if not config_path.is_file():
        return []
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []

    helpers: list[str] = []

    creds_store = str(payload.get("credsStore") or "").strip()
    if creds_store:
        helpers.append(f"docker-credential-{creds_store}")

    raw_cred_helpers = payload.get("credHelpers")
    if isinstance(raw_cred_helpers, dict):
        for helper_name in raw_cred_helpers.values():
            helper = str(helper_name or "").strip()
            if helper:
                helpers.append(f"docker-credential-{helper}")

    seen: set[str] = set()
    ordered: list[str] = []
    for helper in helpers:
        if helper in seen:
            continue
        seen.add(helper)
        ordered.append(helper)
    return ordered


def _candidate_docker_helper_dirs() -> list[Path]:
    candidates = [
        Path("/Applications/Docker.app/Contents/Resources/bin"),
        Path.home() / "Applications" / "Docker.app" / "Contents" / "Resources" / "bin",
    ]
    out: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out


def _prepare_harness_env() -> dict[str, str]:
    env = dict(os.environ)
    helpers = _docker_helper_binaries(env=env)
    if not helpers:
        return env

    path_value = str(env.get("PATH") or "")
    missing_helpers = [
        helper for helper in helpers if shutil.which(helper, path=path_value) is None
    ]
    if not missing_helpers:
        return env

    extra_dirs: list[str] = []
    for directory in _candidate_docker_helper_dirs():
        if not directory.is_dir():
            continue
        if any((directory / helper).is_file() for helper in missing_helpers):
            extra_dirs.append(str(directory))

    if not extra_dirs:
        return env

    existing_parts = [part for part in path_value.split(os.pathsep) if part]
    for directory in extra_dirs:
        if directory in existing_parts:
            continue
        existing_parts.append(directory)
    env["PATH"] = os.pathsep.join(existing_parts)
    return env


def _subprocess_timeout_seconds(*, timeout_sec: int) -> int:
    if timeout_sec <= 0:
        return _HARNESS_TIMEOUT_GRACE_SECONDS
    return int(timeout_sec) + _HARNESS_TIMEOUT_GRACE_SECONDS


def _kill_active_harness_process_group() -> None:
    global _ACTIVE_HARNESS_PGID
    if _ACTIVE_HARNESS_PGID is None:
        return
    try:
        os.killpg(_ACTIVE_HARNESS_PGID, signal.SIGKILL)
    except ProcessLookupError:
        pass
    _ACTIVE_HARNESS_PGID = None


def _forward_signal_to_active_harness(signum: int, _frame: object) -> None:
    _kill_active_harness_process_group()
    raise SystemExit(128 + int(signum))


def _run_harness_command(
    *, cmd: list[str], cwd: Path, timeout_sec: int
) -> subprocess.CompletedProcess[str]:
    global _ACTIVE_HARNESS_PGID
    env = _prepare_harness_env()
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    previous_sigint = signal.getsignal(signal.SIGINT)
    _ACTIVE_HARNESS_PGID = proc.pid
    signal.signal(signal.SIGTERM, _forward_signal_to_active_harness)
    signal.signal(signal.SIGINT, _forward_signal_to_active_harness)
    try:
        stdout, stderr = proc.communicate(
            timeout=_subprocess_timeout_seconds(timeout_sec=timeout_sec)
        )
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=int(proc.returncode or 0),
            stdout=stdout,
            stderr=stderr,
        )
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = proc.communicate()
        raise subprocess.TimeoutExpired(
            cmd=exc.cmd,
            timeout=exc.timeout,
            output=stdout if stdout else exc.output,
            stderr=stderr if stderr else exc.stderr,
        ) from exc
    finally:
        _ACTIVE_HARNESS_PGID = None
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)


def evaluate_with_harness(
    *,
    instance_id: str,
    dataset_name: str,
    split: str,
    timeout_sec: int,
    work_dir: Path,
    run_id_prefix: str,
    patch_text: str | None = None,
    gold: bool = False,
) -> HarnessEvalResult:
    work_dir = Path(work_dir)
    run_id = _build_run_id(
        prefix=run_id_prefix, instance_id=instance_id, patch_text=patch_text, gold=gold
    )

    predictions_path = "gold"
    predictions_file: Path | None = None
    if not gold:
        if patch_text is None:
            return HarnessEvalResult(
                completed=False,
                resolved=False,
                report_path=None,
                run_id=run_id,
                returncode=2,
                notes="missing_patch_text",
            )
        predictions_file = work_dir / f"predictions_{_safe_token(instance_id)}.jsonl"
        prediction = {
            "instance_id": instance_id,
            "model_name_or_path": "agent-economy-market",
            "model_patch": patch_text,
        }
        predictions_file.write_text(
            json.dumps(prediction, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        predictions_path = str(predictions_file)

    cmd = [
        sys.executable,
        "-m",
        "swebench.harness.run_evaluation",
        "-d",
        dataset_name,
        "-s",
        split,
        "-i",
        instance_id,
        "-p",
        predictions_path,
        "--max_workers",
        "1",
        "-t",
        str(int(timeout_sec)),
        "--force_rebuild",
        "false",
        "--cache_level",
        "env",
        "--clean",
        "false",
        "-id",
        run_id,
        "-n",
        "swebench",
        "--instance_image_tag",
        "latest",
        "--env_image_tag",
        "latest",
        "--rewrite_reports",
        "false",
        "--report_dir",
        str(work_dir),
        "--modal",
        "false",
    ]

    try:
        proc = _run_harness_command(
            cmd=cmd,
            cwd=_runner_dir(work_dir=work_dir),
            timeout_sec=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        result_path = work_dir / f"harness_{_safe_token(run_id)}.json"
        result_path.write_text(
            json.dumps(
                {
                    "cmd": cmd,
                    "returncode": None,
                    "stdout": exc.stdout,
                    "stderr": exc.stderr,
                    "timed_out": True,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        report, report_path = _load_report(work_dir=work_dir, run_id=run_id)
        return HarnessEvalResult(
            completed=False,
            resolved=False,
            report_path=None if report_path is None else str(report_path),
            run_id=run_id,
            returncode=2,
            notes="harness_timeout",
        )

    result_path = work_dir / f"harness_{_safe_token(run_id)}.json"
    result_path.write_text(
        json.dumps(
            {
                "cmd": cmd,
                "returncode": int(proc.returncode),
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "timed_out": False,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    report, report_path = _load_report(work_dir=work_dir, run_id=run_id)
    if report is not None:
        resolved = _resolved_from_report(report=report, instance_id=instance_id)
        return HarnessEvalResult(
            completed=True,
            resolved=resolved,
            report_path=str(report_path) if report_path else None,
            run_id=run_id,
            returncode=0 if resolved else 1,
            notes=None,
        )

    summary, summary_path = _load_summary(work_dir=work_dir, run_id=run_id)
    summary_result = _result_from_summary(
        instance_id=instance_id,
        run_id=run_id,
        summary=summary,
        summary_path=summary_path,
    )
    if summary_result is not None:
        return summary_result

    if proc.returncode != 0:
        return HarnessEvalResult(
            completed=False,
            resolved=False,
            report_path=None if report_path is None else str(report_path),
            run_id=run_id,
            returncode=int(proc.returncode),
            notes="harness_failed",
        )

    return HarnessEvalResult(
        completed=False,
        resolved=False,
        report_path=None if report_path is None else str(report_path),
        run_id=run_id,
        returncode=2,
        notes="missing_report",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate a SWE-bench instance patch via official harness"
    )
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--patch-file", type=Path, default=None)
    parser.add_argument("--gold", action="store_true")
    parser.add_argument("--dataset-name", default=DEFAULT_DATASET_NAME)
    parser.add_argument("--split", default=DEFAULT_SPLIT)
    parser.add_argument("--timeout-sec", type=int, default=1800)
    parser.add_argument("--run-id-prefix", default="ae_phase2")
    args = parser.parse_args()

    if not args.gold and args.patch_file is None:
        raise SystemExit("--patch-file is required unless --gold is set")

    patch_text = None
    if args.patch_file is not None:
        patch_text = Path(args.patch_file).read_text(encoding="utf-8")

    result = evaluate_with_harness(
        instance_id=str(args.instance_id),
        dataset_name=str(args.dataset_name),
        split=str(args.split),
        timeout_sec=int(args.timeout_sec),
        work_dir=Path.cwd(),
        run_id_prefix=str(args.run_id_prefix),
        patch_text=patch_text,
        gold=bool(args.gold),
    )

    print(
        json.dumps(
            {
                "instance_id": str(args.instance_id),
                "completed": bool(result.completed),
                "resolved": bool(result.resolved),
                "report_path": result.report_path,
                "run_id": result.run_id,
                "returncode": int(result.returncode),
                "notes": result.notes,
            },
            ensure_ascii=False,
        )
    )

    if not result.completed:
        raise SystemExit(2)
    if not result.resolved:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
