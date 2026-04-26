# SWE-bench Panel Data

This directory contains two evidence sources over the same Phase II task slice.

## What to use

Start with the two `*_sources.csv` files:

- `swebench_model_task_attempt_panel_published_sources.csv`
  - Rows from our published/report-backed agent-economy runs.
  - Includes model, task, task family, hidden-test success, token consumption, and run provenance.
  - Use this for claims about our scaffold, market, router, Codex diagnostic, and Phase IId results.

- `swebench_site_bashonly_model_task_panel_sources.csv`
  - Rows pulled from the official SWE-bench website leaderboard backing JSON, filtered to our 50-task slice.
  - Includes model, task, task family, success, official site cost, API calls, and site submission provenance.
  - The official website data does not expose per-instance input/output token counts, so `token consumption` is blank here.

The shorter core CSVs keep only the original five requested columns:

- `model`
- `task`
- `task family`
- `success`
- `token consumption`

## Why there are two sources

These should not be merged as if they are the same experiment. Our-work rows are from our local agent-economy execution ledgers. SWE-bench-site rows are official public bash-only leaderboard rows, run under a different scaffold.

Both are useful:

- use our-work rows for the paper/result audit and token-consumption analysis;
- use SWE-bench-site rows as the outside benchmark reference on the same tasks, with cost/API-call fields but no token fields.

## Known caveats

- The original Phase II market `29/50` result has only 30/50 exact raw-ledger task rows because `next20_market_20260219T074528Z` is no longer present locally. The published summary artifacts still preserve the 50-task result.
- The SWE-bench site has no exact `openai:gpt-5.2-pro-2025-12-11` bash-only row. GPT-5.2 high and GPT-5.2 Codex are separate official entries and are not substituted for GPT-5.2 Pro in this export.
- For token boxplots, filter or facet by `published_result`; otherwise the plot mixes different scaffolds and budgets.
- The all-ledger local panel is intentionally not committed because it includes smoke runs, diagnostics, and other local-only attempts.
