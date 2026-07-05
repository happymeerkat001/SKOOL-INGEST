#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Missing .venv/bin/python. Run: python3 -m venv .venv && .venv/bin/python -m pip install -e '.[agent,dev]'" >&2
  exit 1
fi

mkdir -p logs
exec .venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port "${ENGINE_PORT:-8000}"
