# n8n Stage A workflow

This directory holds the versioned local n8n workflow for Agent-core Stage A.

## Workflow

- `agent-core-stage-a-inbound.workflow.json`
  - Webhook trigger: `POST /webhook/fb-stage-a-inbound`
  - HTTP Request target: `POST http://127.0.0.1:8000/webhook/fb-inbound`
  - Auth header: `X-Engine-Token: ={{ $env.ENGINE_WEBHOOK_TOKEN }}`
  - Intended Agent-core mode: `ENGINE_MODE=simulation`

The committed JSON is sanitized for portability:

- inactive by default (`"active": false`)
- no local workflow id
- no owner/project metadata
- no timestamps/version history
- no credentials or token values

## Import/update locally

Run from the repo root after `.env` contains `ENGINE_WEBHOOK_TOKEN` and Agent-core
is already healthy on `127.0.0.1:8000`.

For a quick foreground run:

```bash
ENGINE_WEBHOOK_TOKEN="$(python3 - <<'PY'
from pathlib import Path
for raw in Path('.env').read_text(encoding='utf-8').splitlines():
    line = raw.strip()
    if line and not line.startswith('#') and line.startswith('ENGINE_WEBHOOK_TOKEN='):
        print(line.split('=', 1)[1].strip().strip('"').strip("'"))
        break
PY
)" n8n start
```

For supervised local startup, install the committed LaunchAgent:

```bash
mkdir -p ~/Library/LaunchAgents
cp deploy/n8n.launchd.plist ~/Library/LaunchAgents/com.leon.skool-ingest.n8n.plist
launchctl bootout gui/$(id -u)/com.leon.skool-ingest.n8n 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.leon.skool-ingest.n8n.plist
launchctl kickstart -k gui/$(id -u)/com.leon.skool-ingest.n8n
```

The LaunchAgent runs n8n on `127.0.0.1:5678` only and sets the n8n 1.123.x
runtime/deprecation flags explicitly:

- `N8N_RUNNERS_ENABLED=true`
- `DB_SQLITE_POOL_SIZE=5`
- `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` so the workflow can read `$env.ENGINE_WEBHOOK_TOKEN`
- `N8N_GIT_NODE_DISABLE_BARE_REPOS=true`

n8n logs go to `logs/n8n.out.log` and `logs/n8n.err.log`.

In another shell:

```bash
n8n import:workflow --input ops/n8n/agent-core-stage-a-inbound.workflow.json
n8n update:workflow --id <imported-workflow-id> --active=true
```

If this is replacing an existing local workflow, use the n8n UI or CLI to avoid
leaving duplicate active webhooks with the same path.

## Smoke check

```bash
.venv/bin/python scripts/n8n_stage_a_smoke.py
```

A passing smoke check proves:

1. Agent-core `/healthz` is OK.
2. n8n accepts `POST http://127.0.0.1:5678/webhook/fb-stage-a-inbound`.
3. Agent-core appends an audit JSONL record for the smoke lead.
4. The audit record has `mode=simulation`, `sent=false`, `simulated=true`, and
   `status=simulated`.
5. If the local n8n SQLite DB is available, the latest workflow execution is a
   successful `webhook` execution.

Do not use this workflow to promote Stage B. Stage B remains the operator-owned
OpenPhone/Quo checklist in the root README.
