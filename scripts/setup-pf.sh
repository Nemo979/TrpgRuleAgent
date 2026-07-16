#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [ "$#" -ne 1 ]; then
  echo "Usage: npm run setup:pf -- /absolute/path/to/Pathfinder.chm" >&2
  exit 2
fi

for command in node npm python3; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Missing required command: $command" >&2
    exit 1
  fi
done

node -e '
  const [major, minor] = process.versions.node.split(".").map(Number);
  if (major < 22 || (major === 22 && minor < 19)) {
    console.error(`Node.js 22.19+ is required; found ${process.versions.node}`);
    process.exit(1);
  }
'

if ! command -v extract_chmLib >/dev/null 2>&1; then
  echo "CHM extractor is required." >&2
  echo "  macOS:  brew install chmlib" >&2
  echo "  Ubuntu: sudo apt-get install libchm-bin" >&2
  exit 1
fi

chm_path="$1"
if [ ! -f "$chm_path" ]; then
  echo "CHM file not found: $chm_path" >&2
  exit 1
fi

echo "[1/5] Installing Node.js dependencies"
npm install --ignore-scripts

echo "[2/5] Creating Python virtual environment"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi

echo "[3/5] Installing vector retrieval dependencies"
npm run vector:install

echo "[4/5] Importing the PF1E CHM"
npm run rules:import:pf-chm -- "$chm_path"

echo "[5/5] Building the local vector index"
npm run vector:build:pf

if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "Created .env from .env.example. Add your model endpoint and API key before starting."
fi

echo
echo "Setup complete. Configure .env, then run: npm start"
