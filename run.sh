#!/usr/bin/env bash
# run.sh — one-shot end-to-end driver for skool-ingest.
#
# Use this after you have:
#   1. Pasted TRANSCRIPT_LOL_API_KEY into .env
#   2. Either run scripts/skool_login.py OR dropped a cookies.txt into ./cookies/
#
# Usage:
#   bash run.sh                 # full run with defaults
#   bash run.sh --dry-run       # walk Skool but don't submit to transcript.lol
#   bash run.sh --backend notte # use the Notte backend instead of cookies
#
# What it does, in order, with hard stops on any failure:
#   1. Sanity-check the .env file (exits if API key is missing)
#   2. Run the Skool crawl → manifest/skool_videos.csv
#   3. Show a summary of what was found
#   4. Submit every pending row to transcript.lol
#   5. Show the final manifest status
#   6. Render per-video captures into the Obsidian vault
#
# Designed so you can paste the output back to me and I'll know exactly
# where it stopped.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

DRY_RUN=0
BACKEND="cookies"
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --backend) shift; BACKEND="${1:-cookies}" ;;
    --backend=*) BACKEND="${arg#*=}" ;;
    -h|--help)
      sed -n '2,15p' "$0"
      exit 0
      ;;
  esac
done

# Load .env into the shell for the duration of this script.
if [[ ! -f .env ]]; then
  echo "ERROR: .env not found at $HERE/.env" >&2
  echo "Run: cp .env.example .env  &&  \$EDITOR .env" >&2
  exit 2
fi

# shellcheck disable=SC1091
set -a; source .env; set +a

if [[ -z "${TRANSCRIPT_LOL_API_KEY:-}" || "${TRANSCRIPT_LOL_API_KEY}" == "*** ]]; then
  echo "ERROR: TRANSCRIPT_LOL_API_KEY is missing or still the placeholder in .env" >&2
  echo "Get an API key from https://transcript.lol → API Docs, then update .env" >&2
  exit 2
fi

if [[ "$BACKEND" == "cookies" && ! -f cookies/skool.txt ]]; then
  echo "ERROR: cookies/skool.txt not found" >&2
  echo "Either run: .venv/bin/python scripts/skool_login.py" >&2
  echo "Or, with a logged-in Skool browser, export cookies to ./cookies/skool.txt" >&2
  exit 2
fi

if [[ "$BACKEND" == "notte" && -z "${NOTTE_API_KEY:-}" ]]; then
  echo "ERROR: --backend notte requires NOTTE_API_KEY in .env" >&2
  echo "Sign up at https://console.notte.cc and paste the key into .env" >&2
  exit 2
fi

if [[ ! -x .venv/bin/python ]]; then
  echo "ERROR: .venv not set up. Run: python3 -m venv .venv && .venv/bin/pip install -e .[dev]" >&2
  exit 2
fi

PY=.venv/bin/python
CLASSROOM_URL="${SKOOL_CLASSROOM_URL:-https://www.skool.com/coliving-freedom-unlocked-5532/classroom}"
VAULT_DIR="${VAULT_DIR:-$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/AI-Vault}"

echo "==> Step 1/5: crawl Skool classroom (backend=$BACKEND)"
$PY -m skool_ingest crawl \
  --classroom-url "$CLASSROOM_URL" \
  --backend "$BACKEND" \
  ${BACKEND:+--cookies cookies/skool.txt} \
  --out manifest/skool_videos.csv

echo
echo "==> Step 2/5: manifest summary"
$PY -m skool_ingest status --manifest manifest/skool_videos.csv

if [[ "$DRY_RUN" == "1" ]]; then
  echo
  echo "==> --dry-run: skipping fanout and render."
  echo "Manifest written to: $HERE/manifest/skool_videos.csv"
  exit 0
fi

echo
echo "==> Step 3/5: fan-out to transcript.lol (one submission per row)"
$PY -m skool_ingest fanout --manifest manifest/skool_videos.csv --sleep 2.0

echo
echo "==> Step 4/5: final manifest status"
$PY -m skool_ingest status --manifest manifest/skool_videos.csv

echo
echo "==> Step 5/5: render captures into Obsidian vault"
if [[ -d "$VAULT_DIR" ]]; then
  $PY -m skool_ingest render \
    --manifest manifest/skool_videos.csv \
    --vault-dir "$VAULT_DIR"
  echo
  echo "Captures written to: $VAULT_DIR/Skool Ingest/"
else
  echo "Vault dir not found at: $VAULT_DIR"
  echo "Pass --vault-dir /path/to/vault to render."
fi

echo
echo "==> Done. Summary above. Open the vault's 'Skool Ingest/_index.md' for captures."
