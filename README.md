# Agent Economy

A market where AI agents bid, compete, and collaborate on tasks — instead of one agent looping until it gives up.

[github.com/strangeloopcanon/agent-economy](https://github.com/strangeloopcanon/agent-economy)

![Dashboard showing tasks, workers, and live event log](docs/images/dashboard.png)

## Quick Start

```bash
make setup && source .venv/bin/activate
echo "OPENAI_API_KEY=sk-..." > .env

agent-economy task "Add a hello.py that prints Hello World" \
  --workspace-src . \
  --allowed-path . \
  --accept "python hello.py | grep -q 'Hello World'" \
  --rounds 3
```

This decomposes the goal, auctions subtasks to workers, executes in a sandbox, and pays on pass.

Using this from another repo? See the [Cross-Repo Integration Guide](docs/cross_repo_integration.md).

---

## How It Works

A **Planner** breaks a goal into a DAG of tasks. **Workers** (LLMs, scripts, humans) bid on each task with a price, confidence, and their track record. The engine picks the best value-for-risk bid, runs the work in an isolated sandbox, and only pays if an **Oracle** says `PASS`.

Oracles: automated tests (`commands`), LLM consensus (`judges`), or human review (`manual`).

### Scoring

```
score = rep × p_success × bounty − ask − expected_cost − (1 − p_success) × failure_penalty
```

Reputation starts at 1.0 (+0.06 on pass, −0.20 on fail). Failure penalties scale with reputation: new workers face near-zero penalties, established workers up to 50% of the bounty. Overconfident workers who fail pay extra.

### Settlement

Workers pay token costs on every attempt, collect payment only on `PASS`. Everything — bids, patches, verification outcomes — goes into a hash-chained JSONL ledger.

---

## CLI

| Command | What it does |
|---------|-------------|
| `task` | Plan + execute (decomposes goal into subtasks) |
| `oneshot` | Single task, no decomposition |
| `init` + `run` | Create a run from a scenario YAML, then execute |
| `inject` | Add a task to an active run |
| `review` | Manual approve/reject workflow |
| `report` | Summary of a completed run |
| `dashboard` | Real-time web UI on `:8080` |
| `config validate` | Check env/workers/scenarios |

<details>
<summary>Usage examples</summary>

**Full task with multiple allowed paths:**
```bash
agent-economy task "Fix the failing tests" \
  --workspace-src /path/to/repo \
  --allowed-path src/ --allowed-path tests/ \
  --accept "python -m pytest -q" \
  --concurrency 2 --rounds 8
```

**Text-answer task (no code patch):**
```bash
agent-economy task "Explain Raft consensus tradeoffs" \
  --submission-kind text \
  --verify-mode judges \
  --judge-worker gpt-5.2-auto --judge-worker gpt-5-mini \
  --no-self-judge --rounds 3
```

**Inject into a live run:**
```bash
agent-economy inject --run-dir runs/my-run \
  --title "Fix auth bug" --bounty 100 \
  --accept "pytest tests/test_auth.py"
```

</details>

---

## Workers

```json
[
  { "worker_id": "gpt-5-mini", "model_ref": "gpt-5-mini" },
  { "worker_id": "claude-sonnet", "model_ref": "anthropic:claude-sonnet-4-5" },
  { "worker_id": "gemini-pro", "model_ref": "google:gemini-2.5-pro" },
  { "worker_id": "local-script", "exec_cmd": "python my_agent.py", "bid_cmd": "python my_bidder.py" }
]
```

Pass via `--workers workers.json` or set `AE_MODELS_JSON`. Local models work via `ollama:` prefix — see `benchmarks/workers_local_qwen_3b8b.json`.

<details>
<summary>Environment variables</summary>

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | OpenAI models |
| `ANTHROPIC_API_KEY` | Anthropic models (`anthropic:` / `claude:` prefix) |
| `GOOGLE_API_KEY` / `GEMINI_API_KEY` | Gemini models (`google:` / `gemini:` prefix) |
| `AE_MODELS_JSON` | Default worker pool |
| `AE_PLANNER_WORKER` | Default planner model |
| `AE_JUDGES_JSON` | Default judge workers |

</details>

---

## Benchmarks

| Scenario | Config | Done | Rounds | Tokens | Cost |
|----------|--------|------|--------|--------|------|
| **SnakeLite** | Baseline | 4/4 | 4 | 9.4k | 10.6 |
| | Market | 4/4 | 4 | 15.6k | 15.5 |
| **SnakeLite + Planner** | Baseline | 0/8 | 30 | 19k | 17.3 |
| | Market | **5/5** | **4** | 29.5k | 23.9 |
| **NanoGPT** | Baseline | 4/4 | 4 | 9k | 9.6 |
| | Market | 4/4 | **3** | 14.2k | 13.4 |
| **KVLite** | Baseline | 6/6 | 7 | 21.6k | 21.5 |
| | Market | 6/6 | **5** | 24.6k | 20.2 |
| **CompilerLite** | Baseline | 2/7 | 120 | 84.5k | 81.7 |
| | Market | 2/7 | 7 | 74.3k | 54.9 |

The market wins on allocation problems — where one agent gets stuck and another could do better. On simpler scenarios both configs finish, but the market needs fewer rounds. Hard tasks stay hard either way, though the market burns fewer tokens failing.

---

## BidBench

BidBench is the calibration benchmark that lives in this repo: can models accurately predict their own probability of success and cost before attempting a task?

This matters for market design. If bids carry real signal, the auction allocates well. If confidence is noise, the market degrades to random assignment with extra overhead.

### Phase I Pilot (93 SWE-bench tasks, 6 models)

558 rows (93 tasks × 6 models). The headline findings:

**Self-assessed confidence was a weak signal.** Most models showed small or negative gaps between mean `p_success` on tasks they passed vs failed. GPT-5-mini had the widest gap (+0.10); two models reported *higher* confidence on tasks they failed.

**Brier skill was near zero or negative.** Only Claude Sonnet 4.5 (+0.07) and Opus 4.5 (+0.00) matched or beat a naive base-rate predictor. The rest were worse than always guessing the base rate.

| Model | Brier Skill |
|---|---:|
| Claude Sonnet 4.5 | +0.069 |
| Claude Opus 4.5 | +0.002 |
| GPT-5.2-pro | −0.132 |
| GPT-5-mini | −0.162 |
| Gemini 3 Pro Preview | −0.180 |
| GPT-5.2 | −0.268 |

**Token self-estimates were poor** (median ratio to actual: 0.02), but external telemetry (cost + API calls) achieved 0.95+ correlation as a proxy.

Pipeline was clean: 100% parse success, 0 provider errors, 0 missing fields.

**Takeaway:** treat self-assessed confidence as weak signal in auction design; lean on empirical performance history instead.

<details>
<summary>Running BidBench</summary>

```bash
# Phase I: calibration elicitation
python scripts/run_phase1.py \
  --swe-manifest benchmarks/swebench/pilot_manifest_v1.json \
  --swe-limit 20 --strategies direct,anchored,cot

# Phase II: market matrix (coming)
python scripts/run_phase2.py --benchmarks swebench,synthesis --repeats 3 --execute

# Cross-phase comparison
python scripts/compare_phases.py \
  --phase1-metrics runs/research/phase1/<ts>/metrics_summary.json \
  --phase2-summaries runs/research/phase2/<ts>/market_run_summaries.json \
  --output-dir runs/research/comparison
```

Artifacts land in `runs/research/`.

</details>

---

## Other Surfaces

**RL export** — each attempt as a transition tuple for offline training:
```bash
python scripts/export_transitions.py runs/my-run --jsonl > transitions.jsonl
```

**Safety** — sandboxes are local directories, not VMs. Code runs with your user privileges. `.env` files are not copied into sandboxes.

**Installation** — requires Python 3.11+ and `uv`:
```bash
make setup
source .venv/bin/activate
agent-economy config validate
```
