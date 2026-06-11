#!/usr/bin/env bash
# Install Playwright + chromium into the skool-ingest venv.
#
# This is intentionally not in pyproject.toml's main deps because
# Playwright pulls a 100MB+ browser binary and is only needed for the
# optional login helper.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/../.venv"

if [[ ! -d "$VENV" ]]; then
  echo "no venv at $VENV; run: python3 -m venv .venv" >&2
  exit 1
fi

echo "installing playwright into $VENV"
"$VENV/bin/pip" install --quiet playwright
"$VENV/bin/playwright" install chromium
echo "done. run: $HERE/skool_login.py"
