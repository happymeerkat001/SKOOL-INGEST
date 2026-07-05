#!/usr/bin/env python3
"""Smoke test the local n8n -> Agent-core Stage A path.

This intentionally keeps Agent-core in simulation. It triggers the active n8n
webhook, then verifies Agent-core appended a matching simulated audit record.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_N8N_WEBHOOK_URL = "http://127.0.0.1:5678/webhook/fb-stage-a-inbound"
DEFAULT_ENGINE_HEALTH_URL = "http://127.0.0.1:8000/healthz"
DEFAULT_AUDIT_LOG = ROOT / "logs" / "engine_dispatch.jsonl"
DEFAULT_N8N_DB = Path.home() / ".n8n" / "database.sqlite"
DEFAULT_WORKFLOW_NAME = "Agent-core Stage A inbound simulation"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _json_request(method: str, url: str, *, body: dict | None = None, timeout: int = 10) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            try:
                parsed = json.loads(raw or "{}")
            except json.JSONDecodeError:
                parsed = {"raw": raw}
            return resp.status, parsed
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            parsed = json.loads(raw or "{}")
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return exc.code, parsed


def _iter_audit_records(path: Path, *, start_offset: int = 0) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        handle.seek(start_offset)
        for raw in handle:
            try:
                records.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    return records


def find_audit_record(path: Path, lead_id: str, *, start_offset: int = 0) -> dict | None:
    for record in _iter_audit_records(path, start_offset=start_offset):
        if record.get("lead_id") == lead_id:
            return record
    return None


def latest_n8n_execution(db_path: Path, workflow_name: str = DEFAULT_WORKFLOW_NAME) -> dict | None:
    if not db_path.exists():
        return None
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            select e.id, e.workflowId, e.mode, e.status, e.startedAt, e.stoppedAt
            from execution_entity e
            join workflow_entity w on w.id = e.workflowId
            where w.name = ?
            order by e.id desc
            limit 1
            """,
            (workflow_name,),
        ).fetchone()
    return None if row is None else dict(row)


def run_smoke(
    *,
    engine_health_url: str,
    n8n_webhook_url: str,
    audit_log: Path,
    n8n_db: Path,
    lead_id: str,
    timeout_seconds: float,
) -> list[str]:
    errors: list[str] = []

    status, health = _json_request("GET", engine_health_url)
    if status != 200 or health.get("status") != "ok":
        return [f"engine health failed: status={status} body={health}"]

    start_offset = audit_log.stat().st_size if audit_log.exists() else 0
    payload = {
        "lead_id": lead_id,
        "messages": [{"text": "Just me, no pets. Is it available?"}],
        "metadata": {"rent": 800, "stage": 1, "phone": "+155****0199"},
    }
    status, body = _json_request("POST", n8n_webhook_url, body=payload)
    if status < 200 or status >= 300:
        return [f"n8n webhook failed: status={status} body={body}"]

    deadline = time.monotonic() + timeout_seconds
    record = None
    while time.monotonic() < deadline:
        record = find_audit_record(audit_log, lead_id, start_offset=start_offset)
        if record:
            break
        time.sleep(0.25)

    if record is None:
        errors.append(f"audit record not found for lead_id={lead_id}")
    else:
        if record.get("mode") != "simulation":
            errors.append(f"expected simulation mode, got audit={record}")
        if record.get("sent"):
            errors.append(f"expected no live send, got audit={record}")
        if not record.get("simulated"):
            errors.append(f"expected simulated dispatch, got audit={record}")
        if record.get("status") != "simulated":
            errors.append(f"expected simulated status, got audit={record}")

    execution = latest_n8n_execution(n8n_db)
    if execution is not None:
        if execution.get("mode") != "webhook" or execution.get("status") != "success":
            errors.append(f"latest n8n execution was not a successful webhook: {execution}")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke test local n8n -> Agent-core Stage A wiring")
    parser.add_argument("--env", default=str(ROOT / ".env"), help="dotenv file to read")
    parser.add_argument("--engine-health-url", default=None)
    parser.add_argument("--n8n-webhook-url", default=None)
    parser.add_argument("--audit-log", default=None)
    parser.add_argument("--n8n-db", default=None)
    parser.add_argument("--lead-id", default=f"n8n-stage-a-smoke-{uuid4().hex[:8]}")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    args = parser.parse_args(argv)

    _load_dotenv(Path(args.env))
    errors = run_smoke(
        engine_health_url=args.engine_health_url
        or os.environ.get("ENGINE_HEALTH_URL", DEFAULT_ENGINE_HEALTH_URL),
        n8n_webhook_url=args.n8n_webhook_url
        or os.environ.get("N8N_STAGE_A_WEBHOOK_URL", DEFAULT_N8N_WEBHOOK_URL),
        audit_log=Path(args.audit_log or os.environ.get("ENGINE_AUDIT_LOG", str(DEFAULT_AUDIT_LOG))),
        n8n_db=Path(args.n8n_db or os.environ.get("N8N_SQLITE_DB", str(DEFAULT_N8N_DB))),
        lead_id=args.lead_id,
        timeout_seconds=args.timeout_seconds,
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("n8n stage-a smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
