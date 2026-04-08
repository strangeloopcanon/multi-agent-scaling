## Phase IIb centralized-router baseline

Date: 2026-04-07

This repo now supports a prepared Phase II baseline that keeps the existing six-worker scaffold and swaps the market clearing rule for a centralized router.

The new prepared mode is `central_router` in `scripts/run_phase2.py`.

Mechanics:

- The router sees the current ready tasks, the available workers, recent discussion, worker reputation, and task-specific expected-cost hints.
- The router chooses worker-task assignments for the round.
- The chosen worker still submits its own bid for that assigned task.
- The rest of the run stays the same: the same executor path, verifier, timeout settings, retry exclusion, and settlement logic.

The engine now records a `router_decision` event with router token usage so total token accounting includes the coordinator overhead.

Verification completed before landing:

- `make check`
- `make test`

Targeted coverage added:

- engine test for centralized assignment following the chosen worker only
- runner test for prepared `central_router` mode and summary output

Command shape for the full Experiment 2 run:

```bash
python scripts/run_phase2.py \
  --task-manifest <prepared_manifest.json> \
  --prepared-mode central_router \
  --execute \
  --settlement-mode direct_penalty \
  --workers benchmarks/workers_phase2_mixed.json \
  --exclude-failed-workers \
  --require-bid-barrier \
  --dag-mode off \
  --router-model-ref openai:gpt-5.2-pro-2025-12-11 \
  --output-root runs/research/phase2/<new_run_dir>
```
