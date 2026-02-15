# Agent Economy

**A Coordination Layer for AI Agents.**

Repository: [github.com/strangeloopcanon/agent-economy](https://github.com/strangeloopcanon/agent-economy)

Rather than a single agent trying to do everything, this system runs a **market** where specialized agents bid, compete, and collaborate to solve complex tasks. It applies economic principles (auctions, reputation, skin-in-the-game) to AI code generation.

---

Compared with a single-agent baseline, market routing solved SnakeLite+Planner in 4 rounds (5/5 tasks) versus 30 rounds (0/8 tasks) in this repo’s benchmark runs.

For one clean task with one clean test, a single agent loop is usually enough: run, check, retry.
The market is for harder cases where allocation matters, not just completion.

## Start Here

- Want a quick run? See [30-Second Demo](#30-second-demo).
- Want to use this from another repo? See [Cross-Repo Integration Guide](docs/cross_repo_integration.md) (copy-pasteable for another LLM).
- Need setup details? See [Installation & Setup](#installation--setup).

## See It In Action

![Dashboard showing tasks, workers, and live event log](docs/images/dashboard.png)

This screenshot captures Round 2 of a 6-task run with two AI workers (`gpt-5-mini` and `gpt-5.2-auto`) competing on cost, confidence, and historical performance.

<details>
<summary>Round-by-round breakdown</summary>

- **Round 0 (T1)**: Both models bid on T1. `gpt-5-mini` bid **12 credits** (90% confidence), while `gpt-5.2-auto` bid **18 credits** (75% confidence). The cheaper, more confident bid won. `gpt-5-mini` delivered a passing patch and earned 12 credits. Its reputation rose from 1.0 → **1.06**.
- **Round 1 (T2)**: Roles reversed. `gpt-5-mini` got overconfident and bid **30 credits**. `gpt-5.2-auto` undercut with **18 credits** and won. It passed verification, earned 18 credits, and its reputation rose to **1.06**.
- **Round 2 (T3)**: `gpt-5-mini` bid **25 credits** (95% confidence) and beat `gpt-5.2-auto`'s 18-credit bid because the scoring formula favors higher confidence. It just finished (see the `PAYMENT_MADE +25` in the log), pushing its balance to **37** and reputation to **1.12**.

**Current State:**
- **DONE**: T1, T2, T3 — completed by alternating winners
- **TODO**: T4, T5, T6 — blocked on dependencies (T4 needs T3, T5 needs T4, etc.)
- **Workers**: `gpt-5-mini` leads with 2 wins (37 credits), `gpt-5.2-auto` has 1 win (18 credits)

**Key Insight**: Work flows to whichever agent offers the best value-for-risk, and reputation compounds over successful deliveries.

</details>

---

## The Core Concept

1.  **Work is a Market**: A "Planner" breaks a goal into a DAG of tasks.
2.  **Agents are Vendors**: Workers bid on tasks with:
    *   `ask`: How much they want to be paid.
    *   `p_success`: How confident they are (self-assessed).
    *   `reputation`: Their historical track record (skin-in-the-game).
3.  **Settlement is Truth**: Work is performed in isolated sandboxes. It is only accepted (and paid for) when an Oracle says `PASS`.
    *   **Oracles**: Automated tests (`commands`), LLM consensus (`judges`), or humans (`manual`).

---

## When A Loop Is Enough (And When It Isn't)

For a single task with a cheap, deterministic oracle (for example, one unit test), a single agent in a loop is often fine.
If verification is simple and objective, auctions are usually overkill.

The loop starts to break when you have many tasks with different difficulty levels and agents with different cost/capability profiles.
One agent in a loop does not know when to stop burning tokens on a hard task and hand off to another model that is better suited.
This is an allocation problem, not just a completion problem.

That pattern already appears in this repo's benchmarks: on SnakeLite + Planner, baseline looping went 0/8 in 30 rounds, while market routing went 5/5 in 4 rounds.
The loop got stuck; the market routed around the stuck agent.

---

## The Economics

### Bidding & Clearing
The engine scores bids to find the best value-for-risk:
```python
score = rep*p_success*bounty - ask - expected_cost - (1-p_success)*failure_penalty
failure_penalty = 0.5*bounty*clamp((rep - 0.5)/0.75, 0, 1)
```
*   **Reputation**: A bounded score updated on pass/fail.
    *   Starts at 1.0.
    *   **Pass**: +0.06 | **Fail**: -0.20
*   **Failure Penalty**: Scaled by reputation.
    *   New workers (rep 0.5) → **0% penalty** (allows recovery).
    *   Established workers (rep 1.25) → **50% penalty** (skin-in-the-game).
*   **Overconfidence Penalty (`P0-lite`)**: If a worker fails, an extra penalty is applied based on reported `p_success` (higher confidence → higher fail penalty).

### Execution & Settlement
*   **Sandboxing**: Every attempt runs in a fresh copy of the workspace.
*   **Submission Kinds**: `patch` (default), `text`, or `json` via `--submission-kind`.
*   **Payment**: Workers pay token costs for every attempt. They only get paid on `PASS` (either their `ask`, or the fixed `bounty` if you run with `--payment-rule bounty`).
*   **Settlement Modes**: `commands` (exit code 0), `judges` (LLM vote), `manual` (human review).

For non-patch tasks, the worker output is persisted in the sandbox workspace as:
- `.agent_economy/submission.txt` for `--submission-kind text`
- `.agent_economy/submission.json` for `--submission-kind json`

### When There Is No `pytest`

Many important tasks do not have an exit code:
- "Is this a good architecture?"
- "Did this essay persuade?"
- "Is this design elegant?"

In the real world, we use social consensus when objective verification is impossible: peer review, editorial boards, juries, ratings, and voting systems.
This project supports the same idea through `judges` settlement mode, where multiple LLM judges vote on pass/fail.

The core pattern has two parts:
1. **Competition with consequences**: multiple agents bid, attempt, and bear costs.
2. **Judgment under comparison**: the key question becomes "which output is best among competitors?" not just "is this output good in isolation?"

In that setup, the market helps create conditions for better verification.
It does more than loop one agent against one task.

### Example: Worker Economics Over 3 Rounds

| Round | Event | Reputation | Balance | Notes |
|-------|-------|------------|---------|-------|
| 1 | T1 PASS (ask=10) | 1.0 → 1.06 | +10 | Worker gains rep and payment |
| 2 | T2 FAIL (penalty=10) | 1.06 → 0.86 | +10 → 0 | Rep drops, penalty applied |
| 3 | T3 PASS (ask=15) | 0.86 → 0.92 | +15 | Slow recovery continues |

---

## Why This Is Interesting

*   **Robustness via redundancy**: If one agent fails, the market penalizes them and re-opens the task. Another agent steps in.
*   **Specialization**: Cheap models for simple tasks, expensive models for hard ones. The market routes automatically.
*   **Auditability**: Every bid, patch, and verification outcome is recorded in an append-only JSONL ledger.
*   **Decision Snapshots**: Market clearing and task assignment events include score components so downstream analysis can explain why a worker was selected.
*   **Iterative Planning**: If tasks fail repeatedly, the system triggers a **Plan Revision** event.

---

## 30-Second Demo

```bash
# After setup (see Installation below)
agent-economy task "Add a hello.py that prints Hello World" \
  --workspace-src . \
  --allowed-path . \
  --accept "python hello.py | grep -q 'Hello World'" \
  --rounds 3
```

This will:
1. Ask a Planner to create a task
2. Agents bid on it
3. Winner creates `hello.py`
4. Verification runs `python hello.py` and checks output

---

<details>
<summary><h2>Installation & Setup</h2></summary>

**Prerequisites**: Python 3.11+, `uv`

1.  **Install**:
    ```bash
    make setup
    source .venv/bin/activate
    ```

2.  **Configure** (if using OpenAI agents):
    ```bash
    echo "OPENAI_API_KEY=sk-..." > .env
    ```

3.  **Validate** your setup:
    ```bash
    agent-economy config validate
    ```

</details>

<details>
<summary><h2>Environment Variables</h2></summary>

Set these in `.env` or your shell to configure defaults:

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | API key for OpenAI models |
| `ANTHROPIC_API_KEY` | API key for Anthropic models (`anthropic:` / `claude:` model refs) |
| `GOOGLE_API_KEY` / `GEMINI_API_KEY` | API key for Google Gemini models (`google:` / `gemini:` model refs) |
| `AE_MODELS_JSON` | Default worker pool as JSON (alternative to `--workers`) |
| `AE_PLANNER_WORKER` | Default planner worker/model ref |
| `AE_JUDGES_JSON` | Default judge workers as JSON list |

Legacy `INST_*` names are still accepted for compatibility.

</details>

---

## Advanced Usage

### Full Task Command
```bash
agent-economy task "Fix the failing tests" \
  --workspace-src /path/to/repo \
  --allowed-path src/ \
  --allowed-path tests/ \
  --accept "python -m pytest -q" \
  --concurrency 2 \
  --rounds 8
```

### Text-Answer Task (No Code Patch)
```bash
agent-economy task "Explain Raft consensus and failure recovery tradeoffs" \
  --submission-kind text \
  --verify-mode judges \
  --judge-worker gpt-5.2-auto \
  --judge-worker gpt-5-mini \
  --no-self-judge \
  --rounds 3
```

### Plan Revision
If a task fails repeatedly (default: 3 times), the engine requests a revised plan.

### Dynamic Task Injection
Inject new tasks into an active run:
```bash
agent-economy inject \
  --run-dir runs/my-active-run \
  --title "Fix critical bug in auth" \
  --bounty 100 \
  --accept "pytest tests/test_auth.py"
```

### Custom Workers
Plug in local scripts, Anthropic models, or human-in-the-loop agents via `workers.json`:
```json
[
  { "worker_id": "gpt-5-mini", "model_ref": "gpt-5-mini" },
  { "worker_id": "claude-sonnet", "model_ref": "anthropic:claude-sonnet-4-5" },
  { "worker_id": "gemini-pro", "model_ref": "google:gemini-2.5-pro" },
  { "worker_id": "local-script", "exec_cmd": "python my_agent.py", "bid_cmd": "python my_bidder.py" }
]
```

### Local Qwen (3B/8B via Ollama)
Use local Qwen workers without `OPENAI_API_KEY` by setting `model_ref` with the `ollama:` provider prefix.

```bash
# 1) Start Ollama and pull local models
ollama pull qwen3:3b
ollama pull qwen3:8b

# 2) Optional if Ollama runs elsewhere
export OLLAMA_BASE_URL=http://127.0.0.1:11434

# 3) Run with local workers
agent-economy oneshot \
  --workers benchmarks/workers_local_qwen_3b8b.json \
  --prompt "Fix failing parser tests" \
  --workspace-src . \
  --allowed-path agent_economy/ \
  --allowed-path tests/ \
  --accept "python -m pytest -q tests/test_parser.py"
```

<details>
<summary>Model naming notes</summary>

Use whatever names exist in your local Ollama registry (`ollama list`).  
If your tags differ, edit `benchmarks/workers_local_qwen_3b8b.json`.

</details>

### Real-Time Dashboard
```bash
agent-economy dashboard --run-dir runs/my-active-run
# Open http://localhost:8080
```

### Benchmarks
```bash
agent-economy init --scenario scenarios/snakelite.yaml --run-dir runs/bench
agent-economy run --run-dir runs/bench
```

### Research Workflow (Phase I/II)
Run the calibration + market research pipeline with script entrypoints:

```bash
# Phase I: calibration elicitation + optional solo baselines
python scripts/run_phase1.py \
  --swe-manifest benchmarks/swebench/pilot_manifest_v1.json \
  --swe-limit 20 \
  --strategies direct,anchored,cot

# Phase II: matrix generation (48 runs by default)
python scripts/run_phase2.py --benchmarks swebench,synthesis --repeats 3

# Execute Phase II matrix live (requires provider credentials)
python scripts/run_phase2.py --benchmarks swebench,synthesis --repeats 3 --execute

# Join cross-phase metrics
python scripts/compare_phases.py \
  --phase1-metrics runs/research/phase1/<timestamp>/metrics_summary.json \
  --phase2-summaries runs/research/phase2/<timestamp>/market_run_summaries.json \
  --output-dir runs/research/comparison
```

Artifacts are written under `runs/research/phase1/`, `runs/research/phase2/`, and
`runs/research/comparison/`.

### Export RL Transitions
Export each attempt as a transition tuple (`action`, `award`, `outcome`, `reward`) for offline training/evaluation:
```bash
python scripts/export_transitions.py runs/my-active-run --jsonl > runs/my-active-run/transitions.jsonl
```

---

## Benchmark Results

Comparing **Market** (multiple competing agents) vs **Baseline** (single agent) across scenarios:

| Scenario | Config | Tasks Done | Rounds | Time | Tokens | Cost |
|----------|--------|------------|--------|------|--------|------|
| **SnakeLite** | Baseline (gpt-5.2) | 4/4 | 4 | 75s | 9.4k | 10.6 |
| | Market (mini+5.2+xhigh) | 4/4 | 4 | 446s | 15.6k | 15.5 |
| **SnakeLite + Planner** | Baseline (gpt-5.2) | 0/8 | 30 | 99s | 19k | 17.3 |
| | Market (mini+5.2+xhigh) | **5/5** | 4 | 700s | 29.5k | 23.9 |
| **NanoGPT** | Baseline (gpt-5.2) | 4/4 | 4 | 60s | 9k | 9.6 |
| | Market (mini+5.2+xhigh) | 4/4 | **3** | 184s | 14.2k | 13.4 |
| **KVLite** | Baseline (gpt-5.2) | 6/6 | 7 | 94s | 21.6k | 21.5 |
| | Market (mini+5.2) | 6/6 | **5** | 127s | 24.6k | 20.2 |
| **CompilerLite** | Baseline (gpt-5.2) | 2/7 | 120 | 417s | 84.5k | 81.7 |
| | Market (mini+5.2+codex) | 2/7 | 7 | 586s | 74.3k | 54.9 |

<details>
<summary>Key Observations</summary>

- **Market succeeds where baseline fails**: On SnakeLite+Planner, baseline completed 0/8 tasks in 30 rounds; market completed 5/5 in just 4 rounds.
- **Market finds winners faster**: NanoGPT and KVLite finish in fewer rounds with market competition.
- **Cost trade-offs**: Market runs use more total tokens due to bidding overhead, but cheaper models win simpler tasks, balancing overall cost.
- **Latency vs parallelism**: Market has higher wall-clock time due to concurrent bidding, but fewer total rounds.
- **Hard tasks remain hard**: CompilerLite challenges both configurations equally, suggesting task complexity limits.

</details>

---

## Safety & Security

*   **Sandboxes are local directories**, not VMs. Code runs with your user privileges.
*   `.env` files are **not** copied to sandboxes by default to prevent leakage.
