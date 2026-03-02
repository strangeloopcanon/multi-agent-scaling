# Experiment Notes

Running observations from Phase I and Phase II that are worth keeping.

---

## The overconfidence table (2026-02-18)

Gate B, 10-task monolithic market run (`gateB_nodag_t2400_20260217T235325Z`).

gpt-5.2 passes 2/10 of these tasks externally; Gemini also passes 2/10 (different tasks). In the market, Gemini won 16/29 assignments (completing 2); gpt-5.2 won zero.

Here's what the bids looked like in round 1 across three tasks:

| Task | gpt-5.2 bid | Gemini bid | Winner |
|------|------------|------------|--------|
| astropy-14182 | ask=28, p=0.62 | ask=45, p=0.85 | Gemini |
| scikit-learn-25747 | ask=34, p=0.62 | ask=45, p=0.85 | Gemini |
| django-14155 | ask=28, p=0.62 | ask=45, p=0.85 | Gemini |

The pattern is identical every time. gpt-5.2 bids cheaply but modestly. Gemini bids expensively but claims very high confidence. The scoring formula (`p_success * bounty - ask - ...`) rewards the confidence claim so heavily that it overcomes the price difference. Then gpt-5.2 stops bidding entirely after round 1 on most tasks.

The model most likely to solve the task never gets assigned. The model least likely to solve it wins every round.

**Why it happens mechanically:** `0.23 * 90 = 20.7 point` advantage from Gemini's higher p_success vs only 17 points clawed back by gpt-5.2's lower ask. The `p_success * bounty` term dominates the score.

---

## Self-assessed confidence is noise (Phase I, 2026-02-16)

Phase I calibration study across the full 93-task set. Brier skill scores for self-assessed `p_success`:

- Most models near zero or negative Brier skill.
- Self-reported confidence is not a useful predictor of actual task success.
- This was known before Phase II started but the Gate B bidding data above shows exactly how it breaks the market in practice.

---

## Decomposition doesn't help for SWE-bench (2026-02-17)

Gate A debug run with `--dag-mode planner_market` on `astropy-14182`.

- Planner (gpt-5.2) decomposed the task into a 7-step chain (DAG_01 through DAG_06 + root).
- 6/7 intermediate tasks completed. Root task failed all 3 attempts.
- Root task was assigned to Sonnet/Opus (which can't solve it individually). gpt-5.2 spent its turn planning instead of patching.
- The diagnosis/localisation context posted to the discussion board didn't help models that can't solve the task monolithically.

Decomposition adds value when subtasks are genuinely independent and parallelisable. SWE-bench bug fixes aren't -- they're inherently one-person-one-session work. The market's value on SWE-bench is model selection, not task splitting.

---

## The market outperforms individual models (Phase II, 2026-02-17/18)

The earlier comparison against "75% individual" rates was wrong -- those numbers were from a different task selection. On these exact 10 tasks, Phase I external labels show much lower individual rates:

| Approach | Pass rate | Notes |
|----------|-----------|-------|
| Market baseline (Gate B, t2400, 3 att, no exclusion) | 40% (4/10) | |
| Market + exclusion (Gate B, t900, 2 att) | 30% (3/10) | |
| Oracle (always pick the right model) | 50% (5/10) | theoretical ceiling from external labels |
| Best individual model (external) | 20% (2/10) | 4-way tie: gpt-5.2, mini, 5.2-pro, gemini |
| Average individual model (external) | 13% (1.3/10) | sonnet and opus are 0/10 externally |

The market is doing 1.5--2x the best individual model, and reaches 60--80% of the oracle ceiling.

Three of the seven market passes across both runs came from models that fail the same task externally -- sonnet passed `matplotlib-23476` (no model passes it externally), mini and gemini each passed `pylint-7080` (only gpt-5.2 and 5.2-pro pass it externally). This is a scaffold/portfolio effect: different prompting context and multiple attempts can unlock passes that the standard SWE-bench scaffold misses.

The allocation mechanism still has real problems (overconfident bidders dominate, retries were non-diverse in the baseline). But the portfolio effect of routing different tasks to different models produces a clear advantage over any single model.

---

## Thin markets produce random assignment (2026-02-17)

From the early DAG-mode smoke runs (`smoke10_market_20260217T012419Z`):

- In 10 out of 24 root-task first rounds, only 1 model submitted a bid.
- The winner in those rounds was whoever happened to respond, regardless of capability.
- Cause: bid timeouts, models returning empty bid arrays, or models not generating valid JSON bids.

When 3+ bidders participated, winner selection was materially different (more spread across models).

---

## INFRA noise is real, but it's not Docker (2026-02-17/18)

Across both 10-task Gate B runs, roughly 25% of all attempts ended in INFRA status: 7/29 in the baseline (t2400), 5/18 in the exclusion run (t900). The rate is consistent regardless of timeout setting.

Root cause analysis of the 5 INFRA events in the exclusion run:

| Task | Model | Actual cause |
|------|-------|--------------|
| astropy-14182 | gemini | Patch apply failed (hunk mismatch against repo state in container) |
| psf-requests-2317 | gemini | Python compatibility crash (`collections.Mapping` removed in 3.10+) |
| scikit-learn-25747 | mini | Patch apply failed (hunk mismatch) |
| matplotlib-23476 | mini | Patch apply failed (2 of 3 hunks failed) |
| sympy-17630 | gpt-5.2 | Docker image build timeout (read timeout after 60s) |

Three of five are **patch quality failures** -- the model generated a diff with context lines that don't match the file state inside the Docker container. The SWE harness classifies a failed `git apply` as an evaluation `error`, which our system maps to INFRA. Docker was running fine throughout (PASS events are interspersed between INFRA events in the timeline).

`psf__requests-2317` remains particularly fragile: 6 INFRA events in the baseline alone, with a Python version incompatibility in the repo's dependencies that affects runs outside Docker too.

---

## Single-model controls on the same 10 tasks (2026-02-18)

We ran monolithic (`--dag-mode off`) single-worker controls on the exact same 10-task prepared manifest:

| Run | Pass rate | Final statuses |
|---|---|---|
| Market (6 models, exclusion-on retry) `gateB10_exclfix_t900_20260218T190909Z` | 30% (3/10) | 3 PASS, 6 FAIL, 1 INFRA |
| Solo Sonnet `solo_sonnet10_20260218T203131Z` | 0% (0/10) | 0 PASS, 0 FAIL, 10 INFRA (`harness_failed`) |
| Solo Gemini `solo_gemini10_20260218T204958Z` | 0% (0/10) | 0 PASS, 0 FAIL, 10 INFRA |
| Solo GPT-5.2 `solo_gpt52_10_20260218T210509Z` | 0% (0/10) | 0 PASS, 2 FAIL, 8 INFRA |

### Interpretation

- Within this harness and these exact settings, the multi-model market is clearly more robust than any single-model run on this slice.
- This is **not** evidence that decomposition or "collaborative reasoning" helped; these runs were monolithic (`--dag-mode off`) with one worker per attempt.
- The likely gain here is operational diversity (different workers across retries) plus lower exposure to per-model infra/harness failure modes.
- Because single-model runs were INFRA-dominated, this is stronger as a **system robustness** result than a pure **capability** result.

---

## Docker-clean rerun (GPT-5.2 solo, 2026-02-18)

After a full Docker reset (`docker system prune -af --volumes`), we reran the same GPT-5.2 solo 10-task setup:

- Previous GPT-5.2 solo: `solo_gpt52_10_20260218T210509Z` -> 0/10 (2 FAIL, 8 INFRA)
- Docker-clean GPT-5.2 solo: `solo_gpt52_10_cleanDocker_20260218T225012Z` -> 1/10 (5 FAIL, 4 INFRA, 1 PASS)

The one newly-resolved task was:

- `pylint-dev__pylint-7080` (0 -> 1)

### Interpretation

- Docker health/cache state does affect outcomes in this harness (INFRA dropped 8 -> 4 in this rerun).
- It does **not** fully explain the broader gap; even after cleaning, GPT-5.2 solo remained far below market-on-same-slice (1/10 vs 3/10).

---

## Phase IIa: Second-price auction comparison (2026-02-26)

Same 50 tasks and models, but the prompt now says "you are paid the second-lowest ask, not your own ask; bidding your true cost is optimal."

### The headline finding

Opus behaves as Vickrey theory predicts: its anchoring ratio collapsed from 1.61x to 1.10x. GPT-5.2 does the opposite: its ratio *increased* from 2.21x to 4.55x, suggesting it doesn't grasp the second-price mechanism.

All three models dropped asks dramatically (60--90% reduction). Mean asks went from $1--$4.50 to $0.14--$0.79. But most bids fell *below* breakeven (only 13/100 Opus bids and 59/100 GPT-5.2 bids above breakeven). Models interpreted "bid your true cost" as compute cost alone, ignoring expected penalty for failure.

Allocation accuracy was unchanged (68% under both regimes), despite radically different bid levels and winner distributions. GPT-5.2 went from winning 3/50 tasks (first-price) to 23/50 tasks (second-price) under min\_ask, because its bids are no longer inflated.

Run artifacts: `runs/research/phase2a_second_price_50task/` (226/300 clean; 74 Gemini quota-limited)

---

## Phase IIa: Informed competitive auctions (2026-02-25/26)

Three experiments on 50 SWE-bench tasks, 3 models (GPT-5.2, Opus 4.5, Gemini 3 Pro), 2 reserve levels ($5, $10).

### Exp 1: First-price informed bids (300 calls, all clean)

| Model | Mean ask @$5 | Mean ask @$10 | Ratio | Pattern |
|---|---|---|---|---|
| GPT-5.2 | $2.04 | $4.50 | 2.21x | Extracts surplus proportionally |
| Opus | $1.27 | $1.92 | 1.61x | Moderate upward adjustment |
| Gemini | $2.14 | $2.21 | 1.05x | Flat -- prices by task, ignores budget |

Allocation accuracy: 68--70% for both min\_ask and formula, vs 80% oracle.

### Exp 2: Second-price Vickrey bids (226/300 clean; 74 Gemini quota errors)

Asks dropped 60--90%. Opus anchoring collapsed (1.10x), GPT-5.2 anchoring *increased* (4.55x). 72% of bids fell below breakeven -- models ignored penalty risk when told to "bid true cost."

### Exp 3: Formula second-price (450/450 clean after Gemini retry)

Explicit breakeven formula in the prompt + reserve-hidden condition. Gemini initially quota-exhausted (147/150 errors); retried next day with full success.

| Model | Exp 1 ratio | Exp 2 ratio | Exp 3 ratio | Exp 3 hidden ask |
|---|---|---|---|---|
| GPT-5.2 | 2.21x | 4.55x | 0.97x | $0.40 |
| Opus | 1.61x | 1.10x | 0.99x | $0.37 |
| Gemini | 1.05x | 1.60x (n=12) | 1.08x | $0.21 |

The formula eliminated anchoring for all three models. Penalty inclusion jumped to 97%+. Gemini bids cheapest ($0.20 mean) and dominates allocation (85--92% of tasks), but wins tasks it can't solve.

Allocation accuracy (3-model): 62--63% across all conditions, slightly below 2-model results (66--68%) because Gemini wins tasks beyond its capability. Oracle ceiling: 77%.

### Takeaway

The allocation bottleneck is not the auction mechanism. Accuracy stays at 62--70% regardless of payment rules. The bottleneck is `p_success` calibration quality. Prompt design dramatically affects bidding behaviour: explicit formulas produce near-truthful bids, while vague instructions produce irrational ones. Cheap bidders can dominate allocation without delivering results -- quality signals beyond price are needed.

Published data: [`docs/research/data/phase2a/`](../data/phase2a/) (exp1/exp2/exp3 calibration + auction results)

---

## Phase II: 30-Task Market vs Solo Evaluation (2026-02-19)

We expanded the monolithic market evaluation to 30 contiguous tasks (the original 10 + the next 20).

**Key Results on 30 Tasks:**
- **Market (Our Scaffold):** 15/30 (50.0%)
- **Best External Solo (Strong Scaffold):** 17/30 (56.7%) - `gpt-5.2`
- **External Oracle Ceiling:** 22/30 (73.3%)

**The Retry Mechanism Works:**
Of the 15 market passes, exactly one third (5 passes) occurred on attempt 2 after a failure on attempt 1. The hard exclusion rule successfully routed the retry to a different model, rescuing 5 tasks that would have otherwise failed. Solo models do not get retries.

**The Scaffold Gap:**
The market's 50% pass rate is highly impressive when considering the scaffold handicap. The external solo models (achieving up to 56.7%) benefit from interactive shells, test feedback, and multi-turn iteration. The market achieved its 50% using single-shot attempts and raw diff/BEGIN_FILE output. 

**The Allocation Bottleneck:**
Allocation remains the primary factor keeping the market below the oracle ceiling (73.3%). 
- Gemini won 50% of all assignments (25 wins) and converted at 48%.
- GPT-5.2 (the strongest external model) won only 6 assignments, converting at 33.3%.
- GPT-5-mini won 13 assignments but converted at only 7.7%.
The scoring mechanism still over-weights self-assessed confidence, allowing overconfident but less capable models (like mini) to steal assignments from stronger models (like gpt-5.2).

**Conclusion on the 30-task slice:**
The market is performing exceptionally well given its scaffold. It beats 4 out of 6 individual models (on their better scaffolds) and trails the absolute best model by only 2 tasks. With improved allocation to filter out overconfident bids, the market would likely approach the 73.3% oracle ceiling.
