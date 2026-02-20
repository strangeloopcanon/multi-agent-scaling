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
