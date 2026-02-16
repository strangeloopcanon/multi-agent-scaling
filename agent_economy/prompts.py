from __future__ import annotations

from agent_economy.engine import ReadyTask
from agent_economy.schemas import (
    DiscussionMessage,
    PaymentRule,
    SubmissionKind,
    VerifyMode,
    WorkerRuntime,
)
from agent_economy.submission import submission_workspace_relpath


def system_prompt(*, worker: WorkerRuntime, persona: str | None = None) -> str:
    persona_text = persona or "You are an autonomous software agent."
    return "\n".join(
        [
            "You are an autonomous worker in a task marketplace.",
            "Objective: maximize your credits by winning tasks you can complete "
            "and passing verification.",
            "Important: there may be private/hidden checks beyond the public acceptance commands.",
            "",
            "Output rules:",
            "- For bidding: output valid JSON only (no markdown).",
            "- For patch tasks: follow the user prompt exactly.",
            "",
            f"Identity: worker_id={worker.worker_id}",
            f"Balance: {worker.balance}",
            f"Reputation: {worker.reputation:.2f}",
            f"Persona: {persona_text}",
        ]
    )


def bid_prompt(
    *,
    worker: WorkerRuntime,
    ready_tasks: list[ReadyTask],
    payment_rule: PaymentRule,
    max_bids: int,
    discussion_history: list[DiscussionMessage],
    penalty_mode: str = "reputation",
    penalty_fraction: float = 0.10,
) -> str:
    lines: list[str] = []
    lines.append(
        "Submit bids (asks) for ready tasks. Bid only when you can deliver a passing result."
    )
    if str(penalty_mode) == "direct_penalty":
        lines.append("Settlement mode: direct_penalty")
        lines.append(
            "Clearing rule: score = p_success*bounty - ask - expected_cost - (1-p_success)*failure_penalty, "
            f"where failure_penalty = {float(penalty_fraction):.3f}*bounty*(0.5 + p_success). "
            "Reputation does not enter score in this mode. Bids with score <= 0 will not win."
        )
    else:
        lines.append("Settlement mode: reputation")
        lines.append(
            "Clearing rule: score = rep*p_success*bounty - ask - expected_cost - (1-p_success)*failure_penalty, "
            "where failure_penalty = 0.5*bounty*clamp((rep-0.5)/0.75, 0, 1). Bids with score <= 0 will not win."
        )
    lines.append(
        "expected_cost is an estimate of your token cost based on model pricing and past runs, "
        "so keep your work lean and bid accordingly."
    )
    if payment_rule == PaymentRule.ASK:
        lines.append(
            "Payout rule: if you win and pass verification, you are paid your ask "
            "(first-price procurement)."
        )
    else:
        lines.append("Payout rule: if you win and pass verification, you are paid the task bounty.")
    lines.append("Settlement notes:")
    lines.append("- usage_cost is always debited after an attempt (pass or fail).")
    lines.append(
        "- Overconfident failures are penalized more: higher reported p_success increases fail penalties."
    )
    lines.append(
        "- For judges verification, part of payout is held back until the whole run completes."
    )
    lines.append(f"Constraints: at most {max_bids} bids this round.")
    lines.append("")

    if discussion_history:
        lines.append("Public Discussion Board:")
        lines.append(
            "(Check this for recent updates, blockers, or clarifications from other agents.)"
        )
        for msg in discussion_history[-20:]:  # Show last 20
            lines.append(f"[{msg.ts.strftime('%H:%M:%S')}] {msg.sender}: {msg.message}")
        lines.append("")

    lines.append("Ready tasks:")
    for t in ready_tasks:
        spec = t.spec
        rt = t.runtime
        lines.append(
            f"- {spec.id}: {spec.title} (bounty={rt.bounty_current} fail_count={rt.fail_count})"
        )
        if spec.description.strip():
            blurb = spec.description.strip().splitlines()[0].strip()
            if blurb:
                lines.append(f"  {blurb}")
        lines.append("  public_acceptance:")
        for cmd in spec.acceptance:
            lines.append(f"    - {cmd.cmd}")
        if spec.verify_mode == VerifyMode.JUDGES:
            lines.append("  verification: judges (model voting on diff + outputs)")
        elif spec.verify_mode == VerifyMode.MANUAL:
            lines.append("  verification: manual review")
        lines.append(f"  submission: {spec.submission_kind.value}")
    lines.append("")
    lines.append(
        """Return JSON:
{
  "bids": [
    {"task_id":"T1","ask":50,"p_success":0.7,"eta_minutes":20,"notes":"optional"}
  ],
  "discussion": "optional public message (use to report blockers, clarify specs, or coordinate)"
}"""
    )
    return "\n".join(lines)


def patch_prompt(
    *,
    task: ReadyTask,
    files: dict[str, str],
    discussion_history: list[DiscussionMessage],
) -> str:
    spec = task.spec
    lines: list[str] = []
    lines.append("You are assigned a task. Produce a patch that passes verification.")
    lines.append(
        "Assume there may be hidden checks; implement the spec robustly, "
        "not just for visible tests."
    )
    lines.append("")
    lines.append("Patch output format:")
    lines.append("- Prefer one or more full-file blocks (most robust):")
    lines.append("  BEGIN_FILE <relative_path>")
    lines.append("  <full file contents>")
    lines.append("  END_FILE")
    lines.append("- Alternatively, you may output a unified diff that starts with 'diff --git',")
    lines.append("  but only if you are confident it will apply cleanly via `git apply`.")
    lines.append(
        "  If you use a diff, include full file headers for every file (no patch fragments)."
    )
    lines.append("Do not wrap the patch in JSON. Do not use markdown fences.")
    lines.append("")
    lines.append(f"Task: {spec.id} — {spec.title}")
    if spec.description.strip():
        lines.append(spec.description.strip())
    lines.append("")

    if discussion_history:
        lines.append("Public Discussion Board:")
        lines.append("(Review this for any updates or warnings relevant to your task.)")
        for msg in discussion_history[-20:]:
            lines.append(f"[{msg.ts.strftime('%H:%M:%S')}] {msg.sender}: {msg.message}")
        lines.append("")

    if spec.verify_mode == VerifyMode.JUDGES:
        lines.append("Verification: independent model judges will vote on this patch.")
        lines.append("")
        lines.append(
            "Settlement: passing judges is provisional; part of payout is held back until the whole run completes."
        )
        lines.append("")
    lines.append("Cost: usage_cost (token cost) is debited for this attempt regardless of outcome.")
    lines.append("")
    lines.append("Public acceptance commands (hidden checks may also run):")
    for cmd in spec.acceptance:
        lines.append(f"- {cmd.cmd}")
    lines.append("")
    lines.append("Allowed paths (patch must only touch these):")
    for p in spec.allowed_paths:
        lines.append(f"- {p}")
    lines.append("")
    lines.append("You are given these files:")
    for path in sorted(files.keys()):
        lines.append(f"\n--- FILE: {path} ---\n{files[path]}")
    lines.append("")
    lines.append("Output the patch now (BEGIN_FILE blocks preferred) and nothing else.")
    return "\n".join(lines)


def submission_prompt(
    *,
    task: ReadyTask,
    files: dict[str, str],
    discussion_history: list[DiscussionMessage],
) -> str:
    spec = task.spec
    if spec.submission_kind not in {SubmissionKind.TEXT, SubmissionKind.JSON}:
        raise ValueError(f"submission_prompt requires text/json kind, got {spec.submission_kind}")

    lines: list[str] = []
    lines.append("You are assigned a task. Produce the best possible final answer.")
    lines.append(
        "Assume there may be hidden checks; satisfy the intent robustly, not just visible checks."
    )
    lines.append("")
    lines.append("Submission output format:")
    if spec.submission_kind == SubmissionKind.JSON:
        lines.append("- Output valid JSON only.")
        lines.append("- Do not wrap in markdown fences.")
    else:
        lines.append("- Output plain text only.")
        lines.append("- Do not wrap in markdown fences.")
    lines.append(
        f"- Your output will be saved to {submission_workspace_relpath(kind=spec.submission_kind)} "
        "inside the sandbox workspace before verification runs."
    )
    lines.append("")
    lines.append(f"Task: {spec.id} — {spec.title}")
    if spec.description.strip():
        lines.append(spec.description.strip())
    lines.append("")

    if discussion_history:
        lines.append("Public Discussion Board:")
        lines.append("(Review this for any updates or warnings relevant to your task.)")
        for msg in discussion_history[-20:]:
            lines.append(f"[{msg.ts.strftime('%H:%M:%S')}] {msg.sender}: {msg.message}")
        lines.append("")

    if spec.verify_mode == VerifyMode.JUDGES:
        lines.append("Verification: independent model judges will review your submission.")
        lines.append("")
        lines.append(
            "Settlement: passing judges is provisional; part of payout is held back until the whole run completes."
        )
        lines.append("")
    lines.append("Cost: usage_cost (token cost) is debited for this attempt regardless of outcome.")
    lines.append("")
    lines.append("Public acceptance commands (hidden checks may also run):")
    for cmd in spec.acceptance:
        lines.append(f"- {cmd.cmd}")
    lines.append("")
    lines.append("You are given these files:")
    for path in sorted(files.keys()):
        lines.append(f"\n--- FILE: {path} ---\n{files[path]}")
    lines.append("")
    lines.append("Output the final submission now and nothing else.")
    return "\n".join(lines)
