# Phase II Market Dynamics

This document details the operational behavior, constraints, and failures observed as the market executed live SWE-bench tasks during Phase II.

## 1. The Scaffold Gap

External evaluations in Phase I used mature scaffolds that provide models with:
- Interactive shells for repository exploration.
- Direct test execution and immediate, in-context feedback.
- Multi-turn iteration (try, observe test results, revise).
- Structured file-editing tools vs raw diff output.

Our Agent Economy runs a "weak" scaffold: **single-shot generation of raw diffs or `BEGIN_FILE` blocks with minimal prior context.**

### Quantifying the Handicap
By running a single model (GPT-5.2) solo on our scaffold for the same 50 tasks evaluated externally, we can precisely quantify this gap:
- **GPT-5.2 External (Strong Scaffold):** 74%
- **GPT-5.2 Solo (Our Scaffold):** 48%

This represents a **26-percentage-point penalty (13 tasks)** strictly attributable to the environment. The market's primary achievement in Phase II was recovering ~40% of this lost performance purely through model diversity and retries.

## 2. Allocation Inefficiencies

Despite beating the best individual model (58% vs 48% on identical scaffolds), the market failed to reach the 84% Oracle ceiling due to severe assignment skew.

### Win Allocation Skew (N=50 Tasks)
| Model | Assignments Won | Passes | Conversion Rate | External Rate |
|-------|----------------:|-------:|----------------:|--------------:|
| Gemini | 42 | 21 | 50.0% | 66.0% |
| GPT-5-mini | 20 | 2 | **10.0%** | 60.0% |
| **GPT-5.2** | **9** | **3** | **33.3%** | **74.0%** |
| GPT-5.2-pro | 6 | 1 | 16.7% | 70.0% |
| Claude Opus | 4 | 2 | 50.0% | 66.0% |
| Claude Sonnet | 1 | 0 | 0.0% | 62.0% |

**The Bottleneck:**
Gemini dominates the board, receiving 51% of all assignments and delivering 72% of all passes. It converts at a respectable 50%.
However, GPT-5-mini is a massive drag on the system: it absorbs 24% of assignments but converts at only 10%. Meanwhile, the strongest external model (GPT-5.2) receives only 11% of assignments.

Both market and solo share an identical first-attempt pass rate (18/50, 36%); the market's +10pp advantage comes entirely from diverse-model retry (rescuing 11/32 failures vs solo's 6/32 with same-model retry). Better first-attempt allocation — routing tasks to models with genuine capability rather than high self-confidence — would push the market's attempt-1 rate toward the oracle ceiling and reduce its dependence on the retry mechanism.

This confirms the Phase I hypothesis: the `direct_penalty` scoring formula over-indexes on self-assessed confidence, allowing weaker, aggressive bidders to crowd out stronger, conservative bidders.

## 3. The Failure of Decomposition

Early in Phase II, we attempted to use a Planner model to decompose monolithic SWE-bench tasks into sub-tasks (`dag_mode=planner_market`).

**Observation (Gate A Debug Run, `astropy-14182`):**
- The Planner (GPT-5.2) successfully decomposed the task into a 7-step chain.
- 6 of the 7 intermediate sub-tasks completed successfully.
- However, the final "root" patching task failed all 3 of its attempts.
- The root task was assigned to Sonnet/Opus, neither of which can solve it individually. GPT-5.2, which *can* solve it, spent its turn planning instead of patching.

**Conclusion:**
Decomposition adds value when sub-tasks are genuinely independent and parallelizable. SWE-bench bug fixes are not; they are inherently one-person, one-session tasks requiring unified context. The diagnosis/localization context posted to the discussion board did not help models that couldn't solve the task monolithically.

For SWE-bench, the market's value proposition is **model selection and diversity routing**, not task splitting. The primary Phase II benchmark successfully reverted to monolithic root tasks.

## 4. Thin Markets Produce Random Assignment

During early smoke runs, we observed that in 10 out of 24 root-task first rounds, only a single model submitted a bid (due to timeouts or parsing errors).
- The winner in those rounds was simply whoever managed to respond, regardless of capability.
- When 3+ bidders participated, winner selection became materially different and more spread across models.

To counteract this, the final 50-task evaluation utilized **Forced Bids**, ensuring that empty-bidding models were assigned conservative fallback bids, preserving market depth and preventing strong models from dropping out of retries by accident.
