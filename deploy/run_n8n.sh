#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

if [[ ! -x "/opt/homebrew/bin/n8n" ]]; then
  echo "Missing /opt/homebrew/bin/n8n. Install n8n before loading this LaunchAgent." >&2
  exit 1
fi

if [[ ! -f ".env" ]]; then
  echo "Missing .env. Copy .env.example to .env and set ENGINE_WEBHOOK_TOKEN first." >&2
  exit 1
fi

ENGINE_WEBHOOK_TOKEN="$(python3 - <<'PY'
from pathlib import Path
for raw in Path('.env').read_text(encoding='utf-8').splitlines():
    line = raw.strip()
    if line and not line.startswith('#') and line.startswith('ENGINE_WEBHOOK_TOKEN='):
        print(line.split('=', 1)[1].strip().strip('"').strip("'"))
        break
PY
)"

if [[ -z "${ENGINE_WEBHOOK_TOKEN}" ]]; then
  echo "Missing ENGINE_WEBHOOK_TOKEN in .env; n8n needs it for the Agent-core header." >&2
  exit 1
fi

export ENGINE_WEBHOOK_TOKEN

# Local-only Stage A n8n service. Keep env access available because the
# workflow reads ENGINE_WEBHOOK_TOKEN via $env.ENGINE_WEBHOOK_TOKEN, and set
# the n8n 1.123.x deprecation flags explicitly so launchd starts quietly.
export N8N_HOST="${N8N_HOST:-127.0.0.1}"
export N8N_LISTEN_ADDRESS="${N8N_LISTEN_ADDRESS:-127.0.0.1}"
export N8N_PROTOCOL="${N8N_PROTOCOL:-http}"
export N8N_PORT="${N8N_PORT:-5678}"
export N8N_RUNNERS_ENABLED="${N8N_RUNNERS_ENABLED:-true}"
export DB_SQLITE_POOL_SIZE="${DB_SQLITE_POOL_SIZE:-5}"
export N8N_BLOCK_ENV_ACCESS_IN_NODE="${N8N_BLOCK_ENV_ACCESS_IN_NODE:-false}"
export N8N_GIT_NODE_DISABLE_BARE_REPOS="${N8N_GIT_NODE_DISABLE_BARE_REPOS:-true}"

mkdir -p logs
exec /opt/homebrew/bin/n8n start
