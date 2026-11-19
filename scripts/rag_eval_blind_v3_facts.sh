#!/usr/bin/env bash
# Fact-level check of blind v3 runs (evaluation/blind_v3/fact_check.py), next to rag_eval_report.sh.
# Usage: ./scripts/rag_eval_blind_v3_facts.sh blind-v3-1 blind-v3-2 blind-v3-3 > rag_eval_report_blind_v3_facts.md
set -euo pipefail
[ "$#" -ge 1 ] || { echo "usage: $0 <run ...>" >&2; exit 2; }
runs=()
for name in "$@"; do
  [ -f "rag_eval_phase_${name}.json" ] || { echo "missing rag_eval_phase_${name}.json" >&2; exit 2; }
  runs+=("/results/rag_eval_phase_${name}.json")
done
docker compose run --rm -T --no-deps -e PYTHONPATH=/app -v "$PWD:/results:ro" rag-indexer \
  python /results/evaluation/blind_v3/fact_check.py app/rag/evaluation/generalization_v3.json "${runs[@]}"
