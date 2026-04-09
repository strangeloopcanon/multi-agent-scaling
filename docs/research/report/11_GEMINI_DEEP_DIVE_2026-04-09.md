# Gemini Deep Dive: Old Phase II Market Run vs Experiment II Rerun

Date: 2026-04-09

## Scope

This note compares Gemini's behavior in the archived Phase II market ledgers that still survive (`10 + 20 = 30` tasks with raw market ledgers) against the full 50-task Experiment II market rerun.

The goal is narrow:

- check whether Gemini failed many of the same tasks,
- separate checker noise from actual Gemini failures,
- see whether Gemini's bidding behavior changed between the old market run and the new rerun.

## Data availability

The original 50-task Phase II market rollup still exists, but the middle 20-task raw market ledgers were lost.

That means:

- old top-line market result is still `29 / 50`,
- old task-level winner and bid details are only directly observable for 30 tasks,
- old Gemini deep-dive comparisons are strongest on the 24 overlapping tasks where Gemini won in both the old observed ledgers and the new rerun.

## Top-line Gemini comparison

Observed old market ledgers (`30` tasks of raw provenance):

- Gemini won `26` tasks
- Gemini outcomes: `12 PASS`, `12 FAIL`, `2 INFRA`
- average ask: `29.7`
- average stated `p_success`: `0.662`
- average patch input tokens: `55.2k`
- average patch output tokens: `1.47k`

Experiment II market rerun (`50` tasks, full raw provenance):

- Gemini won `45` assignments across `44` unique tasks
- Gemini outcomes: `13 PASS`, `29 FAIL`, `3 INFRA`
- average ask: `19.7`
- average stated `p_success`: `0.838`
- average patch input tokens: `61.4k`
- average patch output tokens: `2.48k`

The rerun made Gemini much more aggressive:

- ask dropped by about `10` points on average
- stated success rose by about `0.18`
- Gemini won more tasks, but converted those wins much worse

## Failure-mode shift

Old observed Gemini failures (`14` failed tasks):

- `6` verified but unsolved (`resolved=False` after patch applied)
- `4` no patch found
- `3` patch apply failed
- `1` missing report / infra

New Gemini failures (`32` failed tasks):

- `14` patch apply failed
- `8` no patch found
- `6` executor timeout at `900s`
- `2` verified but unsolved
- `2` missing report / infra

This is the main behavioral shift.

In the older observed ledgers, Gemini failures were more often "the patch ran but did not solve the task."

In the rerun, Gemini failures were much more often:

- malformed output,
- patch application failure,
- or timing out before completing the attempt.

## Repeated Gemini-won tasks

There are `24` tasks where Gemini won in both the old observed ledgers and the new rerun.

Across those `24` overlapping Gemini-won tasks:

- `6` passed in both runs
- `6` passed old but failed new
- `3` failed old but passed new
- `9` failed in both runs

On these overlapping tasks, Gemini again became more aggressive in the rerun:

- average ask change: `-8.4`
- average stated `p_success` change: `+0.171`

For the `6` tasks that flipped from old-pass to new-fail, the rerun changed Gemini's bids by:

- average ask change: `-7.7`
- average stated `p_success` change: `+0.20`

Concrete flip examples:

- `django__django-11133`
  - old: pass, ask `25`, `p_success=0.60`
  - new: fail, ask `15`, `p_success=0.85`
  - new failure note: `no patch found`
- `pylint-dev__pylint-7080`
  - old: pass, ask `38`, `p_success=0.75`
  - new: fail, ask `15`, `p_success=0.85`
  - new failure note: `executor_timeout_after_s=900`
- `scikit-learn__scikit-learn-13142`
  - old: pass, ask `25`, `p_success=0.60`
  - new: fail, ask `15`, `p_success=0.85`
  - new failure note: `patch apply failed`
- `sphinx-doc__sphinx-8721`
  - old: pass, ask `18`, `p_success=0.65`
  - new: fail, ask `20`, `p_success=0.80`
  - new failure note: `executor_timeout_after_s=900`
- `sympy__sympy-15345`
  - old: pass, ask `25`, `p_success=0.60`
  - new: fail, ask `15`, `p_success=0.80`
  - new result: patch applied but task still unresolved

## Did the verifier break?

Only a small part of Gemini's rerun drop looks like checker noise.

New rerun Gemini outcomes were:

- `29 FAIL`
- `3 INFRA`
- `13 PASS`

The three Gemini `INFRA` tasks were:

- `django__django-11999`
- `scikit-learn__scikit-learn-13496`
- `sympy__sympy-16792`

The larger story is not verifier corruption.

Most Gemini rerun losses were ordinary task failures with concrete execution problems:

- patch could not be applied,
- no valid patch was produced,
- or the attempt timed out.

## Interpretation

The rerun does not show a simple "Gemini became a worse problem solver" story.

It shows a more specific change:

- Gemini bid more aggressively,
- Gemini won more tasks,
- Gemini then failed many of those wins through malformed submissions or timeouts.

That means the market rerun got worse mainly because Gemini became easier for the scoring rule to select, while its execution quality on those selected tasks got worse.

The full-description rerun did not make the benchmark universally worse: the centralized router used the same full task descriptions and still beat the market.

The cleaner interpretation is:

- richer task descriptions changed Gemini's self-assessment and bidding behavior,
- the current market rule rewarded that more aggressive behavior,
- and the resulting assignments converted poorly.
