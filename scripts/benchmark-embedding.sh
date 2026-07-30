#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: bash scripts/benchmark-embedding.sh MODEL SLUG" >&2
  exit 2
fi

model_name="$1"
result_slug="$2"
documents="data/pathfinder-1e/generated/documents.jsonl"
index_dir="data/pathfinder-1e/embedding-benchmarks/${result_slug}"
report="data/pathfinder-1e/generated/retrieval-eval-${result_slug}.json"

if [ ! -x .venv/bin/python ]; then
  echo "Missing .venv. Run npm run setup:pf first." >&2
  exit 1
fi
if [ ! -f "$documents" ]; then
  echo "Missing PF1E documents: $documents" >&2
  exit 1
fi

PYTHONPATH=services/retrieval-python/src .venv/bin/python \
  -m trpg_retrieval.vector_index \
  --documents "$documents" \
  --index-dir "$index_dir" \
  --cache-dir data/fastembed-cache \
  --model "$model_name"

PYTHONPATH=services/retrieval-python/src .venv/bin/python \
  -m trpg_retrieval.evaluation \
  --documents "$documents" \
  --index-dir "$index_dir" \
  --cases rulepacks/pathfinder-1e/evals/retrieval.jsonl \
  --backend hybrid \
  --report "$report"

echo "Benchmark report: $report"
