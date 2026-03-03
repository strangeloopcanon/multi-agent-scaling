# Logging Gaps TODOs

## Context
- These TODOs capture missing observability and provenance gaps identified while comparing Market vs Solo GPT-5.2 results and generating slide-ready tables from committed JSON artifacts.

## TODOs
- [x] **Log per-task outcome labels in committed Phase 2 data**  
  Added `docs/research/data/phase2/per_task_outcomes.jsonl` -- 50 rows with `market_pass`, `solo_pass`, `market_tokens`, `solo_tokens`, `source` (observed/inferred).

- [x] **Log overlap-scoped pass metrics directly**  
  Added `docs/research/data/phase2/overlap_manifest.json` with 30-task overlap pass rates (market 17/30, solo 13/30) and confusion matrix.

- [x] **Log overlap manifest used for each comparison table**  
  `overlap_manifest.json` includes `task_ids` (30 IDs), `source_runs`, and `provenance` cross-references to all related data files.

- [x] **Log pass-rate provenance fields**  
  `overlap_manifest.json` includes provenance block linking to source files, task universe size, and filtering logic. `per_task_outcomes.jsonl` includes `source` field (observed vs inferred).

- [x] **Log per-task market clearing and final winner outcome in one compact table**  
  Added `docs/research/data/phase2/market_clearing_details.jsonl` -- 30 rows with winner model, ask, p_success, score, expected cost, verify status, attempts, bid/patch tokens, and score components. 20 tasks missing (raw ledgers lost).

- [ ] **Log bid-stage prompt context snapshots**  
  Persist the exact per-round bidder prompt context (or compact hash + canonical reconstruction fields), including what task description text was visible at bid time.  
  *Note (2026-03-03):* Phase II ran with blurb-only prompts (first line of description). This is now fixed in `prompts.py` but cannot be retroactively captured for past runs. Future runs should persist prompt snapshots.

- [x] **Log expected-cost components used in scoring**  
  `market_clearing_details.jsonl` includes `score_components` (bounty, ask, expected_cost, p_success, failure_penalty) for each task's winning bid. Covers 30/50 tasks.

- [x] **Log benchmark/task replacement history in canonical dataset metadata**  
  Added `docs/research/data/phase2/task_replacement_history.json` documenting the sphinx-doc__sphinx-8595 -> django__django-12708 swap and reason.

- [x] **Redo the bidding prompt**  
  Fixed `agent_economy/prompts.py`: `bid_prompt` now shows full `spec.description` instead of first-line blurb. Bidders see the problem statement, repo, base commit, and hints. Re-running comparisons is a separate future experiment.

- [ ] **Add a "data completeness check" step to reporting scripts**  
  Fail fast (or warn loudly) when required fields for requested analyses are missing (for example, per-task pass labels for overlap-only reporting).
