# Executive Summary: Agent Economy Research

**Last updated:** 2026-02-26
**Scope:** SWE-bench Lite evaluation (50+ tasks) across Phase I calibration, Phase II market execution, and Phase IIa auction mechanism experiments (3 experiments, 3 models)

Can a competitive market of LLMs outperform any single model? We tested this on SWE-bench Lite -- real software engineering tasks with verifiable patches. The short answer: yes, but the bottleneck isn't the auction mechanism, it's how well models know their own capabilities.

---

## Phase I: Calibration (2026-02-15)

Phase I evaluated 6 frontier models on 93 SWE-bench Lite tasks to establish the oracle ceiling and test whether self-assessed confidence could drive allocation.

The oracle ceiling (best possible model for each task) is **84%** on the 50-task subset. But self-assessed confidence turned out to be noise: most models had near-zero or negative Brier skill scores, with only Claude Sonnet 4.5 (+0.07) beating a naive base-rate predictor. Overconfident models consistently stole assignments from more capable ones.

This established the core challenge for Phase II: the market mechanism works, but allocation quality is bottlenecked by calibration, not by the auction design.

For details, see [Phase I Calibration](01_PHASE_1_CALIBRATION.md).

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

## Phase IIa: Informed Competitive Auctions (2026-02-25/26)

Phase IIa investigated the allocation bottleneck identified in Phase II through three experiments testing how LLMs bid under different economic framings: first-price, second-price, and second-price with an explicit breakeven formula. All used 50 SWE-bench Lite tasks, 3 models (GPT-5.2, Opus 4.5, Gemini 3 Pro), and reserve levels of $5 and $10.

### Key findings

**Prompt design matters more than mechanism design.** Across three experiments, allocation accuracy stayed at 62--70% regardless of payment rule or reserve conditions. The bottleneck is `p_success` self-assessment quality, not the auction mechanism. But *how* models are told to bid changes their behaviour dramatically.

**Reserve anchoring is curable with explicit formulas.** Ask ratio ($10/$5) trajectory across experiments:

| Experiment | GPT-5.2 ratio | Opus ratio | Gemini ratio |
|---|---|---|---|
| Exp 1: First-price | 2.21x | 1.61x | 1.05x |
| Exp 2: Second-price ("bid true cost") | 4.55x | 1.10x | 1.60x (n=12) |
| Exp 3: Second-price (explicit formula) | **0.97x** | **0.99x** | **1.08x** |

Telling GPT-5.2 to "bid its true cost" made anchoring *worse*. Giving it the breakeven formula made it *perfect*. Opus responded correctly to Vickrey incentives even without the formula. Gemini was price-rigid throughout.

**Cheap bidders dominate but don't always deliver.** With all three models competing in Exp 3, Gemini wins 85--92% of tasks by bidding $0.20 vs Opus's $0.37 and GPT-5.2's $0.48. But it wins tasks beyond its capability, pulling 3-model allocation accuracy (62--63%) below the 2-model results (66--68%). A well-designed market needs quality signals beyond price.

**Reserve visibility is a non-issue with the right prompt.** In Exp 3, all three models bid at breakeven regardless of whether they see the budget. The reserve anchor that dominated Exp 1 is entirely a product of ambiguous pricing instructions.

**Models need computational structure, not strategic advice.** The explicit formula fixed two problems at once: anchoring collapsed (all models near 1.0x) and penalty inclusion jumped from ~28% to ~97% of bids above breakeven. LLMs follow formulas well but infer economic strategy poorly.

For full details, see the [Phase IIa report](08_PHASE_2A_COMPETITIVE_AUCTION_2026-02-26.md).
