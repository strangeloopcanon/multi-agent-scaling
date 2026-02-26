# Phase IIa: Informed Competitive Auction (2026-02-26)

## What changed

We redesigned the Phase IIa reserve auction experiment to test a real behavioural question: can price-based allocation outperform the confidence-weighted formula used in Phase II?

Instead of mechanically simulating reserves against fixed calibration data (the earlier design), we now elicit *informed bids* from models: each model sees the task, the penalty ($1.00), its compute cost rate ($0.00001/token), and the client's budget cap ($5 or $10), then submits a dollar ask. The competitive auction allocates each task by either lowest ask (min\_ask) or highest `p_success * reserve - ask` (the Phase II formula), and we compare allocation accuracy against ground-truth external labels.

## Parameters

- **50 tasks** from `external_covered_lite` (SWE-bench Lite, external labels)
- 3 models: GPT-5.2, Claude Opus 4.5, Gemini 3 Pro Preview
- 2 reserve levels: $5, $10
- 300 total LLM calls (50 tasks x 3 models x 2 reserves)
- Run incrementally: 20-task pilot first, then 30 additional tasks merged via `--resume-from`

<details>
<summary>Data lineage</summary>

- Pilot (120 records): `runs/research/phase2a_competitive_20260226T090231Z/`
- Full run (300 records): `runs/research/phase2a_competitive_50task/`
- 0 errors, 0 missing asks across all 300 records
</details>

## Key findings

### 1. Economic context fixes the ask-scale problem

With informed bids, all models produce asks in the $0.05–$6.50 range, all below the reserve, and 298/300 above breakeven. The economic framing gives models enough context to price rationally. (Compare: without economic context, GPT-5.2 asked $18–$1,800 and Gemini asked $2–$250.)

| Model | Ask range | Mean ask @$5 | Mean ask @$10 | Rational asks |
|---|---|---|---|---|
| Opus | $0.45–$3.85 | $1.27 | $1.92 | 100/100 |
| GPT-5.2 | $1.25–$6.50 | $2.04 | $4.50 | 99/100 |
| Gemini | $0.05–$4.50 | $2.14 | $2.21 | 99/100 |

### 2. Reserve anchoring is model-specific and stable at scale

How much does each model adjust its ask when the budget doubles from $5 to $10?

| Model | Mean ratio ($10/$5) | 20-task pilot ratio | Interpretation |
|---|---|---|---|
| GPT-5.2 | 2.21x | 2.18x | Highly strategic: doubles ask with budget |
| Opus | 1.61x | 1.51x | Moderate adjustment |
| Gemini | 1.05x | 1.12x | Price-rigid: ignores budget signal |

The pattern from the 20-task pilot holds almost exactly at 50 tasks, increasing confidence that these are genuine model-level behaviours rather than sampling noise.

### 3. Allocation accuracy: both mechanisms reach 68–70%

| Mechanism | Reserve=$5 | Reserve=$10 |
|---|---|---|
| min\_ask | 35/50 (70%) | 34/50 (68%) |
| formula | 34/50 (68%) | 35/50 (70%) |
| oracle | 40/50 (80%) | 40/50 (80%) |

Neither mechanism consistently outperforms. Both sit 10–12pp below the oracle ceiling, up from 15–20pp gap at 20 tasks. The scaling improved absolute accuracy from ~42% to ~69% (the task pool at 50 is more solvable: 80% oracle vs 60% at 20), but the relative gap between mechanisms remains negligible.

### 4. Opus dominates min\_ask allocation by underbidding

| Reserve | min\_ask winner distribution | formula winner distribution |
|---|---|---|
| $5 | Opus 42, Gemini 5, GPT-5.2 3 | Opus 33, Gemini 17 |
| $10 | Opus 34, Gemini 14, GPT-5.2 2 | Gemini 29, Opus 21 |

Under min\_ask, Opus wins 68–84% of tasks because it consistently bids lowest. Under the formula (which rewards high confidence), Gemini's high `p_success` claims shift allocation toward it, especially at the $10 reserve. GPT-5.2 rarely wins under either mechanism because it bids highest.

### 5. Cost efficiency

| Metric | min\_ask @$5 | min\_ask @$10 | formula @$5 | formula @$10 |
|---|---|---|---|---|
| Cost per solve (1st-price) | $1.72 | $2.31 | $1.99 | $2.41 |
| Cost per solve (2nd-price) | $2.67 | $3.61 | $2.99 | $3.48 |

min\_ask is consistently cheaper per solve than the formula because winners bid low. The formula pays more but doesn't improve accuracy enough to justify the premium.

## What this means

1. **Economic context works and scales.** The ask-scale fix holds at 50 tasks. Models price rationally when given penalty, compute cost, and budget information.

2. **Reserve anchoring is a stable model-level trait.** GPT-5.2's 2.2x ratio and Gemini's 1.05x ratio are essentially unchanged from the 20-task pilot. This is a reliable finding for mechanism design: budget-responsive models can be influenced by reserve levels, price-rigid ones cannot.

3. **Price alone doesn't fix allocation.** Neither min\_ask nor the formula closes the gap to the oracle ceiling. The 10pp gap at 50 tasks is smaller than the 20pp gap at 20 tasks, but this is driven by a more solvable task pool rather than better allocation.

4. **The bottleneck is calibration, not the scoring rule.** Both mechanisms produce similar accuracy because the underlying `p_success` estimates are similarly noisy. Improving self-assessment would help more than changing the allocation rule.

5. **Opus's underbidding is economically rational but allocation-suboptimal.** It wins most tasks under min\_ask by bidding low, but this doesn't match capability. A mechanism that verifies post-hoc solve rates and penalises over-claiming could rebalance allocation without changing the bidding protocol.
