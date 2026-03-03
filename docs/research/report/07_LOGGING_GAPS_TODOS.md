# Logging Gaps TODOs

## Context
- These TODOs capture missing observability and provenance gaps identified while comparing Market vs Solo GPT-5.2 results and generating slide-ready tables from committed JSON artifacts.

## TODOs
- [ ] **Log per-task outcome labels in committed Phase 2 data**  
  Add a committed artifact with one row per task per config (`market`, `solo_gpt52`) and explicit final status (`PASS`, `FAIL`, `INFRA`, etc.).

- [ ] **Log overlap-scoped pass metrics directly**  
  Publish pass counts/rates for the exact overlap set used in `execution_token_usage.jsonl` (currently 30 tasks), not only the 50-task paired summary.

- [ ] **Log overlap manifest used for each comparison table**  
  Emit and commit a stable `overlap_task_ids` list whenever overlap tables are rendered so figures are reproducible without reverse inference.

- [ ] **Log pass-rate provenance fields**  
  In summary JSON, include explicit metadata for each reported pass rate: source file, task universe size, and filtering logic.

- [ ] **Log per-task market clearing and final winner outcome in one compact table**  
  Add a per-task joined export linking `task_id`, selected winner model, final verify status, attempts, and token usage totals.

- [ ] **Log bid-stage prompt context snapshots**  
  Persist the exact per-round bidder prompt context (or compact hash + canonical reconstruction fields), including what task description text was visible at bid time.

- [ ] **Log expected-cost components used in scoring**  
  Persist per-bid expected-cost breakdown inputs (price source, token priors/defaults, complexity multiplier) to make score reconstruction deterministic.

- [ ] **Log benchmark/task replacement history in canonical dataset metadata**  
  Keep a first-class changelog for replacements (for example, swapped instances) and expose both replaced and replacement IDs in one canonical file.

- [ ] **Redo the bidding prompt**  
  Update `bid_prompt` so bidders see meaningful task description content (not just the first generic line), then re-run comparisons to measure impact.

- [ ] **Add a "data completeness check" step to reporting scripts**  
  Fail fast (or warn loudly) when required fields for requested analyses are missing (for example, per-task pass labels for overlap-only reporting).
