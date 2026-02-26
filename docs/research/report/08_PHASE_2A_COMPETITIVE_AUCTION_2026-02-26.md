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

---

## Second-price (Vickrey) auction comparison

To test whether the anchoring behaviour observed under first-price rules is strategic or mere pattern-matching, we re-ran the same 50-task experiment with a second-price prompt: models were told they'd be paid the second-lowest ask (not their own), and that bidding their true cost was optimal.

226/300 calls succeeded (Gemini hit a daily API quota limit; 100/100 GPT-5.2 and 100/100 Opus completed cleanly, plus 26/100 Gemini).

### Asks collapsed toward breakeven

| Model | 1st-price mean @$5 | 2nd-price mean @$5 | 1st-price mean @$10 | 2nd-price mean @$10 |
|---|---|---|---|---|
| Opus | $1.27 | **$0.50** | $1.92 | **$0.54** |
| GPT-5.2 | $2.04 | **$0.66** | $4.50 | **$0.79** |
| Gemini | $2.14 | $0.14 (n=14) | $2.21 | $0.27 (n=12) |

All three models dropped their asks dramatically -- Opus by 60%, GPT-5.2 by 68--82%, Gemini by 87--93%. Under second-price rules, models bid near their estimated cost rather than inflating toward the budget.

### Opus's anchoring collapsed; GPT-5.2's inverted

| Model | 1st-price ratio ($10/$5) | 2nd-price ratio ($10/$5) | Change |
|---|---|---|---|
| GPT-5.2 | 2.21x | **4.55x** | Increased -- opposite of expected |
| Opus | 1.61x | **1.10x** | Collapsed toward 1.0x as predicted |
| Gemini | 1.05x | 1.60x (n=12) | Increased, but small sample |

Opus behaves as theory predicts: under second-price, it bids near true cost regardless of the budget, and the anchoring ratio drops to near 1.0x. GPT-5.2 does the opposite -- its ratio actually increases to 4.55x, suggesting it doesn't understand the Vickrey mechanism and still tries to extract surplus (or it interprets the higher budget as a signal about task difficulty).

### Rationality dropped sharply

| Model | 1st-price rational | 2nd-price rational |
|---|---|---|
| Opus | 100/100 | **13/100** |
| GPT-5.2 | 99/100 | **59/100** |
| Gemini | 99/100 | 22/26 |

Under second-price, most models bid *below* breakeven. This is economically irrational in any auction format -- you'd lose money if you won. The prompt told them to bid their "true cost", and they interpreted this as expected compute cost alone, ignoring the expected penalty for failure. They're being too literal about "cost" and not accounting for risk.

### Allocation accuracy held steady

| Mechanism | 1st-price @$5 | 2nd-price @$5 | 1st-price @$10 | 2nd-price @$10 |
|---|---|---|---|---|
| min\_ask | 70% | 68% | 68% | 68% |
| formula | 68% | 64% | 70% | 68% |

Accuracy barely changed despite radically different bid levels. The winner distribution shifted substantially (GPT-5.2 wins 23/50 under second-price min\_ask vs 3/50 under first-price), but outcomes are similar because the task-model match quality hasn't changed.

### What this means

1. **Models respond to mechanism design, but inconsistently.** Opus understands second-price incentives (anchoring collapses, bids near cost). GPT-5.2 doesn't (anchoring increases, suggesting it's still trying to extract surplus).

2. **"Bid your true cost" is taken too literally.** Most models bid compute cost and ignore expected penalty risk, producing below-breakeven bids. A more explicit prompt that defines "true cost = compute + expected penalty" might fix this.

3. **Allocation accuracy is mechanism-invariant.** Whether bids are inflated (first-price) or compressed (second-price), the auction picks winners with similar accuracy. The information content of the bids hasn't changed, just the scale.

4. **Second-price produces much cheaper payments.** Mean asks dropped 60--90%. In a real market, this would slash procurement costs without reducing allocation quality -- but only if the below-breakeven bidding problem is addressed (otherwise the winning agents lose money).

<details>
<summary>Data lineage</summary>

- First-price data: `runs/research/phase2a_competitive_50task/` (300/300 clean)
- Second-price data: `runs/research/phase2a_second_price_50task/` (226/300 clean; 74 Gemini quota errors)
- Gemini data is partial (26/100 records); GPT-5.2 and Opus are complete (100/100 each)
</details>
