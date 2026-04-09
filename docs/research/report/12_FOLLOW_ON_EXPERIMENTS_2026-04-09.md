# Follow-On Experiments After Phase IIb

Date: 2026-04-09

## Naming

Use these names going forward:

- Phase II: Market Scaffold vs Solo GPT-5.2
- Phase IIb: Matched Centralized-Router Baseline
- Phase IIc: Market Calibration Intervention

The current "Experiment 2" result is best referred to as **Phase IIb** in the paper and future notes.

## What Phase IIb established

Phase IIb held the worker pool fixed and changed the chooser.

Result:

- market: `23 / 50`
- centralized router: `27 / 50`

This means the current evidence supports model diversity more strongly than the current market-clearing rule.

## High-priority follow-ons

### Phase IIc: Market Calibration Intervention

Goal:

- keep the market chooser,
- keep the same six workers,
- improve the bids.

Mechanics:

- before bidding, give each worker a short held-out calibration card about itself,
- include overall pass rate,
- include overconfidence or underconfidence direction,
- include typical token underestimation,
- optionally include coarse repository-level history only when enough held-out examples exist.

Why this is the best immediate next run:

- Experiment I already showed that this kind of self-knowledge improves forecasting,
- Phase IIb suggests that the current market fails because bid quality is weak,
- this is the most direct test of the paper's current causal story.

Success criterion:

- rerun the same 50-task market slice,
- compare against the current Phase IIb market result,
- check whether the market recovers solved tasks and reduces Gemini over-assignment.

### Router-ablation: Different Central Router Models

Goal:

- check whether the central-router win depends heavily on the coordinator model.

Variants:

- `gpt-5.2`
- `gpt-5.2-pro`
- Gemini
- Claude Opus

Question:

- does the central-router advantage persist across strong coordinator choices,
- or was the current result mostly a `gpt-5.2-pro` effect.

### Router-ablation: Market-Chosen Router

Goal:

- let the system choose the coordinator itself before routing tasks.

Mechanics:

- add a short router-selection stage,
- choose a coordinator from the same worker pool,
- then let that coordinator assign workers for the round.

Question:

- does a market-selected coordinator work better than a fixed coordinator.

### Market Score Ablation

Goal:

- check whether the current clearing rule is too sensitive to aggressive low-ask high-confidence bids.

Variants:

- stronger penalty on overconfident failure,
- confidence clipping,
- bid floor or ask regularization,
- reputation or calibration adjustment on reported `p_success`.

Question:

- can the market stop over-selecting Gemini-like aggressive bids without giving up diversity.

## One run worth doing right now

Run **Phase IIc: Market Calibration Intervention** next.

This is the cleanest follow-on because it tests the main current explanation:

- the market is not failing because diverse workers are useless,
- the market is failing because the bids are noisy and overconfident.

The concrete version to run now:

- use the same 50-task prepared slice,
- use the same six workers,
- use the same two-attempt cap and `900` second timeout,
- keep the market chooser unchanged,
- add a held-out calibration card to each worker before bidding,
- rerun the market only,
- compare worker assignment mix, Gemini conversion, total solves, and token use against Phase IIb.
