## Phase IIb: Matched Centralized-Router Baseline

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

## Final matched run

Date: 2026-04-09

The final Phase IIb comparison finished on the 50-task prepared slice with the same worker pool, the same `900` second execution limit, the same two-attempt cap, the same verifier path, and the same full task descriptions shown to both sides.

The market run was completed from these roots:

- `runs/research/phase2/exp2_market_matched_timeoutaligned_20260409T030344Z`
- `runs/research/phase2/exp2_market_shard01_offset002_limit016_20260409T033114Z`
- `runs/research/phase2/exp2_market_shard02_offset018_limit016_20260409T033114Z`
- `runs/research/phase2/exp2_market_shard03_offset034_limit016_20260409T033114Z`

The centralized-router run was completed from these roots:

- `runs/research/phase2/exp2_central_router_matched_timeoutaligned_20260409T030344Z`
- `runs/research/phase2/exp2_central_shard01_offset002_limit016_20260409T033114Z`
- `runs/research/phase2/exp2_central_shard02_offset018_limit016_20260409T033114Z`
- `runs/research/phase2/exp2_central_shard03_offset034_limit016_20260409T033114Z`

Merged outcome:

- market solved `23 / 50` tasks
- centralized router solved `27 / 50` tasks
- overlap table: `21` both passed, `2` market-only, `6` central-only, `21` neither
- McNemar exact `p = 0.2890625`

Merged cost and token totals:

- market total tokens: `5,072,291`
- centralized router total tokens: `3,479,510`
- market tokens per solved task: `220,534`
- centralized router tokens per solved task: `128,871`
- market penalties per task: `52.60`
- centralized router penalties per task: `53.52`

Interpretation:

- On this matched rerun, access to the same six-model worker pool plus a centralized chooser outperformed the market clearing rule on solved tasks.
- The centralized router also used substantially fewer total tokens.
- This result weakens the claim that the Phase II scaffold gain comes from the market-style routing rule itself. On the matched 50-task rerun, the non-market chooser was stronger.

Suggested paper framing:

- Keep the older `29 / 50` versus `24 / 50` result as a scaffold-level finding: a six-worker scaffold beat a same-scaffold solo GPT-5.2 baseline.
- Use this Phase IIb rerun for the mechanism-level claim: when both sides get the same six-worker pool, the centralized chooser beat the market (`27 / 50` vs `23 / 50`).
- The clean interpretation is that current evidence supports multi-model diversity more strongly than it supports the present market-clearing rule.

## Why the market dropped in the rerun

The clearest change in the rerun was Gemini.

On the older observed raw market ledgers that still survive, Gemini was more conservative:

- average ask: `29.7`
- average stated `p_success`: `0.662`
- outcomes: `12 PASS`, `12 FAIL`, `2 INFRA`

On the full Phase IIb rerun, Gemini became much more aggressive:

- average ask: `19.7`
- average stated `p_success`: `0.838`
- outcomes: `13 PASS`, `29 FAIL`, `3 INFRA`

That means Gemini got many more assignments without producing many more passes.

The key point is that this was not mostly verifier corruption.

Gemini's rerun failures were mainly ordinary execution failures:

- `14` patch apply failures
- `8` cases with no valid patch found
- `6` timeouts at `900s`
- `3` `INFRA` outcomes

The rerun therefore points to a bid-quality problem rather than a checker problem.

The market selected Gemini more often because Gemini bid more aggressively. Gemini then converted those wins much worse than before.

Retry did usually happen after Gemini failed:

- `31` unique tasks had a Gemini failure
- `30` of those got a retry by a different worker
- only `8` were rescued on retry
- `22` still failed after the retry

So the drop is not a retry-rule bug. The market spent too many first attempts on Gemini and then could not recover most of them with a single second try.

The clean interpretation is:

- richer task descriptions changed Gemini's bidding behavior,
- Gemini became easier for the market to select,
- and the resulting first attempts converted poorly.

This is why the matched centralized-router result matters. The centralized router saw the same full task descriptions and still beat the market.

## Follow-on runs

Use these names going forward:

- Phase II: Market Scaffold vs Solo GPT-5.2
- Phase IIb: Matched Centralized-Router Baseline
- Phase IIc: Codex Relaxed-Time Diagnostic
- Phase IId: Market Calibration Intervention

### Best next run

Run **Phase IId: Market Calibration Intervention** next.

Keep the market chooser and the six-worker pool exactly the same. Change only the bid prompt.

The Phase II runner now supports this through `--worker-calibration-source <phase1_baseline.jsonl>`, which injects a held-out self-knowledge note into each worker's private bidding context.

Before bidding, give each worker a short held-out calibration card with:

- overall pass rate
- whether the worker tends to be overconfident or underconfident
- typical token underestimation
- coarse repository-level history only when there is enough held-out data

This is the cleanest next run because Phase Ib showed that self-knowledge improves forecasting, and Phase IIb suggests the current market is failing because the bids are too noisy and too optimistic.

### Gemini prompt sensitivity probe

Date: 2026-04-09

We ran a small live Gemini-only bid probe on `astropy__astropy-14182` to check why the first Phase IId smoke did not move Gemini's first-round bid.

Saved artifacts:

- `runs/research/phase2/phase2d_gemini_prompt_probe_20260409T000000Z/gemini_prompt_probe.json`
- `runs/research/phase2/phase2d_gemini_prompt_probe_20260409T000000Z/gemini_prompt_probe_extra.json`

Important prompt detail:

- `expected_cost_hint≈2.83` is not a self-knowledge statistic.
- It is the market's rough estimate of Gemini's internal model-usage cost for attempting this task.
- In the current bid prompt, that line sits in the same private context block as the held-out self-knowledge note.

Probe result on the same task:

- baseline cost hint only: `ask=20`, `p_success=0.80`
- current Phase IId note: `ask=40`, `p_success=0.75`
- richer stats note with Astropy-specific history: `ask=30`, `p_success=0.85`
- forceful guardrail note: `ask=45`, `p_success=0.81`
- hard cap `0.70`: `ask=45`, `p_success=0.70`
- hard cap `0.65` or skip: `ask=50`, `p_success=0.65`

What this says:

- The current held-out note can move Gemini in the right direction on this task.
- The current held-out note is also unstable. Repeating the same live call produced both `ask=25, p_success=0.80` and `ask=40, p_success=0.75`.
- A richer note can make Gemini more aggressive again when the extra stats sound favorable. In this case, the small-sample Astropy history (`3 / 3` held-out passes) pushed the bid back upward.
- A hard numeric guardrail is the first thing that stopped Gemini from winning this task under the old round-0 competitor bids.

Mechanically, the threshold was:

- `ask=45`, `p_success=0.70` was enough to push Gemini below Claude Sonnet on the old round-0 comparison.
- `ask=50`, `p_success=0.65` pushed Gemini below Claude Sonnet and GPT-5.2.

This points to a concrete design lesson for Phase IId:

- keep the self-knowledge note separate from the cost hint,
- avoid small-sample repo-specific success lines when they are flattering,
- and test explicit calibration rules such as confidence caps or shrinkage toward held-out pass rate.

### Later ablations

- Router model sweep: rerun the centralized-router baseline with `gpt-5.2`, `gpt-5.2-pro`, Gemini, and Claude Opus as the coordinator.
- Market-chosen router: add a short router-selection stage so the system chooses the coordinator from the same worker pool before routing tasks.
- Market score ablation: try stronger overconfidence penalties, confidence clipping, or calibration adjustments on reported `p_success`.

### Phase IId first-5 hard-prior slice

Date: 2026-04-09

We ran the first five tasks of the 50-task live slice with the new `hard_prior_v1` calibration mode.

Mechanically, this changed only the private bid note. Each worker still saw the same task and the same market rules, but the bid prompt now started from a held-out prior:

- `prior_success = pass_rate - overconfidence_gap`, clamped to `[0.15, 0.85]`
- if the worker stayed near that prior, it had to respect an ask floor tied to that prior
- the cost hint was moved into a separate private section

Saved combined summary:

- `runs/research/phase2/phase2d_market_hardprior_first5_summary_20260409T000000Z.json`

Canonical run roots for the first-5 slice:

- tasks `1-2`: `runs/research/phase2/phase2d_market_hardprior_first5_20260409T174100Z`
- task `3`: `runs/research/phase2/phase2d_market_hardprior_task003_20260409T181200Z`
- task `4`: `runs/research/phase2/phase2d_market_hardprior_task004_20260409T181200Z`
- task `5`: `runs/research/phase2/phase2d_market_hardprior_task005_20260409T181200Z`

Top-line result:

- solved: `0 / 5`
- Gemini round-0 wins: `1 / 5`
- non-Gemini round-0 wins: `4 / 5`

Per-task outcomes:

- `astropy__astropy-14182`: round 0 winner `claude-opus-4-5`; round 1 `FAIL`; retry `claude-sonnet-4-5`; retry `INFRA`
- `matplotlib__matplotlib-23299`: round 0 winner `claude-opus-4-5`; round 1 `FAIL`; retry `claude-sonnet-4-5`; retry `FAIL`
- `psf__requests-2317`: round 0 winner `gemini-3-pro-preview`; round 1 `FAIL`; retry `claude-opus-4-5`; retry `FAIL`
- `scikit-learn__scikit-learn-25747`: round 0 winner `claude-opus-4-5`; round 1 `FAIL`; retry `claude-sonnet-4-5`; retry `FAIL`
- `django__django-11964`: round 0 winner `claude-opus-4-5`; round 1 `FAIL`; retry `claude-sonnet-4-5`; retry `FAIL`

What changed in the bids:

- On the same three tasks where the old matched market rerun gave Gemini `ask=20`, `p_success=0.80`, the hard-prior version moved Gemini to:
  - `astropy__astropy-14182`: `ask=45`, `p_success=0.70`
  - `matplotlib__matplotlib-23299`: `ask=45`, `p_success=0.70`
  - `psf__requests-2317`: `ask=40`, `p_success=0.85`

This means the hard-prior rule did the main thing it was designed to do on harder tasks. It pushed Gemini out of the first slot on `astropy` and `matplotlib`, and it also held Gemini to its prior on `scikit-learn` and `django`. Gemini still won `psf__requests-2317`, where the task text contained an explicit localized fix and Gemini argued that this was direct evidence the task was easier than average.

### Phase IId targeted 5 after verifier cleanup fix

Date: 2026-04-10

The first hard-prior slice exposed a separate execution bug. The engine was marking worker attempts as timed out after `900` seconds, but the acceptance command could keep running in the background. The missing cleanup path had two layers:

- the outer verifier needed a real command timeout and process-group cleanup,
- and `swebench_eval` needed to forward termination to its own `run_evaluation` child process.

The code now does both:

- `verify.py` sends `SIGTERM`, waits briefly, then sends `SIGKILL` if the command still has not exited,
- `swebench_eval.py` tracks its active harness child and kills that child group when it receives a termination signal,
- `run_phase2.py` now rewrites SWE-bench acceptance commands so they get an outer timeout (`execution_timeout_seconds - 30`) instead of running without any command-level timeout.

Verification completed before the rerun:

- `.venv/bin/python -m pytest -q tests/test_phase2_runner.py tests/test_verify.py tests/test_research_swebench_eval.py`
- `make check`

We then reran a targeted five-task slice that was deliberately chosen from tasks where the older published market run had wins but the later rerun had losses:

- `matplotlib__matplotlib-24970`
- `pylint-dev__pylint-7080`
- `scikit-learn__scikit-learn-13142`
- `sympy__sympy-16792`
- `django__django-11964`

Saved run root:

- valid rerun: `runs/research/phase2/phase2d_targeted5_hardprior_20260410T003800Z`

Discarded invalid roots:

- `runs/research/phase2/phase2d_targeted5_hardprior_20260409T191000Z`
- `runs/research/phase2/phase2d_targeted5_hardprior_20260409T194800Z`
- `runs/research/phase2/phase2d_targeted5_hardprior_20260409T201000Z`

The three `2026-04-09` roots were scrubbed because the Docker service disappeared mid-run. Those results are not valid solver evidence.

Top-line result:

- solved: `4 / 5`
- failed runs: `0`
- stray verifier processes after completion: `0`

What changed mechanically:

- Gemini won `0 / 5` opening rounds.
- Gemini's bids stayed high and cautious across the slice:
  - `matplotlib__matplotlib-24970`: round 0 `ask=45`, `p_success=0.68`
  - `pylint-dev__pylint-7080`: round 0 `ask=41`, `p_success=0.70`
  - `scikit-learn__scikit-learn-13142`: round 0 `ask=45`, `p_success=0.85`
- `sympy__sympy-16792`: round 0 `ask=45`, `p_success=0.68`
- `django__django-11964`: round 0 `ask=45`, `p_success=0.68`
- The market routed the first attempt to Claude workers on all five tasks.

Per-task outcomes:

- `matplotlib__matplotlib-24970`: round 0 `claude-sonnet-4-5` `FAIL`; retry `gpt-5.2` `PASS`
- `pylint-dev__pylint-7080`: round 0 `claude-opus-4-5` `INFRA`; retry `claude-sonnet-4-5` `PASS`
- `scikit-learn__scikit-learn-13142`: round 0 `claude-opus-4-5` `FAIL`; retry `claude-sonnet-4-5` `FAIL`
- `sympy__sympy-16792`: round 0 `claude-opus-4-5` `PASS`
- `django__django-11964`: round 0 `claude-opus-4-5` `PASS`

Interpretation:

- The verifier cleanup bug is fixed for this path. The targeted rerun completed fully and left no lingering `swebench_eval` or `run_evaluation` processes behind.
- The hard-prior bidding rule did suppress Gemini's over-selection on this slice.
- On a valid rerun of this regression-style slice, the market recovered to `4 / 5`.
- That is back in line with the older live evidence on these tasks. The older published market run solved `4 / 5` on the same slice, and the older solo GPT-5.2 run solved `3 / 5`.
- The one remaining miss was `scikit-learn__scikit-learn-13142`, where both attempts failed cleanly.

What this says:

- the cleanup fix was necessary and correct,
- the earlier `0 / 5` on this second slice was a broken harness result, not a meaningful market result,
- and the hard-prior market can perform well on this slice once the Docker-backed evaluation path stays healthy.

### Phase IId full 50-task combined result

Date: 2026-04-10

We then completed the full Phase IId result in three clean pieces:

- the validated targeted slice: `runs/research/phase2/phase2d_targeted5_hardprior_20260410T003800Z`
- the large continuation run that completed `41` tasks before the outer session stopped: `runs/research/phase2/phase2d_remaining45_hardprior_20260410T045117Z`
- the clean tail rerun for the final four tasks: `runs/research/phase2/phase2d_tail4_hardprior_20260410T142400Z`

Saved combined artifacts:

- `runs/research/phase2/phase2d_full50_combined_20260410T171500Z.json`
- `runs/research/phase2/phase2d_full50_combined_20260410T171500Z_task_outcomes.csv`
- `runs/research/phase2/phase2d_full50_combined_20260410T171500Z_model_outcomes.csv`

Top-line result:

- solved: `24 / 50`
- pass rate: `48.0%`
- total tokens: `5,760,326`
- tokens per pass: `240,014`

How to read it:

- The hard-prior intervention clearly helped on the regression slice. That is the `4 / 5` targeted rerun above.
- On the full 50-task total, the gain was small. The matched market moved from `23 / 50` in Phase IIb to `24 / 50` in Phase IId.
- So the intervention improved some bad allocation cases, but it did not repair the broader mechanism enough to beat the centralized router (`27 / 50`) or recover the older published market scaffold result (`29 / 50`).
- The cleanest interpretation now is still the same: model diversity helps, but the current decentralized bidding signal remains too weak and too unstable for this market rule to realize the full benefit of that diversity.

Reconciliation against the older published `29 / 50`:

- The repo's canonical published Phase II market total still sums to `29 / 50` in `docs/research/data/phase2/per_task_outcomes.jsonl`.
- The new Phase IId full rerun is raw-ledger confirmed at `24 / 50`.
- Direct task-by-task comparison gives `22` shared passes, `7` old-only passes, and `2` new-only passes.
- Old-only passes: `astropy__astropy-12907`, `django__django-12308`, `matplotlib__matplotlib-23314`, `pytest-dev__pytest-7432`, `scikit-learn__scikit-learn-13142`, `scikit-learn__scikit-learn-13496`, `sympy__sympy-15345`
- New-only passes: `django__django-14534`, `sympy__sympy-21379`
- One caution remains: some rows in the older published market table are marked `source=\"inferred\"` because one middle raw market batch was lost earlier in the project. So the older `29 / 50` is confirmed from the saved canonical published artifacts, while the new `24 / 50` is confirmed directly from raw ledgers.
