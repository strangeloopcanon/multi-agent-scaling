# System Learnings

Operational lessons and architectural observations gathered across Phase I and Phase II executions.

## 1. INFRA Noise vs Docker Stability

Early Phase II runs saw roughly 25% of attempts end in `INFRA` status. Initial assumptions blamed Docker daemon downtime or evaluation timeouts.

**Root Cause Analysis:**
Detailed inspection of SWE-bench harness logs revealed:
1. **Patch apply failures** (diff hunks not matching repo state in container) constituted the vast majority of `INFRA` failures. The model generated a diff with context lines that didn't align, and `git apply` failed.
2. **Python version incompatibility** accounted for chronic failures on specific repos (e.g., `psf__requests-2317` failing on Python 3.10+ due to `collections.Mapping` removal).
3. **Docker image build timeouts** accounted for a tiny fraction (1 in 5 events).

**The Fix:**
- Allowing models to output `BEGIN_FILE` blocks instead of forcing raw `diff --git`. The executor handles `BEGIN_FILE` by writing the file directly and generating a clean `patch.diff` against the workspace, eliminating hunk mismatch errors.
- Broadening the `report.json` lookup logic in the evaluation wrapper. The harness occasionally drops `report.json` but successfully writes an `agent-economy-market.<run_id>.json` summary file. Parsing this summary correctly classified valid evaluation failures instead of defaulting to `INFRA`.

These two fixes reduced `INFRA` misclassification and patch-format-related failures, improving the interpretability of later runs. Across the 50-task market rollup, `INFRA` remains present (`3/50`) and should still be treated as an active reliability concern.

## 2. Docker Cache State

While `INFRA` noise was primarily driven by patch formatting, Docker health does affect outcomes.

A control run of GPT-5.2 solo before a clean (`solo_gpt52_10_20260218T210509Z`) resulted in 8 `INFRA` failures on 10 tasks.
After a full Docker reset (`docker system prune -af --volumes`), the same 10-task setup (`solo_gpt52_10_cleanDocker_20260218T225012Z`) saw `INFRA` drop to 4, and 1 task moved to `PASS`.

Docker state management is critical for running SWE-bench at scale, though it does not explain the broader performance gap between the market and individual models.

## 3. External Labels are Weakly Predictive of Scaffold-Specific Outcomes

Phase I external labels (from standard SWE-bench interactive scaffolds) proved only weakly predictive of what our specific single-shot scaffold produces.

- **Our scaffold passes tasks no model passes externally.** e.g., Claude Sonnet passed `matplotlib-23476` and Gemini passed `pylint-7080` in the market, despite external labels stating neither model should be capable of solving them.
- **External passers sometimes fail in our scaffold.** e.g., GPT-5.2 passes `astropy-14182` externally but failed when assigned in the market.
- **Conversion rate when routing to an external passer is ~50%.** Even when the market successfully assigned a task to a model known to pass it externally, the conversion rate was only around 50%.

The external labels come from different scaffolds with different prompts, timeouts, and execution environments. They are invaluable for task selection and estimating theoretical ceilings (the Oracle), but they cannot be used to predict precise per-model, per-task outcomes in a different environment. This reinforces the need for dynamic routing and retries.

## 4. 2026-04-01 Codex-Direct 10-Task Sample

A clean 10-task Codex-direct Phase II sample landed at **2 / 10 passes (20%)** on the same first-10-task slice where the archived market run scored **3 / 10** and the archived solo GPT-5.2 run scored **1 / 10**.

The result matters less for the raw pass rate than for the reliability lessons it surfaced:

- The SWE-bench harness must run from a neutral working directory. Running it from inside the task repo lets task packages such as `requests` shadow harness dependencies and creates false failures before grading even begins.
- Patch construction must come from the sandbox repo's own git state, not from a raw directory diff. The raw diff path leaked ignored build outputs and benchmark side files into submissions, which turned valid edits into patch-apply failures.
- After those fixes, Codex-direct produced clean end-to-end outcomes across the sample. The remaining notable operational miss was `matplotlib__matplotlib-23476`, where the executor hit the 900-second limit without returning a patch.

The per-task pattern was mixed rather than uniformly weak. Codex-direct solved `astropy__astropy-14182` and `scikit-learn__scikit-learn-25747`, both of which the archived market and solo GPT-5.2 runs missed on this slice, but it failed `django__django-11964`, `django__django-12308`, and `pylint-dev__pylint-7080`, which the market had solved.

## 5. 2026-04-01 Codex-Direct Full-50 False Failure

The first full-50 Codex-direct rerun on 2026-04-01 produced an alarming early readout of **0 / 44 completed**. That result was not trustworthy and should not be treated as a real benchmark outcome.

**What happened:**
- The executor built submission patches by asking `git` whether the sandbox workspace was "inside a git repo."
- The copied SWE-bench task workspaces did not have their own `.git` metadata, but they lived underneath the main `agent-economy` repo on disk.
- `git` therefore latched onto the host repo instead of the copied task workspace.
- Because the host repo ignores `runs/`, the patch builder concluded there were **no workspace changes produced**, even when Codex had edited task files inside the sandbox.

**Why it mattered:**
- The ledger recorded repeated verification failures with `no workspace changes produced`.
- The worker notes still described concrete repo-specific edits, which made the apparent `0 / 44` collapse look like model failure when it was really a packaging bug.
- Every affected task was effectively graded as an empty submission.

**Fix:**
- Only use the git-based patch path when the sandbox workspace itself contains local git metadata (`.git` file or directory).
- Otherwise, fall back to the directory-to-directory diff path, which correctly compares the prepared workspace copy against the edited sandbox copy.

This bug invalidated the original `codex_direct_market50_full50_20260401T185430Z` run. After the fix, replaying the saved Astropy task sandbox produced the expected two-file patch instead of an empty result.

## 6. 2026-04-01 Relaxed-Time GPT-5.2 Codex Sanity Check

A one-task Codex-direct sanity run with the underlying model fixed to **GPT-5.2** and the execution budget doubled from **900 seconds** to **1800 seconds** completed cleanly and passed end to end.

**What was verified:**
- Clean preflight state: no leftover benchmark containers, images, or volumes before launch.
- Worker identity stayed pinned to `openai:gpt-5.2-2025-12-11`.
- Codex produced a real patch, the patch was handed off to the grader, and the grader generated the normal verification artifacts.
- The final benchmark result agreed with the checker output: `astropy__astropy-14182` resolved successfully.
- No executor-timeout marker appeared on the winning attempt.

**Why it matters:**
- This separates the earlier 900-second misses from the more serious infrastructure bugs. The Codex + GPT-5.2 path can now complete a full Phase II loop cleanly when given a longer budget.
- The remaining open question is no longer whether the pipeline works. It is whether Codex + GPT-5.2 can produce competitive results under the original 900-second budget, or whether its main weakness in this scaffold is speed.

## 7. 2026-04-02 Full 50-Task Relaxed-Time Codex Follow-Up

The full relaxed-time follow-up completed at **35 / 50 passes (70%)** with Codex-direct on **GPT-5.2** and the execution limit doubled from **900 seconds** to **1800 seconds**.

**What changed relative to the published benchmark:**
- Same 50-task slice.
- Same underlying model family as the published solo GPT-5.2 baseline.
- Same broad Phase II scaffold and scoring setup.
- Different execution path (`codex-direct`) and a doubled per-task time budget.

**What the result says:**
- The run beat the published market baseline (**29 / 50**) by **6 tasks**.
- It beat the published solo GPT-5.2 baseline (**24 / 50**) by **11 tasks**.
- Gains concentrated in `django` and `sympy`. `matplotlib` remained a clear weak spot.
- The run was expensive: **321.3M total tokens** across **66 attempts**.

**How to interpret it:**
- This is strong evidence that the Codex + GPT-5.2 setup was mostly constrained by time budget, not by an inability to solve the tasks at all.
- It is **not** a replacement for the published 900-second benchmark. The published market-vs-solo comparison should stay as-is.
- The relaxed-time result is better thought of as a diagnostic upper bound on what this execution path can do when given more runway.
