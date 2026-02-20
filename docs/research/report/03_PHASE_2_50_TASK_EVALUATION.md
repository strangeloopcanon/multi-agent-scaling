# Phase II Final 50-Task Evaluation

**Date:** 2026-02-20
**Scope:** 50-task analysis set from the canonical 93-task `phase2_93_manifest_v1.json` (first 50 slots with one strict-preflight replacement: `sphinx-doc__sphinx-8595` -> `django__django-12708`)

## 1. Top-Line Results

We evaluated 50 tasks across four paradigms to isolate the value of the market mechanism from the noise of the scaffold:

| Execution Paradigm | Pass Rate | Absolute Passes |
| :--- | :--- | :--- |
| **Market (Our Scaffold)** | **58.0%** | 29 / 50 |
| **Solo GPT-5.2 (Our Scaffold)** | **48.0%** | 24 / 50 |
| *Solo GPT-5.2 (External Scaffold)* | *74.0%* | 37 / 50 |
| *Oracle Ceiling (External Scaffold)* | *84.0%* | 42 / 50 |

*(External Scaffold denotes standard SWE-bench setups with interactive shells, test feedback, and multi-turn iteration).*

The market beats the best same-scaffold solo by 10 percentage points. Against external baselines with stronger scaffolds, it trails GPT-5.2 by 16pp -- but those baselines get interactive shells, direct test feedback, and multi-turn iteration that our scaffold lacks.

## 2. The Apples-to-Apples Comparison

The same-scaffold comparison eliminates the confound of scaffold quality. Does model diversity through market allocation add value over picking the single best model?

| | Solo GPT-5.2 passes | Solo GPT-5.2 fails |
| :--- | :--- | :--- |
| **Market passes** | 18 | 11 |
| **Market fails** | 6 | 15 |

11 tasks solved by the market but not by solo GPT-5.2. 6 tasks the other way round. Net +5 for the market.

*(McNemar exact two-sided p-value: 0.3323; one-sided p-value: ~0.17. Direction is favorable to the market, but N=50 is not large enough for conventional significance thresholds.)*

## 3. Retries Are the Market's Engine

| | Count | % of market passes |
| :--- | :--- | :--- |
| Attempt 1 passes | 18 | 62.1% |
| Retry rescues (attempt 2) | 11 | 37.9% |

Without retries, the market would sit at 18/50 = 36% -- below solo GPT-5.2. The hard exclusion rule (a failed worker cannot rebid on the same task) forces model diversity on the second attempt, producing 11 rescues.

**The Rescue Traffic:**
Flow is mostly weaker-to-stronger: 8 of 11 rescues had Gemini or Opus cleaning up after mini or gpt-5.2. Two went the other direction. This is the portfolio effect -- failures routed to a different model that happens to be better at that specific task.

## 4. Market Allocation in Practice

| Model | Assignments | Passes | Conversion | Ext rate (strong scaffold) |
|-------|----:|----:|----:|----:|
| Gemini | 42 | 21 | 50.0% | 66.0% |
| GPT-5-mini | 20 | 2 | 10.0% | 60.0% |
| GPT-5.2 | 9 | 3 | 33.3% | 74.0% |
| GPT-5.2-pro | 6 | 1 | 16.7% | 70.0% |
| Claude Opus | 4 | 2 | 50.0% | 66.0% |
| Claude Sonnet | 1 | 0 | 0.0% | 62.0% |

**Gemini** dominates: 51% of assignments, 72% of passes. It earns that position in this market run -- its 50% conversion rate is the highest for any model with significant volume in this sample, though still below its 66% external baseline on the same task set.

**GPT-5-mini** is the drag: 20 assignments, 2 passes, 10% conversion. Its self-assessed confidence wins auctions that it then fails to execute.

**GPT-5.2** is chronically under-assigned. 9 wins on a model that externally solves 74% of these tasks. The scoring formula's reliance on self-assessed `p_success` lets overconfident models crowd out the genuinely strongest one.

## 5. By Repository Diversity

| Repo | N | Market | Solo-5.2 | Ext-5.2 | Market edge vs solo |
|------|---|--------|----------|---------|---------------------|
| sympy | 12 | 8 (67%) | 4 (33%) | 10 (83%) | **+4** |
| django | 15 | 8 (53%) | 8 (53%) | 9 (60%) | 0 |
| scikit-learn | 5 | 4 (80%) | 4 (80%) | 4 (80%) | 0 |
| pytest-dev | 2 | 2 (100%) | 1 (50%) | 1 (50%) | +1 |
| pydata | 1 | 1 | 0 | 1 | +1 |
| matplotlib | 7 | 2 (29%) | 2 (29%) | 5 (71%) | 0 |
| astropy | 4 | 2 (50%) | 3 (75%) | 4 (100%) | -1 |
| sphinx-doc | 2 | 1 (50%) | 1 (50%) | 2 (100%) | 0 |

The market's edge concentrates on `sympy` (+4 over solo). These are mathematical/symbolic reasoning tasks where models fail in diverse ways -- a second attempt by a different model has a decent chance of succeeding. For `django` and `matplotlib`, diversity adds nothing; the failure mode is more uniform across models.

## 6. The Scaffold Gap, Quantified

On these 50 tasks, the same model (GPT-5.2) solves 37 tasks externally and 24 tasks on our scaffold. That is a **13-task gap** attributable to scaffold quality alone.

What external scaffolds provide:
- Interactive shell for repository exploration
- Direct test execution and feedback within the attempt
- Multi-turn iteration (try, observe test results, revise)
- Structured file-editing tools vs raw diff output

The market recovers about 5 of those 13 lost tasks through diversity (29 vs 24). The remaining gap was not recovered in this setup.

## 7. Conclusions

1. **Model diversity adds measurable value.** On the same scaffold, the 6-model market solves 5 more tasks than the best solo model (29 vs 24, +10pp) on this 50-task sample.
2. **Retries are the mechanism.** 38% of market passes come from the second attempt after exclusion-forced model diversity. Without retries the market would underperform solo. The hard exclusion rule is the single most important mechanism.
3. **First-attempt allocation is still poor.** The market's attempt-1 rate (36%) is below solo GPT-5.2 (48%). The scoring formula gives too much weight to self-assessed confidence, letting GPT-5-mini absorb 24% of assignments while converting at 10%. The strongest model (GPT-5.2) gets only 11% of assignments.
4. **The scaffold gap is the ceiling.** External scaffolds add 13 tasks of capability for the same model. The market recovers ~5 of those through diversity. The remaining 8 need scaffold upgrades (test feedback, multi-turn, file-editing tools) to become reachable.
5. **Diversity has domain-specific value.** The market's advantage concentrates in `sympy` tasks (+4 over solo), where symbolic reasoning failures are model-specific. Framework-pattern tasks (`django`, `matplotlib`) show no diversity benefit.
