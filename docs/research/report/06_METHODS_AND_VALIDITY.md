# Methods and Validity

## Study Objective
Evaluate whether a multi-model market configuration improves SWE-bench task completion versus a strong single-model baseline under the same execution scaffold.

## Task Set
- Canonical source: `benchmarks/swebench/phase2_93_manifest_v1.json`
- Analysis set: first 50 manifest slots, with one deterministic strict-preflight replacement:
  - replaced: `sphinx-doc__sphinx-8595`
  - replacement: `django__django-12708`
  - reason: deterministic strict preflight failure on gold patch setup
- Final analyzed set size: 50 tasks

## Systems Compared
1. Market (our scaffold): 6-model worker pool, market bidding, `direct_penalty` settlement.
2. Solo GPT-5.2 (our scaffold): same harness and run policy, single worker.
3. External GPT-5.2 baseline: public SWE-bench labels used as reference only.
4. External oracle ceiling: per-task max across the 6 external model labels.

## Core Run Configuration (Our Scaffold)
- `dag_mode=off` (monolithic SWE tasks)
- `market_only=true`
- `settlement_mode=direct_penalty`
- `force_bids=true`
- `exclude_failed_workers=true`
- `max_attempts=2`
- `bid_timeout_seconds=90`
- `execution_timeout_seconds=900`

## Data Sources
- Market rollup data:
  - `runs/research/phase2/gateB10_exclfix_t900_20260218T190909Z`
  - `runs/research/phase2/next20_market_20260219T074528Z`
  - `runs/research/phase2/next20_contiguous_market_20260219T183750Z`
- Solo GPT-5.2 data:
  - `runs/research/phase2/solo_gpt52_10_cleanDocker_20260218T225012Z`
  - `runs/research/phase2/solo_gpt52_market50_remaining40_20260219T221230Z`
- External labels:
  - `runs/research/phase1/full_external_20260216T070002Z/calibration_results.jsonl`
  - `runs/research/phase1/next63_external_20260216T191746Z/calibration_results.jsonl`

## Primary Metrics
- Pass rate (`passes / tasks`)
- Paired confusion counts (`both_pass`, `market_only`, `solo_only`, `both_fail`)
- McNemar exact p-value (two-sided primary; one-sided exploratory)
- Allocation diagnostics (wins, conversion, bidder participation, fallback/error rates)
- Efficiency diagnostics (total tokens, tokens per pass, penalties per task)

## Statistical Notes
- The main paired significance test is McNemar exact (two-sided).
- For the 50-task sample: two-sided `p=0.3323`.
- Interpretation: directional advantage for market, but not conventionally significant at this sample size.

## Threats to Validity
1. External-vs-local scaffold mismatch:
   - External labels come from stronger, interactive setups; they are not strict counterfactuals for this harness.
2. Limited sample size:
   - `N=50` supports directional comparisons but has limited power for small deltas.
3. Replacement in analysis slice:
   - One deterministic replacement means the set is not a pure contiguous 0-49 slice.
4. Infrastructure sensitivity:
   - `INFRA` outcomes remain non-zero in market rollup (`3/50`), which can bias pass-rate estimates.
5. Single-configuration estimate:
   - Findings are for one policy/configuration family and should not be generalized to all market mechanisms.

## Reproducibility
- Canonical summary artifacts used by the report:
  - `docs/research/report/data/phase2_rollup_50.json`
  - `docs/research/report/data/market_vs_gpt52_solo50_summary.json`
- The narrative sections in this folder should be interpreted as derived commentary over those two data artifacts.
