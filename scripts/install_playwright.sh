#!/usr/bin/env bash
# Install Playwright + chromium into the skool-ingest venv.
#
# This is intentionally not in pyproject.toml's main deps because
# Playwright pulls a 100MB+ browser binary and is only needed for the
# optional login helper.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PY="$ROOT/.venv/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "no venv python at $PY; run: cd $ROOT && python3 -m venv .venv" >&2
  exit 1
fi

echo "installing playwright into $ROOT/.venv"
"$PY" -m pip install --quiet playwright
"$PY" -m playwright install chromium
echo "done. run: $PY $HERE/skool_login.py"
