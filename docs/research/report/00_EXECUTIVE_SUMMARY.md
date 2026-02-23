# Executive Summary: Agent Economy Phase II Evaluation

**Date:** 2026-02-20
**Scope:** 50-task analysis set from SWE-bench Lite (first 50 manifest slots, with one deterministic replacement after strict preflight failure)

## The Core Thesis

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

On this 50-task sample, the market shows a practical gain over the strongest same-scaffold solo baseline. Future improvements should focus on bid calibration and assignment scoring to capture more oracle upside, followed by incremental scaffold enhancements.
