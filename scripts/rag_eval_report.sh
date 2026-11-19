#!/usr/bin/env bash
# Markdown report over saved runs (rag_eval_phase_<name>.json in the repository root).
# Usage: ./scripts/rag_eval_report.sh <dataset> <run ...> [-- <baseline run ...>] > report.md
#   ./scripts/rag_eval_report.sh historical hardening-1 hardening-2 hardening-3 -- validated-1 validated-2 validated-3
#   ./scripts/rag_eval_report.sh generalization gen-1 gen-2 gen-3
set -euo pipefail

[ "$#" -ge 2 ] || { echo "usage: $0 <historical|generalization|generalization-v2|app/rag/evaluation/generalization_v3.json> <run ...> [-- <baseline run ...>]" >&2; exit 2; }
dataset="$1"; shift
runs=(); baseline=(); target=runs
for name in "$@"; do
  if [ "$name" = "--" ]; then target=baseline; continue; fi
  [ -f "rag_eval_phase_${name}.json" ] || { echo "missing rag_eval_phase_${name}.json" >&2; exit 2; }
  if [ "$target" = runs ]; then runs+=("/results/rag_eval_phase_${name}.json"); else baseline+=("/results/rag_eval_phase_${name}.json"); fi
done
args=(--dataset "$dataset" --runs "${runs[@]}")
[ "${#baseline[@]}" -eq 0 ] || args+=(--baseline "${baseline[@]}")
code_version=$(git rev-parse --short HEAD 2>/dev/null || true)
[ -z "$code_version" ] || args+=(--code-version "$code_version")
docker compose run --rm -T --no-deps -v "$PWD:/results:ro" rag-indexer python -m app.rag.evaluation report "${args[@]}"
