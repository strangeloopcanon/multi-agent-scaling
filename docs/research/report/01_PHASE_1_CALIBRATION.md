# Phase I Calibration: The Confidence Problem and Oracle Ceiling

## 1. The Oracle Ceiling

Phase I evaluated 6 leading frontier models against 93 tasks from SWE-bench Lite using external scaffolds (which feature interactive shells, file editing tools, and multi-turn iteration).

This evaluation produced a baseline capability profile for each model, identifying precisely which tasks are solvable by which model when given an optimal environment.

### Pass Rates on External Scaffolds (N=50 Slice)
For the 50-task subset analyzed in Phase II, external performance breaks down as follows:

| Model | Pass Rate | Absolute Passes |
|-------|----------:|----------------:|
| GPT-5.2 (2025-12-11) | 74.0% | 37/50 |
| GPT-5.2-pro (2025-12-11) | 70.0% | 35/50 |
| Gemini 3 Pro Preview | 66.0% | 33/50 |
| Claude Opus 4.5 | 66.0% | 33/50 |
| Claude Sonnet 4.5 | 62.0% | 31/50 |
| GPT-5-mini | 60.0% | 30/50 |

**The Theoretical Ceiling:** If a perfectly omniscient system (the "Oracle") could pick exactly the right model for each task on its first attempt, the maximum possible pass rate would be **84% (42/50)**.

This establishes the absolute upper bound for the market: *Can the market route assignments efficiently enough to capture the 84% oracle ceiling?*

## 2. Self-Assessed Confidence is Noise

The primary mechanism for routing in the Agent Economy is the `p_success` bid submitted by each model. However, Phase I calibration proved that self-reported confidence is fundamentally flawed as an allocation signal.

Brier skill scores computed across the 93-task set revealed:
- **Most models have near-zero or negative Brier skill scores.**
- **Self-reported confidence is not a useful predictor of actual task success.**

This finding was known prior to Phase II but became the defining bottleneck in the market's live execution.

### The "Overconfidence Table" in Practice
In a monolithic 10-task market run (Gate B), we observed exactly how weak calibration breaks the auction block:

| Task | GPT-5.2 Bid | Gemini Bid | Winner |
|------|------------|------------|--------|
| `astropy-14182` | ask=28, p=0.62 | ask=45, **p=0.85** | Gemini |
| `scikit-learn-25747`| ask=34, p=0.62 | ask=45, **p=0.85** | Gemini |
| `django-14155` | ask=28, p=0.62 | ask=45, **p=0.85** | Gemini |

*Note: Externally, GPT-5.2 passes 2 of 10 tasks while Gemini passes a different 2 of 10.*

**The Mechanics of Failure:**
Because the scoring formula (`p_success * bounty - ask - expected_cost`) heavily weights the reported probability of success, Gemini's extreme overconfidence (p=0.85) produces a `+20.7` point advantage over GPT-5.2 (p=0.62). GPT-5.2's lower ask price (28 vs 45) only claws back 17 points, resulting in a consistent loss.

The model most likely to solve the task (GPT-5.2, scoring 74% externally) is under-allocated because it bids honestly, while the models least likely to solve it win every round by over-promising. 

This sets the stage for Phase II: overcoming allocation inefficiencies and a rigid scaffold to prove the value of the market structure.
****

## Phase Ib: Self-Knowledge Calibration Intervention (2026-04-06)

We re-ran the Phase I direct calibration prompt with a simple self-knowledge intervention. Each model saw a short held-out history card summarizing its own prior pass rate, average stated confidence, and typical token underestimation before forecasting the current task.

The final rerun completed for all 6 models, yielding a clean 558-row result (93 tasks x 6 models). The first pass failed on both Anthropic models because of a provider authentication error; after refreshing credentials and rerunning only the missing rows, the output folder was repaired in place so the experiment now reads as one cohesive full run.

On the full six-model set, calibration still improved, though less dramatically than in the temporary four-model subset. Mean Brier score improved from `0.1835` to `0.1693`, and ECE improved from `0.1065` to `0.0616`. The intervention also pushed stated confidence upward, from `0.7268` to `0.8057`, against a realized pass rate of `0.7903`. Token forecasts became less severely underestimated: the median estimated-to-actual token ratio moved from `0.1929` to `0.2501`.

The auction consequence remained mixed rather than uniformly positive. Under the paper-aligned reserve-auction simulation (`penalty = 2`, `max_reserve = 1`, `legacy_pay_reserve`), mean realized profit moved from `$0.0852` to `$0.0819`, while the oracle gap narrowed slightly from `0.2357` to `0.2330`. The intervention again shifted allocation away from Gemini and toward the OpenAI workers. Gemini's mean realized profit fell by about `0.169`, GPT-5-mini, GPT-5.2, and GPT-5.2-pro each gained realized profit, Claude Opus gained slightly, and Claude Sonnet lost slightly.

Artifacts for this follow-up live under `runs/research/phase1/self_knowledge_direct_full_20260406T221032Z/`, including the repaired full raw run, the collated clean six-model JSONL files, and matched auction outputs for baseline versus self-knowledge prompting.
