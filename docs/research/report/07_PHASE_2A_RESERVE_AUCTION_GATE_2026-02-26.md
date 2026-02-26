# Phase IIa Reserve Auction Gate (2026-02-26)

## So what
Phase IIa is now set up and runnable end-to-end. The 20-task gate completed successfully across 3 models, 3 prompting variants, 2 reserve treatments, and 3 seeds.

The main signal is coherent with the design: `known_first_price` produces materially higher expected and realized profit than `hidden_first_price` across all three strategies, while keeping low seed variance.

## Scope
- 20 tasks (`external_covered_lite`)
- 3 models: GPT-5.2, Claude Opus 4.5, Gemini 3 Pro Preview
- 3 strategies: `direct_bid`, `prob_tokens`, `plan_prob_tokens`
- 2 treatments: `hidden_first_price`, `known_first_price`
- 3 seeds: `42`, `314`, `2718`
- `n_draws=500` per row

## Headline results (pooled across models)
- `direct_bid`: hidden `mean_expected_profit=0.341`, known `3.546`
- `prob_tokens`: hidden `1.743`, known `3.490`
- `plan_prob_tokens`: hidden `1.737`, known `3.476`

## Interpretation for next step
- The experiment harness and treatment logic are working and producing stable outputs.
- The 20-task run is enough to sanity-check setup and math.
- We should treat 50 tasks as the Phase IIa readout set before drawing stronger conclusions.

<details>
<summary>Artifacts</summary>

- Run root: `runs/research/phase2a_reserve_20260226T075754Z`
- Calibration rows: `runs/research/phase2a_reserve_20260226T075754Z/calibration_results.jsonl`
- Reserve outputs:
  - `reserve_hidden_first_price_seed42.json`
  - `reserve_known_first_price_seed42.json`
  - `reserve_hidden_first_price_seed314.json`
  - `reserve_known_first_price_seed314.json`
  - `reserve_hidden_first_price_seed2718.json`
  - `reserve_known_first_price_seed2718.json`
- Summary tables:
  - `runs/research/phase2a_reserve_20260226T075754Z/summary_pooled.csv`
  - `runs/research/phase2a_reserve_20260226T075754Z/summary_per_model.csv`

</details>

<details>
<summary>Notes and caveats</summary>

- In this gate, `direct_bid` under hidden reserve underperforms relative to the token-based variants because one model produced asks that were rarely competitive under hidden reserve.
- This does not block progression; it is exactly the kind of behavior the hidden-reserve treatment is meant to surface.
- Prior market-vs-solo work can be referenced as Phase IIb in follow-up writeups; this note is intentionally scoped to Phase IIa only.

</details>
