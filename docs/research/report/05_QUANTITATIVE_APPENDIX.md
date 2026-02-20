# Quantitative Appendix: Run Data & Metrics

This appendix contains the raw data outputs, run paths, and specific metric calculations that support the findings in the final report.

## 1. Primary Data Sources

The 50-task evaluation was compiled by joining data across multiple independent runs to compare the Market, Solo model, and External models on identical tasks.

**Market Runs (50 Tasks Total):**
- Batch 1 (0-9): `runs/research/phase2/gateB10_exclfix_t900_20260218T190909Z`
- Batch 2 (10-29): `runs/research/phase2/next20_market_20260219T074528Z`
- Batch 3 (30-49): `runs/research/phase2/next20_contiguous_market_20260219T183750Z`

**Solo GPT-5.2 Runs (Same 50 Tasks, Our Scaffold):**
- Batch 1 (0-9): `runs/research/phase2/solo_gpt52_10_cleanDocker_20260218T225012Z`
- Batch 2 & 3 (10-49): `runs/research/phase2/solo_gpt52_market50_remaining40_20260219T221230Z`

**External Labels (Strong Scaffold):**
- `runs/research/phase1/full_external_20260216T070002Z/calibration_results.jsonl`
- `runs/research/phase1/next63_external_20260216T191746Z/calibration_results.jsonl`

---

## 2. Recomputed 50-Task Summary

The following summary is recomputed from the archived run artifacts listed above (market rollup + paired market-vs-solo summary):

```text
Total tasks evaluated: 50

--- 50-Task Outcomes ---
Market (Our Scaffold): 29.0 / 50 (58.0%)
Solo GPT-5.2 (Our Scaffold): 24.0 / 50 (48.0%)
Solo GPT-5.2 (External): 37.0 / 50 (74.0%)
Oracle Ceiling (External): 42 / 50 (84.0%)

--- Market Retries ---
Passes on Attempt 1: 18
Passes on Attempt 2: 11
Total rescued by retry exclusion: 11

--- Market Allocations (All 50) ---
anthropic:claude-opus-4-5-20251101: 4 wins, 2 passes (50.0% conversion)
anthropic:claude-sonnet-4-5-20250929: 1 wins, 0 passes (0.0% conversion)
google:models/gemini-3-pro-preview: 42 wins, 21 passes (50.0% conversion)
openai:gpt-5-mini-2025-08-07: 20 wins, 2 passes (10.0% conversion)
openai:gpt-5.2-2025-12-11: 9 wins, 3 passes (33.3% conversion)
openai:gpt-5.2-pro-2025-12-11: 6 wins, 1 passes (16.7% conversion)

--- INFRA Noise ---
Market INFRA count: 3 / 50
Solo INFRA count: 0 / 50
```

*(Note: `INFRA` counts are taken from the 50-task market rollup status breakdown.)*

---

## 3. Statistical and Efficiency Metrics

When comparing the Market directly against the best Solo Model (GPT-5.2) on the exact same scaffold, we track statistical significance and cost tradeoffs.

### McNemar Test (Paired Outcomes)
- **Both Pass:** 18
- **Market Only:** 11
- **Solo Only:** 6
- **Both Fail:** 15
- **McNemar Exact p-value:** `0.3323` (Not yet statistically significant at n=50, but directionally positive for the market).

### Difficulty-Stratified Signal
We stratified the 50 tasks based on how many external models could solve them (`external_success_rate`):
- **Hard Band (`external_success_rate <= 0.5`, n=19):**
  - Market: 7/19 (37%)
  - Solo GPT-5.2: 5/19 (26%)
  - External GPT-5.2: 6/19 (32%)
- **Very Easy Band (`external_success_rate = 1.0`, n=25):**
  - Market: 21/25 (84%)
  - Solo GPT-5.2: 16/25 (64%)
  - External GPT-5.2: 25/25 (100%)

### Token Efficiency Tradeoff
- **Total Tokens Used:** Market `5.82M` vs Solo GPT-5.2 `4.37M`.
- **Tokens Per Pass:** Market `~200.8k` vs Solo `~182.2k`.
- **Penalties Per Task:** Market `47.6` vs Solo `64.4`.

The market requires roughly 33% more total tokens than the solo model, primarily driven by evaluating multiple bids and executing retries. However, it only costs ~10% more tokens *per successful pass* because the market secures more total passes.
