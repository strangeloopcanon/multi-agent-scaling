# Phase IIa: Informed Competitive Auctions

Phase IIa is a series of three experiments investigating how LLMs bid when given economic context, and whether auction mechanism design can improve task allocation. All experiments use the same 50 SWE-bench Lite tasks with external ground-truth labels, 3 frontier models (GPT-5.2, Claude Opus 4.5, Gemini 3 Pro Preview), and 2 reserve levels ($5, $10).

The core question: when models are told their costs, the penalty for failure, and the payment rules, do they bid rationally? And can we exploit that to allocate tasks better than the confidence-weighted formula used in Phase II?

## Experiment overview

| | Exp 1: First-price | Exp 2: Second-price | Exp 3: Formula second-price |
|---|---|---|---|
| **Payment rule** | Winner pays own ask | Winner pays second-lowest ask | Winner pays second-lowest ask |
| **Cost info** | Pre-computed: "$0.000010/token" | Pre-computed: "$0.000010/token" | Raw parameter: "0.00001 $/token" |
| **Formula given** | No | No | Yes: `breakeven = tokens * ppt + penalty * (1-p)` |
| **Dominant strategy stated** | No | "Bid your true cost" (vague) | "Bidding above or below cannot improve your outcome" |
| **Reserve conditions** | $5, $10 (always shown) | $5, $10 (always shown) | $5, $10 shown + hidden |
| **Calls** | 300 | 300 (226 clean) | 450 (303 clean) |

Gemini hit daily API quota limits in Exp 2 (74/100 failures) and Exp 3 (147/150 failures). Gemini data is partial in those experiments; GPT-5.2 and Opus are complete throughout.

<details>
<summary>Data lineage</summary>

- Exp 1: `runs/research/phase2a_competitive_50task/` (300/300 clean)
- Exp 2: `runs/research/phase2a_second_price_50task/` (226/300 clean; 74 Gemini quota errors)
- Exp 3: `runs/research/phase2a_formula_sp/` (303/450 clean; 147 Gemini quota errors)
</details>

---

## Exp 1: First-price informed bids

Models see the task, penalty ($1.00), compute cost rate ($0.00001/token), and the client's budget cap. They submit a dollar ask and are paid that ask if they win and solve the task.

### Ask distributions

| Model | Ask range | Mean ask @$5 | Mean ask @$10 | Rational asks |
|---|---|---|---|---|
| Opus | $0.45--$3.85 | $1.27 | $1.92 | 100/100 |
| GPT-5.2 | $1.25--$6.50 | $2.04 | $4.50 | 99/100 |
| Gemini | $0.05--$4.50 | $2.14 | $2.21 | 99/100 |

Economic context solved the ask-scale problem: all asks fell in a rational range, versus $18--$1,800 without it.

### Reserve anchoring

| Model | Ratio ($10/$5) | Interpretation |
|---|---|---|
| GPT-5.2 | 2.21x | Doubles ask when budget doubles |
| Opus | 1.61x | Moderate adjustment |
| Gemini | 1.05x | Ignores budget signal |

### Allocation accuracy

| Mechanism | @$5 | @$10 | Oracle |
|---|---|---|---|
| min\_ask | 35/50 (70%) | 34/50 (68%) | 40/50 (80%) |
| formula | 34/50 (68%) | 35/50 (70%) | 40/50 (80%) |

Neither mechanism dominates. Both sit ~10pp below the oracle ceiling.

---

## Exp 2: Second-price (Vickrey) informed bids

Same economic information as Exp 1, but models are told they'll be paid the second-lowest ask, not their own. The prompt says "bidding your true cost is optimal."

### Asks collapsed toward breakeven

| Model | 1st-price @$5 | 2nd-price @$5 | 1st-price @$10 | 2nd-price @$10 |
|---|---|---|---|---|
| Opus | $1.27 | $0.50 | $1.92 | $0.54 |
| GPT-5.2 | $2.04 | $0.66 | $4.50 | $0.79 |
| Gemini | $2.14 | $0.14 (n=14) | $2.21 | $0.27 (n=12) |

All models dropped asks 60--90%. Under second-price, they bid near estimated cost rather than inflating toward the budget.

### Anchoring diverged

| Model | 1st-price ratio | 2nd-price ratio | Change |
|---|---|---|---|
| GPT-5.2 | 2.21x | 4.55x | Increased -- opposite of theory |
| Opus | 1.61x | 1.10x | Collapsed toward 1.0x as predicted |
| Gemini | 1.05x | 1.60x (n=12) | Increased, small sample |

Opus understood the Vickrey mechanism: anchoring collapsed. GPT-5.2 did the opposite, inflating its ratio to 4.55x.

### Rationality dropped sharply

| Model | 1st-price rational | 2nd-price rational |
|---|---|---|
| Opus | 100/100 | 13/100 |
| GPT-5.2 | 99/100 | 59/100 |
| Gemini | 99/100 | 22/26 |

Models interpreted "true cost" as compute cost only, ignoring expected penalty for failure. Most bids fell below breakeven.

### Allocation accuracy held

| Mechanism | 1st @$5 | 2nd @$5 | 1st @$10 | 2nd @$10 |
|---|---|---|---|---|
| min\_ask | 70% | 68% | 68% | 68% |
| formula | 68% | 64% | 70% | 68% |

Despite radically different ask levels, accuracy barely changed.

---

## Exp 3: Formula second-price (reserve shown vs hidden)

This experiment addressed both problems from Exp 2: (1) models ignored penalty in their cost estimate, and (2) we couldn't tell if remaining anchoring was caused by seeing the reserve or by intrinsic model behaviour.

Changes from Exp 2:
- The breakeven formula is stated explicitly: `breakeven = estimated_tokens x price_per_token + penalty x (1 - p_success)`
- The dominant strategy is stated unambiguously: "Bidding above or below cannot improve your outcome"
- Price per token is given as a raw number (0.00001), not a pre-computed dollar amount
- A third condition is added: reserve hidden (no budget information in the prompt)

GPT-5.2 and Opus: 300/300 clean. Gemini: 3/150 clean (daily quota exhausted).

### The formula eliminated anchoring

| Model | Exp 1 ratio | Exp 2 ratio | Exp 3 ratio (shown) |
|---|---|---|---|
| GPT-5.2 | 2.21x | 4.55x | **0.97x** |
| Opus | 1.61x | 1.10x | **0.99x** |

Both models now bid virtually the same regardless of the budget level. The explicit formula gave GPT-5.2 the structure it needed to stop anchoring -- its ratio dropped from 4.55x to 0.97x.

### Ask levels dropped to true cost

| Model | Exp 1 @$5 | Exp 2 @$5 | Exp 3 @$5 | Exp 3 hidden |
|---|---|---|---|---|
| GPT-5.2 | $2.04 | $0.66 | $0.48 | $0.40 |
| Opus | $1.27 | $0.50 | $0.37 | $0.37 |

With the formula, models bid at their computed breakeven rather than padding margins or anchoring on the budget.

### Reserve visibility barely matters

| Model | Hidden ask | Shown @$5 | Shown @$10 | Hidden/Shown@$5 |
|---|---|---|---|---|
| GPT-5.2 | $0.40 | $0.48 | $0.46 | 0.83x |
| Opus | $0.37 | $0.37 | $0.37 | 1.00x |

Opus is perfectly indifferent to whether the reserve is shown -- pure cost-based bidding. GPT-5.2 bids ~17% lower when the reserve is hidden, suggesting a small residual anchoring effect, but compared to its 2.21x ratio in Exp 1, this is negligible.

### Penalty inclusion fixed

| Model | Exp 2: asks above compute-only | Exp 3: asks above compute-only |
|---|---|---|
| GPT-5.2 | low (most bids below breakeven) | 94.7% |
| Opus | low (13/100 above breakeven) | 100% |

The explicit formula made penalty inclusion nearly universal.

### Rationality improved for Opus, still mixed for GPT-5.2

| Model | Exp 1 rational | Exp 2 rational | Exp 3 rational |
|---|---|---|---|
| Opus | 100/100 | 13/100 | **150/150** |
| GPT-5.2 | 99/100 | 59/100 | **85/150** (56.7%) |

Opus returned to 100% rationality with the formula. GPT-5.2 improved from 59% to 57% (similar), but when irrational its bids average 71% of breakeven -- it systematically underestimates its costs on some tasks.

### Allocation accuracy stable

| Condition | min\_ask | formula | Oracle |
|---|---|---|---|
| Shown @$5 | 34/50 (68%) | 33/50 (66%) | 39/50 (78%) |
| Shown @$10 | 33/50 (66%) | 32/50 (64%) | 38/50 (76%) |
| Hidden @$5 | 33/50 (66%) | 33/50 (66%) | 38/50 (76%) |
| Hidden @$10 | 33/50 (66%) | 33/50 (66%) | 38/50 (76%) |

Accuracy sits at 64--68% across all conditions, consistent with Exp 1 and 2.

---

## Cross-experiment comparison

### Anchoring trajectory

| Model | Exp 1 (1st-price) | Exp 2 (2nd-price, vague) | Exp 3 (2nd-price, formula) |
|---|---|---|---|
| GPT-5.2 | 2.21x | 4.55x | **0.97x** |
| Opus | 1.61x | 1.10x | **0.99x** |
| Gemini | 1.05x | 1.60x (n=12) | insufficient data |

The explicit formula collapsed anchoring for both models. Opus was already heading there (1.10x in Exp 2); GPT-5.2 needed the formula to stop extracting surplus.

### What each experiment told us

| Finding | Exp 1 | Exp 2 | Exp 3 |
|---|---|---|---|
| Economic context solves ask-scale | Yes | Yes | Yes |
| Models anchor on reserve | GPT-5.2 strongly, Opus moderately | Opus collapses, GPT-5.2 inflates | Both collapse |
| Models include penalty in cost | Yes (implicit) | No -- ignores penalty | Yes (explicit formula) |
| Bids are rational (above breakeven) | 99%+ | 28% (Opus+GPT combined) | 78% overall, 100% Opus |
| Allocation accuracy | 68--70% | 64--68% | 64--68% |
| Oracle ceiling | 80% | 76--78% | 76--78% |

### What drives model bidding

1. **Opus** responds correctly to mechanism design. It adjusted its anchoring from 1.61x (first-price, rational surplus extraction) to 1.10x (second-price, near-truthful) to 0.99x (formula, perfectly truthful). It is the most economically sophisticated bidder.

2. **GPT-5.2** needed explicit structural guidance. Telling it to "bid true cost" made anchoring *worse* (4.55x). Giving it the formula made it *perfect* (0.97x). It's good at following formulas, bad at inferring economic strategy.

3. **Gemini** is price-rigid in Exp 1 (1.05x), suggesting cost-based rather than strategic bidding. Insufficient data in Exp 2/3 due to quota limits.

---

## Conclusions

**The allocation bottleneck is not the auction mechanism.** Across three experiments, two payment rules, and multiple reserve conditions, allocation accuracy stays at 64--70%, always ~10pp below the oracle ceiling. The bottleneck is the quality of `p_success` self-assessment, not how bids are scored or priced.

**Prompt design matters more than mechanism design.** The difference between Exp 2 (vague "bid true cost") and Exp 3 (explicit formula) is dramatic: anchoring collapses, penalty inclusion jumps from ~28% to ~95%, and bid rationality recovers. For LLM-based markets, giving models clear decision procedures outperforms giving them strategic advice.

**Reserve visibility is a non-issue when the formula is explicit.** With the formula in the prompt, models bid at breakeven regardless of whether they see the budget. The reserve anchor that dominated Exp 1 (2.21x for GPT-5.2) is entirely a product of ambiguous pricing instructions, not a fundamental model limitation.

**Second-price auctions work as intended -- when paired with explicit formulas.** Exp 3 achieves the Vickrey ideal: near-truthful bidding, no anchoring, rational cost inclusion. Exp 2 showed that second-price alone is insufficient; models need the computational structure to produce truthful bids.
