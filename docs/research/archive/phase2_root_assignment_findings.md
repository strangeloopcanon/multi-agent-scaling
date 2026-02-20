# Phase II Root Assignment Findings (2026-02-17)

This note captures what happened in early Phase II market runs and why root SWE tasks were often assigned to Sonnet/Opus.

## Data inspected

- Ledgers under `/Users/rohit/Documents/Workspace/Coding/agent-economy/runs/research/phase2/*/*/ledger.jsonl`
- Run configs and states under matching task run folders
- Phase I external outcomes from:
  - `/Users/rohit/Documents/Workspace/Coding/agent-economy/runs/research/phase1/full_external_20260216T070002Z/calibration_results.jsonl`
  - `/Users/rohit/Documents/Workspace/Coding/agent-economy/runs/research/phase1/next63_external_20260216T191746Z/calibration_results.jsonl`

## What was observed

- Task-runs with a root SWE task: `30`
- Root task reached at least one assignment: `24`
- Root task never reached assignment: `6`

First root winner counts (`24` runs):

- `claude-sonnet-4-5`: `9`
- `claude-opus-4-5`: `3`
- `gemini-3-pro-preview`: `5`
- `gpt-5.2`: `3`
- `gpt-5-mini`: `4`

The main driver of the Sonnet/Opus skew was participation, not just scoring:

- First-round bidder count distribution on root assignments:
  - `1 bidder`: `10` runs
  - `3-6 bidders`: `14` runs
- In one early family (`smoke10_market_20260217T012419Z`), first root rounds were effectively single-bidder, so the winner was whoever responded.

## Allocation quality against known external outcomes

For runs where at least one model in the pool was externally labeled as a passer for that task:

- Cases: `18`
- Market picked a known passer first: `9`
- Market did not pick a known passer first: `9`

Missed-known-passer cases split into:

- Known passer did not bid that round: `6` (availability/timeout/no-bid path)
- Known passer did bid but lost on score: `3` (score misallocation path)

## Practical implication

For SWE-bench, the primary Phase II benchmark should use:

- `market-only`
- one monolithic root task per instance
- `dag_mode=off`
- `direct_penalty`

Planner DAG mode remains useful as a separate orchestration experiment, but it should not be mixed into the primary SWE benchmark readout.
