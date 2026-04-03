# Executive Summary: Agent Economy Research

**Last updated:** 2026-04-02
**Scope:** SWE-bench Lite evaluation (50+ tasks) across Phase I calibration, Phase II market execution, Phase IIa auction mechanism experiments (3 experiments, 3 models), and a later Codex follow-up diagnostic

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

## Later Follow-Up: Codex + GPT-5.2 With a Longer Budget (2026-04-02)

We later reran the same 50-task slice through the Codex worker path with the underlying model fixed to **GPT-5.2**, but doubled the per-task execution budget from **900 seconds** to **1800 seconds**.

That run finished at **35 / 50 passes (70%)**, ahead of both published same-task baselines:

| Execution Paradigm | Pass Rate | Absolute Passes |
| :--- | :--- | :--- |
| Codex + GPT-5.2 (relaxed 1800s budget) | 70.0% | 35 / 50 |
| Market (published 900s benchmark) | 58.0% | 29 / 50 |
| Solo GPT-5.2 (published 900s benchmark) | 48.0% | 24 / 50 |

This result should **not** replace the published 900-second benchmark. It answers a different question: what happens if the same model family gets more time and uses the Codex execution path? The answer is that the Codex setup looks much more speed-limited than capability-limited. The biggest gains came in `django` and `sympy`, while `matplotlib` remained a weak spot.

Of the **35** passing tasks in this relaxed-time follow-up, **20** finished after the original **900-second** budget and **15** finished within it. So most of the gain came from the extra runway, not from a hidden apples-to-apples improvement under the original limit.

### Efficiency Snapshot

| Execution Paradigm | Budget / task | Passes | Attempts | Total tokens | Tokens / pass | Penalties / task |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: |
| Market (published) | 900s | 29 / 50 | 82 | ~5.82M* | ~200.8k* | 47.6* |
| Solo GPT-5.2 (published) | 900s | 24 / 50 | 82 | 4.37M | 182.2k | 64.4 |
| Codex + GPT-5.2 (relaxed follow-up) | 1800s | 35 / 50 | 66 | 321.3M | 9.18M | 3920.5 |

The relaxed-time Codex run won on raw pass count, but it did so with a much larger time and token budget. It used about **74x** as many tokens as the published solo GPT-5.2 run and about **55x** as many as the published market run.

`Penalties / task` uses the repo's internal accounting units, not literal billing.

*Market token and penalty figures come from the published Phase II rollup and appendix. The middle 20-task raw market batch was lost, so the fine-grained run summaries for that batch are no longer recoverable.*

For details, see [Phase II Codex Relaxed-Time Follow-Up](09_PHASE_2_CODEX_RELAXED_TIME_GPT52_2026-04-02.md).

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
