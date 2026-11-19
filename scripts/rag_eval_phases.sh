#!/usr/bin/env bash
# Measure the SQL-first assistant phase by phase on the same cases, seed data and models.
#   baseline      enriched documents off, SQL lookups off, SQL aggregates off (vector RAG only)
#   sql-first     verified facts in documents + SQL lookups
#   aggregations  + deterministic SQL aggregates
#   final, final-<n>, targeted-<n>, corrected-<n>, validated-<n>, hardening-<n>
#                 same switches as aggregations, for the current code
#                 (repeat with different names to see run-to-run variance)
#   consistency-<n>  current code, historical dataset (after TAH-139)
#   gen-<n>       current code on generalization dataset v1 (regression set since TAH-139)
#   gen2-<n>      current code on generalization dataset v2 (regression set since TAH-141)
#   blind-v3-<n>  current code on the frozen blind dataset v3 (TAH-140); the dataset hash is checked first
# Usage (repository root): ./scripts/rag_eval_phases.sh [phase ...]
# Writes rag_eval_phase_<phase>.log (readable) and .json (full results).
set -euo pipefail

phases=("$@")
[ "${#phases[@]}" -gt 0 ] || phases=(baseline sql-first aggregations)
[ -f .env ] || { echo ".env missing"; exit 2; }

flags() {
  case "$1" in
    baseline)     echo "-e RAG_DOCUMENT_FACTS=false -e RAG_SQL_LOOKUP=false -e RAG_SQL_AGGREGATES=false" ;;
    sql-first)    echo "-e RAG_DOCUMENT_FACTS=true -e RAG_SQL_LOOKUP=true -e RAG_SQL_AGGREGATES=false" ;;
    aggregations|final|final-*|targeted-*|corrected-*|validated-*|hardening-*|consistency-*|gen-*|gen2-*|blind-v3-*)
      echo "-e RAG_DOCUMENT_FACTS=true -e RAG_SQL_LOOKUP=true -e RAG_SQL_AGGREGATES=true" ;;
    *) echo "unknown phase $1" >&2; exit 2 ;;
  esac
}
for phase in "${phases[@]}"; do flags "$phase" >/dev/null; done
dataset() {
  case "$1" in
    blind-v3-*) echo app/rag/evaluation/generalization_v3.json ;;
    gen2-*) echo generalization-v2 ;;
    gen-*) echo generalization ;;
    *) echo historical ;;
  esac
}
for phase in "${phases[@]}"; do
  if [ -e "rag_eval_phase_${phase}.json" ] && [ "${OVERWRITE:-0}" != 1 ]; then
    echo "rag_eval_phase_${phase}.json exists: choose a new phase name (or OVERWRITE=1)" >&2; exit 2
  fi
  case "$phase" in
    blind-v3-*)
      expected=$(cat backend/app/rag/evaluation/generalization_v3.sha256)
      actual=$( (sha256sum backend/app/rag/evaluation/generalization_v3.json 2>/dev/null || shasum -a 256 backend/app/rag/evaluation/generalization_v3.json) | cut -d' ' -f1)
      [ "$expected" = "$actual" ] || { echo "generalization_v3.json does not match its frozen hash" >&2; exit 2; } ;;
  esac
done
code_version=$(git rev-parse --short HEAD 2>/dev/null || true)

docker compose up -d --build db api qdrant
docker compose build rag-indexer
docker compose stop rag-indexer
trap 'docker compose start rag-indexer >/dev/null' EXIT

run() { local env="$1"; shift; docker compose run --rm -T -e PAYFLOW_CODE_VERSION="$code_version" $env rag-indexer python -m "$@"; }

run "" app.rag.evaluation seed
for phase in "${phases[@]}"; do
  env=$(flags "$phase")
  echo "== $phase: indexing"
  run "$env" app.rag.index
  echo "== $phase: evaluation (retrieval + answers, a few minutes)"
  run "$env" app.rag.evaluation run --dataset "$(dataset "$phase")" --format both > "rag_eval_phase_${phase}.raw"
  sed '/^=====JSON=====$/,$d' "rag_eval_phase_${phase}.raw" > "rag_eval_phase_${phase}.log"
  sed '1,/^=====JSON=====$/d' "rag_eval_phase_${phase}.raw" > "rag_eval_phase_${phase}.json"
  rm -f "rag_eval_phase_${phase}.raw"
  sed -n '3,4p' "rag_eval_phase_${phase}.log"
done
echo "Done. rag-indexer restarts with the configuration in .env (all phases on by default)."
