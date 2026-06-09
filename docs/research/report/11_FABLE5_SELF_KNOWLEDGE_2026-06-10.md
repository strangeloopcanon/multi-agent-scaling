# Fable 5 self-knowledge probes (2026-06-10)

Claude Fable 5 (released 2026-06-09) was probed with the phase-1 direct calibration
protocol via Cursor subagents, since it has no SWE-bench leaderboard entry. Two
probes: forecast-only on 12 hard-biased phase-1 tasks, and a full
forecast-solve-grade loop on the 15 hub_vs_spoke benchmark tasks.

## Probe 1: SWE-bench Lite, 12 tasks (forecast only)

Tasks drawn from the canonical 93 (5 hard: 0-1/6 baseline models passed; 4 medium;
3 easy), prompts generated with `build_calibration_prompt` (direct strategy), one
fresh subagent per task, no repo access. Data:
`runs/research/phase1/fable_elicitation_20260609/`.

- Mean stated p 0.883 (six-model mean on same tasks: 0.660; their realized pass
  rate: 0.347). Near-flat band 0.88-0.93 across difficulty; one dip to 0.60.
- Spearman(p, 6-model passes) = +0.20. Surface pattern resembles Gemini 3 Pro's
  flat overconfidence in phase 1.
- Caveat: most rationales claim recognition of the task and its gold patch, so
  high confidence may be justified contamination rather than miscalibration.
  No outcomes yet (needs Docker solve+grade to compute Brier).

## Probe 2: hub_vs_spoke, 15 tasks (full loop)

One fresh subagent forecast per task, one fresh subagent one-shot answer per task,
graded by the benchmark's own GPT-5.2 judge (temp 0, pass = score >= 7; exact
match for reasoning-001). Data:
`hub_vs_spoke/results/fable_calibration_20260610/`.

| Metric | Fable 5 | Notes |
| --- | ---: | --- |
| Pass rate | 0.867 (13/15) | baselines: market 0.756, solo Opus 4.6 0.733, hub-spoke 0.667 |
| Mean stated p | 0.850 | bias -1.7pp vs realized |
| Brier | 0.117 | 0.060 excluding reasoning-001 artifact |
| ECE | 0.149 | n=15, sparse bins |

The one real miss was forecast: reasoning-004 (logic grid) got the lowest-but-one
p (0.65) with a rationale that named the exact failure mode (puzzle admits two
solutions; judge holds one), and it failed at score 3. reasoning-005 (p 0.60,
flawed magic-square premise) passed by confronting the flaw. The reasoning-001
"failure" is a harness artifact: correct answer 10/33 rendered as LaTeX
`\frac{10}{33}`, which the substring exact-match misses.

## Why it matters

- On novel tasks Fable 5 shows genuinely useful self-knowledge: spread forecasts
  (0.60-0.97), near-zero global bias, and its low-confidence calls identified the
  real trap. Phase-1 models never produced this pattern (best Brier 0.156, all
  with weak per-task discrimination).
- On SWE-bench Lite the forecasts are flat and high, consistent with recognition
  of contaminated tasks. MarketBench-style calibration scoring on public
  SWE-bench tasks may increasingly measure contamination awareness, not
  self-assessment; newer task pools are needed.
- Caveats: Cursor-subagent scaffold differs from the paper's API calls
  (temperature, system context); n is small; single rep per task; judge variance
  not averaged over reps as in the hard_run baselines (3 reps there).
