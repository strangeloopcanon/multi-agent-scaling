#!/usr/bin/env bash
# Retry script for Phase IIa Exp 3 Gemini formula_second_price calls.
# Run this after the Gemini daily quota resets.
#
# Usage:
#   # Day 1: Run shown reserves (~100 calls)
#   bash scripts/retry_gemini_formula_sp.sh shown
#
#   # Day 2: Resume shown (if needed) + run hidden (~50-100 calls)
#   bash scripts/retry_gemini_formula_sp.sh resume-shown
#   bash scripts/retry_gemini_formula_sp.sh hidden
#
#   # After both days: merge all results
#   bash scripts/retry_gemini_formula_sp.sh merge

set -euo pipefail
cd "$(dirname "$0")/.."

SHOWN_DIR="runs/research/phase2a_formula_sp_gemini_shown_v2"
HIDDEN_DIR="runs/research/phase2a_formula_sp_gemini_hidden"
MERGED_OUT="runs/research/phase2a_formula_sp/calibration_results_complete.jsonl"

case "${1:-help}" in
  shown)
    echo "=== Running Gemini shown reserves (5.0, 10.0) ==="
    source .venv/bin/activate
    python scripts/run_phase1.py \
      --task-source external_covered_lite --tasks-limit 50 \
      --models "google:models/gemini-3-pro-preview" \
      --strategies formula_second_price \
      --reserves "5.0,10.0" \
      --execute-calibration --calibration-concurrency 1 \
      --output-root "$SHOWN_DIR"
    echo "=== Done. Check results: ==="
    python3 -c "
import json; from collections import Counter
records = [json.loads(l) for l in open('$SHOWN_DIR/calibration_results.jsonl')]
clean = [r for r in records if not r.get('rationale','').startswith('ERROR:')]
errors = [r for r in records if r.get('rationale','').startswith('ERROR:')]
by_res = Counter(r.get('reserve_shown') for r in clean)
print(f'Total={len(records)}, Clean={len(clean)}, Errors={len(errors)}, by_reserve={dict(by_res)}')
"
    ;;

  resume-shown)
    echo "=== Resuming Gemini shown reserves ==="
    source .venv/bin/activate
    # Filter out errors from prior run so resume only keeps clean records
    python3 -c "
import json
clean_path = '$SHOWN_DIR/calibration_results_clean.jsonl'
with open(clean_path, 'w') as out:
    for line in open('$SHOWN_DIR/calibration_results.jsonl'):
        r = json.loads(line)
        if not r.get('rationale','').startswith('ERROR:'):
            out.write(line)
print('Wrote clean-only resume file')
"
    python scripts/run_phase1.py \
      --task-source external_covered_lite --tasks-limit 50 \
      --models "google:models/gemini-3-pro-preview" \
      --strategies formula_second_price \
      --reserves "5.0,10.0" \
      --execute-calibration --calibration-concurrency 1 \
      --resume-from "$SHOWN_DIR/calibration_results_clean.jsonl" \
      --output-root "$SHOWN_DIR"
    echo "=== Done. Check results: ==="
    python3 -c "
import json; from collections import Counter
records = [json.loads(l) for l in open('$SHOWN_DIR/calibration_results.jsonl')]
clean = [r for r in records if not r.get('rationale','').startswith('ERROR:')]
errors = [r for r in records if r.get('rationale','').startswith('ERROR:')]
by_res = Counter(r.get('reserve_shown') for r in clean)
print(f'Total={len(records)}, Clean={len(clean)}, Errors={len(errors)}, by_reserve={dict(by_res)}')
"
    ;;

  hidden)
    echo "=== Running Gemini hidden reserves ==="
    source .venv/bin/activate
    python scripts/run_phase1.py \
      --task-source external_covered_lite --tasks-limit 50 \
      --models "google:models/gemini-3-pro-preview" \
      --strategies formula_second_price \
      --execute-calibration --calibration-concurrency 1 \
      --output-root "$HIDDEN_DIR"
    echo "=== Done. Check results: ==="
    python3 -c "
import json; from collections import Counter
records = [json.loads(l) for l in open('$HIDDEN_DIR/calibration_results.jsonl')]
clean = [r for r in records if not r.get('rationale','').startswith('ERROR:')]
errors = [r for r in records if r.get('rationale','').startswith('ERROR:')]
by_res = Counter(r.get('reserve_shown') for r in clean)
print(f'Total={len(records)}, Clean={len(clean)}, Errors={len(errors)}, by_reserve={dict(by_res)}')
"
    ;;

  merge)
    echo "=== Merging all results ==="
    python3 -c "
import json
from collections import Counter

files = [
    'runs/research/phase2a_formula_sp/calibration_results.jsonl',
    '$SHOWN_DIR/calibration_results.jsonl',
    '$HIDDEN_DIR/calibration_results.jsonl',
]

seen = set()
merged = []
for path in files:
    try:
        for line in open(path):
            r = json.loads(line.strip())
            if r.get('rationale', '').startswith('ERROR:'):
                continue
            key = (r['model_ref'], r['task_id'], r['strategy'], r.get('reserve_shown'))
            if key not in seen:
                seen.add(key)
                merged.append(r)
    except FileNotFoundError:
        print(f'WARNING: {path} not found, skipping')

with open('$MERGED_OUT', 'w') as f:
    for r in merged:
        f.write(json.dumps(r, ensure_ascii=False) + '\n')

print(f'Merged: {len(merged)} clean records')
print()
for model in sorted(set(r['model_ref'] for r in merged)):
    by_reserve = Counter(r.get('reserve_shown') for r in merged if r['model_ref'] == model)
    print(f'  {model}: {dict(by_reserve)}')

expected = 450
if len(merged) == expected:
    print(f'\n✅ All {expected} records present!')
else:
    print(f'\n❌ Expected {expected}, got {len(merged)}. Missing {expected - len(merged)} records.')
"
    ;;

  *)
    echo "Usage: $0 {shown|resume-shown|hidden|merge}"
    echo ""
    echo "  shown         - Run Gemini for reserve \$5 and \$10 (100 calls)"
    echo "  resume-shown  - Resume shown run (filters errors before resuming)"
    echo "  hidden        - Run Gemini with no reserve shown (50 calls)"
    echo "  merge         - Merge all results into calibration_results_complete.jsonl"
    ;;
esac
