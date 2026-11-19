#!/usr/bin/env bash
# Run the RAG evaluation once per embedding model, same chat model and seed data.
# Usage (from the repository root): ./scripts/rag_eval_compare.sh nomic-embed-text bge-m3
# Writes rag_eval_<model>.log (and rag_eval_<model>_retrieval.log). Models must be pulled in Ollama.
set -euo pipefail

[ "$#" -ge 1 ] || { echo "usage: $0 <embed-model> [<embed-model> ...]"; exit 2; }
[ -f .env ] || { echo ".env missing"; exit 2; }
if grep -qE '^OLLAMA_EMBED_(QUERY|DOCUMENT)_PREFIX=' .env; then
  echo "Remove OLLAMA_EMBED_QUERY_PREFIX / OLLAMA_EMBED_DOCUMENT_PREFIX from .env:"
  echo "prefixes now follow the model, and nomic's prefixes would distort the other models."
  exit 2
fi
for model in "$@"; do
  curl -fsS "http://127.0.0.1:11434/api/tags" | grep -qF -e "\"$model\"" -e "\"$model:latest\"" \
    || { echo "model $model missing: ollama pull $model"; exit 2; }
done

docker compose up -d --build db api qdrant
docker compose build rag-indexer
docker compose stop rag-indexer
trap 'docker compose start rag-indexer >/dev/null' EXIT

run() { docker compose run --rm -T -e OLLAMA_EMBED_MODEL="$1" rag-indexer python -m "${@:2}"; }

run "$1" app.rag.evaluation seed
for model in "$@"; do
  slug=$(echo "$model" | tr -c 'A-Za-z0-9\n' '_')
  echo "== $model: indexing"
  run "$model" app.rag.index
  echo "== $model: retrieval only"
  run "$model" app.rag.evaluation run --retrieval-only > "rag_eval_${slug}_retrieval.log"
  echo "== $model: retrieval + answers (a few minutes)"
  run "$model" app.rag.evaluation run > "rag_eval_${slug}.log"
  sed -n '3,4p' "rag_eval_${slug}.log"
done
echo "Done. The rag-indexer service restarts with the model configured in .env."
