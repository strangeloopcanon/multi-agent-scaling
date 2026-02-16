from __future__ import annotations

import json
from collections import Counter, defaultdict
from statistics import mean
from typing import Any
from urllib.request import urlopen

DEFAULT_EXTERNAL_EVIDENCE_URL = (
    "https://raw.githubusercontent.com/SWE-bench/swe-bench.github.io/master/data/leaderboards.json"
)
_SWE_LITE_ROWS_URL = (
    "https://datasets-server.huggingface.co/rows?"
    "dataset=princeton-nlp%2FSWE-bench_Lite&config=default&split=test&offset={offset}&length={length}"
)

# External evidence rows are intentionally explicit so the run is reproducible.
MODEL_EVIDENCE_ROW_BY_REF: dict[str, str] = {
    "openai:gpt-5.2-2025-12-11": "GPT-5.2 (2025-12-11)",
    "openai:gpt-5.2-pro-2025-12-11": "GPT-5.2 (2025-12-11) (high reasoning)",
    "openai:gpt-5-mini-2025-08-07": "GPT-5 mini (2025-08-07) (medium reasoning)",
    "anthropic:claude-sonnet-4-5-20250929": "Claude 4.5 Sonnet (20250929)",
    "anthropic:claude-opus-4-5-20251101": "Claude 4.5 Opus medium (20251101)",
    "google:models/gemini-3-pro-preview": "Gemini 3 Pro Preview (2025-11-18)",
}


def _read_json_url(url: str, *, timeout_s: float = 30.0) -> dict[str, Any]:
    with urlopen(url, timeout=timeout_s) as resp:
        return json.load(resp)


def _parse_test_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(v) for v in parsed if str(v).strip()]


def _load_swebench_lite_rows(*, page_len: int = 100) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    offset = 0
    while True:
        payload = _read_json_url(_SWE_LITE_ROWS_URL.format(offset=offset, length=page_len))
        rows = list(payload.get("rows") or [])
        if not rows:
            break
        for wrapped in rows:
            row = wrapped.get("row") if isinstance(wrapped, dict) else None
            if not isinstance(row, dict):
                continue
            instance_id = str(row.get("instance_id") or "").strip()
            if not instance_id:
                continue
            out[instance_id] = {
                "instance_id": instance_id,
                "repo": str(row.get("repo") or ""),
                "problem_statement": str(row.get("problem_statement") or ""),
                "base_commit": str(row.get("base_commit") or ""),
                "fail_to_pass": _parse_test_list(row.get("FAIL_TO_PASS")),
                "pass_to_pass": _parse_test_list(row.get("PASS_TO_PASS")),
            }
        offset += len(rows)
        if len(rows) < page_len:
            break
    return out


def _extract_bash_only_rows(*, leaderboard: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows_by_name: dict[str, dict[str, Any]] = {}
    for board in list(leaderboard.get("leaderboards") or []):
        if str(board.get("name") or "") != "bash-only":
            continue
        for row in list(board.get("results") or []):
            name = str(row.get("name") or "")
            if not name:
                continue
            rows_by_name[name] = row
    return rows_by_name


def _difficulty_band(success_rate: float) -> str:
    if success_rate <= (1.0 / 3.0):
        return "hard"
    if success_rate < (2.0 / 3.0):
        return "medium"
    return "easy"


def _band_targets(*, limit: int) -> dict[str, int]:
    # Prefer balanced strata, then let selection fill from whichever strata has supply.
    hard = limit // 3
    medium = limit // 3
    easy = limit - hard - medium
    return {"hard": hard, "medium": medium, "easy": easy}


def _pick_diverse_by_repo(
    *,
    candidates: list[dict[str, Any]],
    target: int,
) -> list[dict[str, Any]]:
    by_repo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in sorted(candidates, key=lambda r: (str(r["repo"]), str(r["instance_id"]))):
        by_repo[str(rec["repo"])].append(rec)
    repos = sorted(by_repo.keys())
    out: list[dict[str, Any]] = []
    idx = 0
    while len(out) < target and repos:
        repo = repos[idx % len(repos)]
        bucket = by_repo[repo]
        if bucket:
            out.append(bucket.pop(0))
        idx += 1
        repos = [r for r in repos if by_repo[r]]
    return out


def _build_acceptance_hints(*, fail_to_pass: list[str], pass_to_pass: list[str]) -> list[str]:
    hints: list[str] = []
    for node_id in fail_to_pass[:5]:
        hints.append(f"FAIL_TO_PASS: {node_id}")
    for node_id in pass_to_pass[:3]:
        hints.append(f"PASS_TO_PASS: {node_id}")
    if not hints:
        hints.append("Run the SWE-bench verification tests for this instance.")
    return hints


def build_external_covered_lite_phase1(
    *,
    model_refs: list[str],
    task_limit: int,
    leaderboard_url: str = DEFAULT_EXTERNAL_EVIDENCE_URL,
) -> tuple[list[dict[str, Any]], dict[tuple[str, str, str], dict[str, Any]], dict[str, Any]]:
    if task_limit <= 0:
        raise ValueError("task_limit must be > 0")
    if not model_refs:
        raise ValueError("model_refs must not be empty")

    missing = [m for m in model_refs if m not in MODEL_EVIDENCE_ROW_BY_REF]
    if missing:
        raise ValueError(f"unsupported model refs for external evidence: {missing}")

    leaderboard = _read_json_url(leaderboard_url)
    bash_rows = _extract_bash_only_rows(leaderboard=leaderboard)

    row_name_by_model = {m: MODEL_EVIDENCE_ROW_BY_REF[m] for m in model_refs}
    row_by_model: dict[str, dict[str, Any]] = {}
    for model_ref, row_name in row_name_by_model.items():
        row = bash_rows.get(row_name)
        if row is None:
            raise ValueError(f"missing bash-only row in leaderboard: {row_name}")
        details = row.get("per_instance_details")
        if not isinstance(details, dict) or not details:
            raise ValueError(f"row missing per_instance_details: {row_name}")
        row_by_model[model_ref] = row

    common_ids = set.intersection(
        *[
            set((row.get("per_instance_details") or {}).keys())  # type: ignore[union-attr]
            for row in row_by_model.values()
        ]
    )
    lite_rows = _load_swebench_lite_rows()
    covered_ids = sorted(iid for iid in common_ids if iid in lite_rows)
    if not covered_ids:
        raise ValueError("no SWE-bench Lite IDs overlap selected external evidence rows")

    candidate_rows: list[dict[str, Any]] = []
    for iid in covered_ids:
        values: list[float] = []
        for model_ref in model_refs:
            row = row_by_model[model_ref]
            details = row.get("per_instance_details") or {}
            info = details.get(iid) if isinstance(details, dict) else None
            if isinstance(info, dict):
                values.append(1.0 if bool(info.get("resolved")) else 0.0)
        sr = mean(values) if values else 0.0
        lite = lite_rows[iid]
        candidate_rows.append(
            {
                "instance_id": iid,
                "repo": str(lite["repo"]),
                "problem_statement": str(lite["problem_statement"]),
                "base_commit": str(lite["base_commit"]),
                "fail_to_pass": list(lite["fail_to_pass"]),
                "pass_to_pass": list(lite["pass_to_pass"]),
                "success_rate": float(sr),
                "band": _difficulty_band(float(sr)),
            }
        )

    targets = _band_targets(limit=task_limit)
    selected: list[dict[str, Any]] = []
    for band in ("hard", "medium", "easy"):
        band_rows = [r for r in candidate_rows if str(r["band"]) == band]
        selected.extend(_pick_diverse_by_repo(candidates=band_rows, target=targets[band]))

    if len(selected) < task_limit:
        already = {str(r["instance_id"]) for r in selected}
        remainder = [
            r
            for r in sorted(
                candidate_rows,
                key=lambda x: (
                    {"hard": 0, "medium": 1, "easy": 2}[str(x["band"])],
                    str(x["repo"]),
                    str(x["instance_id"]),
                ),
            )
            if str(r["instance_id"]) not in already
        ]
        for row in remainder:
            if len(selected) >= task_limit:
                break
            selected.append(row)

    selected = selected[:task_limit]
    selected_ids = [str(r["instance_id"]) for r in selected]
    selected_set = set(selected_ids)

    tasks: list[dict[str, Any]] = []
    for row in selected:
        iid = str(row["instance_id"])
        tasks.append(
            {
                "benchmark": "swebench",
                "task_id": iid,
                "title": f"SWE-bench fix: {iid}",
                "description": "\n".join(
                    [
                        "You are fixing a SWE-bench Lite issue.",
                        f"Repository: {row['repo']}",
                        f"Base commit: {row['base_commit']}",
                        "",
                        str(row["problem_statement"]).strip(),
                    ]
                ).strip(),
                "acceptance": _build_acceptance_hints(
                    fail_to_pass=list(row.get("fail_to_pass") or []),
                    pass_to_pass=list(row.get("pass_to_pass") or []),
                ),
                "meta": {
                    "repo": str(row["repo"]),
                    "success_rate": float(row["success_rate"]),
                    "difficulty_band": str(row["band"]),
                },
            }
        )

    labels: dict[tuple[str, str, str], dict[str, Any]] = {}
    for model_ref in model_refs:
        evidence_row = row_by_model[model_ref]
        evidence_name = str(evidence_row.get("name") or row_name_by_model[model_ref])
        evidence_date = str(evidence_row.get("date") or "")
        details = evidence_row.get("per_instance_details") or {}
        for iid in selected_set:
            info = details.get(iid) if isinstance(details, dict) else None
            if not isinstance(info, dict):
                continue
            resolved = bool(info.get("resolved"))
            labels[("swebench", iid, model_ref)] = {
                "outcome": 1 if resolved else 0,
                "outcome_status": "pass" if resolved else "fail",
                "attempted": True,
                "outcome_source": (
                    "external_proxy_gpt52_high"
                    if model_ref == "openai:gpt-5.2-pro-2025-12-11"
                    else "external_exact"
                ),
                "external_row_name": evidence_name,
                "external_row_date": evidence_date,
                "external_api_calls": int(info.get("api_calls") or 0),
                "external_cost": float(info.get("cost") or 0.0),
            }

    manifest = {
        "evidence_url": leaderboard_url,
        "model_row_mapping": row_name_by_model,
        "selected_models": list(model_refs),
        "candidate_pool_size": len(candidate_rows),
        "selected_task_count": len(selected),
        "band_targets": targets,
        "band_counts_selected": dict(Counter(str(r["band"]) for r in selected)),
        "selected_task_ids": selected_ids,
        "evidence_rows": {
            model_ref: {
                "name": str(row_by_model[model_ref].get("name") or ""),
                "date": str(row_by_model[model_ref].get("date") or ""),
                "resolved": float(row_by_model[model_ref].get("resolved") or 0.0),
            }
            for model_ref in model_refs
        },
    }

    return tasks, labels, manifest
