#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: npm run rules:import:pf-chm -- /absolute/path/to/pathfinder.chm" >&2
  exit 2
fi

if ! command -v extract_chmLib >/dev/null 2>&1; then
  echo "extract_chmLib is required. On macOS: brew install chmlib" >&2
  exit 1
fi

chm_path="$1"
if [ ! -f "$chm_path" ]; then
  echo "CHM file not found: $chm_path" >&2
  exit 1
fi

extracted_dir="data/pathfinder-1e/extracted"
mkdir -p "$extracted_dir"
extract_chmLib "$chm_path" "$extracted_dir"
npm run rules:import:pf
