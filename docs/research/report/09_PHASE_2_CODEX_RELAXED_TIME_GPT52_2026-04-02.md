# Phase II Follow-Up: Codex + GPT-5.2 at 30 Minutes

**Date:** 2026-04-02
**Scope:** Same 50-task Phase II slice as the published market-vs-solo comparison, but run through the Codex worker path with the underlying model fixed to `openai:gpt-5.2-2025-12-11` and the per-task execution limit raised from 900 seconds to 1800 seconds.

## 1. Top-Line Result

The relaxed-time Codex run finished at **35 / 50 passes (70%)**.

| Execution Paradigm | Pass Rate | Absolute Passes |
| :--- | :--- | :--- |
| Codex + GPT-5.2 (relaxed 1800s budget) | 70.0% | 35 / 50 |
| Market (published 900s benchmark) | 58.0% | 29 / 50 |
| Solo GPT-5.2 (published 900s benchmark) | 48.0% | 24 / 50 |

This run beat the published market baseline by **6 tasks** and the published solo GPT-5.2 baseline by **11 tasks**.

## 2. What Changed

This was not a rerun of the published benchmark under identical conditions.

What stayed the same:
- same 50-task slice
- same underlying model family as the published solo GPT-5.2 baseline
- same broad Phase II scaffold, retry policy, and direct-penalty settlement setup

What changed:
- the worker path was `codex-direct`
- the per-task execution limit was doubled from **900s** to **1800s**

So this result should be read as a **follow-up diagnostic**, not as a replacement for the published 900-second market-vs-solo comparison.

## 3. Where the Gains Came From

The biggest lifts came from `django` and `sympy`.

| Repo | N | Codex relaxed | Market | Solo GPT-5.2 |
| :--- | ---: | ---: | ---: | ---: |
| `astropy` | 4 | 3 | 2 | 3 |
| `django` | 15 | 11 | 8 | 8 |
| `matplotlib` | 7 | 1 | 2 | 2 |
| `psf` | 1 | 1 | 0 | 0 |
| `pydata` | 1 | 1 | 1 | 0 |
| `pylint-dev` | 1 | 1 | 1 | 1 |
| `pytest-dev` | 2 | 1 | 2 | 1 |
| `scikit-learn` | 5 | 4 | 4 | 4 |
| `sphinx-doc` | 2 | 2 | 1 | 1 |
| `sympy` | 12 | 10 | 8 | 4 |

The run was not uniformly better. `matplotlib` was still weaker than both published baselines, and `pytest-dev` slipped behind the market.

## 4. Task-Level Differences

Tasks the relaxed Codex run solved that the published market did not:
- `astropy__astropy-14182`
- `django__django-11999`
- `django__django-12125`
- `django__django-12708`
- `django__django-15252`
- `matplotlib__matplotlib-24149`
- `psf__requests-2317`
- `sphinx-doc__sphinx-11445`
- `sympy__sympy-13031`
- `sympy__sympy-21379`

Tasks the published market solved that the relaxed Codex run did not:
- `django__django-12308`
- `matplotlib__matplotlib-23314`
- `matplotlib__matplotlib-24970`
- `pytest-dev__pytest-7490`

## 5. Operational Cost

This run was materially heavier than the published baselines:
- **321.3M total tokens**
- **66 total attempts**
- **195,802.39 usage-cost units** in the repo's internal accounting

That matters because the result is not "free extra accuracy." It comes with a larger time and token budget.

## 6. Interpretation

The cleanest read is that the Codex + GPT-5.2 path was mostly **speed-limited**, not fundamentally incapable on this task set.

Under the published 900-second cap, the Codex path looked shaky and sometimes uncompetitive. Under the relaxed 1800-second cap, the same model family through the Codex worker path beat both published baselines on the same 50 tasks.

That does **not** invalidate the original market result. The published benchmark asked an apples-to-apples question under a 15-minute cap, and that result should remain the headline comparison. This follow-up answers a different question: how much performance shows up if we give the Codex path more runway?

The answer is: a lot.
