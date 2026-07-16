#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [ ! -f .env ]; then
  echo "Missing .env. Copy .env.example to .env and configure your model first." >&2
  exit 1
fi

if [ ! -f data/pathfinder-1e/generated/documents.jsonl ] || \
   [ ! -d data/pathfinder-1e/vector-index-fastembed ]; then
  echo "PF1E data/index is missing. Run: npm run setup:pf -- /path/to/Pathfinder.chm" >&2
  exit 1
fi

service_log="$(mktemp -t trpg-rule-agent-retrieval.XXXXXX)"
service_pid=""

cleanup() {
  if [ -n "$service_pid" ] && kill -0 "$service_pid" 2>/dev/null; then
    kill "$service_pid" 2>/dev/null || true
    wait "$service_pid" 2>/dev/null || true
  fi
  rm -f "$service_log"
}
trap cleanup EXIT INT TERM

echo "Starting PF1E retrieval service..."
npm run retrieval:pf >"$service_log" 2>&1 &
service_pid=$!

ready=false
for _ in $(seq 1 90); do
  if ! kill -0 "$service_pid" 2>/dev/null; then
    echo "Retrieval service exited during startup:" >&2
    sed -n '1,160p' "$service_log" >&2
    exit 1
  fi
  if python3 -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=1)' >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done

if [ "$ready" != true ]; then
  echo "Retrieval service did not become ready within 90 seconds:" >&2
  sed -n '1,160p' "$service_log" >&2
  exit 1
fi

echo "Retrieval service is ready. Starting TrpgRuleAgent..."
npm run cli -- "$@"
