# Executive Summary: Agent Economy Research

**Last updated:** 2026-02-26
**Scope:** SWE-bench Lite evaluation (50+ tasks) across Phase I calibration, Phase II market execution, and Phase IIa auction mechanism experiments

---

## Phase II: Market vs Solo (2026-02-20)

The primary goal of Phase II was to test whether a multi-agent market with economic allocation and retry-exclusion could outperform the strongest individual frontier model on a level playing field.

The results provide directional evidence for this hypothesis: **the market beats the best solo model by 10 absolute percentage points (58% vs 48%) on identical scaffolds.** On this 50-task sample, the paired McNemar exact test is not yet conventionally significant (two-sided `p=0.3323`).

## Headline Results

We evaluated 50 tasks across four paradigms to isolate the value of the market mechanism from the noise of the scaffold:

| Execution Paradigm | Pass Rate | Absolute Passes |
| :--- | :--- | :--- |
| **Market (Our Scaffold)** | **58.0%** | 29 / 50 |
| **Solo GPT-5.2 (Our Scaffold)** | **48.0%** | 24 / 50 |
| *Solo GPT-5.2 (External Scaffold)* | *74.0%* | 37 / 50 |
| *Oracle Ceiling (External Scaffold)* | *84.0%* | 42 / 50 |

*(External Scaffold denotes standard SWE-bench setups with interactive shells, test feedback, and multi-turn iteration).*

## Key Drivers of Success

1. **Diverse-Model Retry Converts at Double the Rate**
   Both configurations use `max_attempts=2` and share the same first-attempt pass rate (18/50, 36%). The difference emerges on retry: the market's hard exclusion rule forces a *different* model on the second attempt, rescuing 11 of 32 first-attempt failures (34% rescue rate). Solo GPT-5.2's same-model retry rescues only 6 of 32 (19%). This 2x retry-conversion advantage is the primary mechanism behind the market's +10pp edge.

2. **Model Diversity Adds Measurable Value**
   In a direct head-to-head comparison on these 50 tasks, the market solved 11 tasks that Solo GPT-5.2 failed, while Solo GPT-5.2 solved only 6 tasks that the market missed. This diversity effect concentrates in symbolic reasoning domains (e.g., `sympy`), where failures are model-specific rather than universal.

## The Remaining Bottlenecks

While the market outperforms single models, it currently sits below the theoretical Oracle Ceiling (84%) due to two primary constraints:

1. **The Scaffold Gap (The Ceiling)**
   Our single-shot, raw-diff execution environment imposes a ~26 percentage point penalty compared to interactive external scaffolds (48% vs 74% for GPT-5.2). The market recovers about 5 tasks (~10pp) of this lost capability through diversity, but the rest remains blocked without scaffold upgrades (test feedback, file-editing tools).

2. **Allocation Inefficiency (The Missed Upside)**
   Both configurations share a 36% first-attempt rate, but the market has room to do much better — the oracle ceiling is 74% on these tasks with this scaffold's models. The scoring formula (`p_success * bounty - ask`) over-weights self-assessed confidence, allowing weaker but overconfident models (like GPT-5-mini, converting at 10%) to steal assignments from stronger models (like GPT-5.2, converting at 33%). Better bid calibration would push the market's first-attempt rate toward the oracle ceiling and reduce dependence on retries.

## Conclusion

On this 50-task sample, the market shows a practical gain over the strongest same-scaffold solo baseline. The remaining bottlenecks are allocation quality and scaffold limitations.

---

## Phase IIa: Informed Competitive Auctions (2026-02-26)

Phase IIa investigated the allocation bottleneck identified in Phase II: can we improve task routing by changing how models bid and how winners are selected?

We gave models full economic context (penalty, compute cost, client budget) and had them submit dollar asks. We then compared two allocation mechanisms -- lowest ask wins (min\_ask) vs the Phase II confidence-weighted formula -- on 50 SWE-bench tasks across 3 models (GPT-5.2, Opus 4.5, Gemini 3 Pro) at 2 reserve levels ($5, $10). 300 total LLM calls, zero errors.

### Key findings

**Models price rationally with economic context.** All 300 asks fell in the $0.05--$6.50 range, with 298/300 above the theoretical breakeven. This solves the wild ask-scale problem seen without economic framing ($18--$1,800 from GPT-5.2 alone).

**Reserve anchoring is a stable, model-specific trait.** When the budget doubles from $5 to $10:

| Model | Ask ratio ($10/$5) | Behaviour |
|---|---|---|
| GPT-5.2 | 2.21x | Strategically extracts surplus |
| Opus | 1.61x | Moderate adjustment |
| Gemini | 1.05x | Price-rigid, ignores budget |

These ratios held almost exactly from the 20-task pilot to the full 50-task run.

**Neither allocation mechanism dominates.** Both min\_ask and the formula reached 68--70% accuracy against an 80% oracle ceiling. They route tasks to entirely different models (Opus wins 84% under min\_ask; Gemini wins 58% under formula at $10) but produce similar outcomes.

**The bottleneck is calibration, not the scoring rule.** Both mechanisms produce similar accuracy because the underlying `p_success` estimates are similarly noisy. Improving model self-assessment would help more than changing the allocation rule.

For full details, see the [Phase IIa report](08_PHASE_2A_COMPETITIVE_AUCTION_2026-02-26.md).
