#!/usr/bin/env python3
"""Smoke test the locally deployed agent-core service.

Checks /healthz and one simulated /webhook/fb-inbound round trip. The service
must be running with ENGINE_MODE=simulation for this smoke check.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "http://127.0.0.1:8000"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _request(method: str, url: str, *, token: str = "", body: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Engine-Token"] = token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = resp.read().decode("utf-8")
            return resp.status, json.loads(payload or "{}")
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8")
        try:
            parsed = json.loads(payload or "{}")
        except json.JSONDecodeError:
            parsed = {"raw": payload}
        return exc.code, parsed


def run_smoke(base_url: str, token: str) -> list[str]:
    errors: list[str] = []
    base = base_url.rstrip("/")
    status, health = _request("GET", f"{base}/healthz")
    if status != 200 or health.get("status") != "ok":
        errors.append(f"healthz failed: status={status} body={health}")

    status, body = _request(
        "POST",
        f"{base}/webhook/fb-inbound",
        token=token,
        body={
            "lead_id": "smoke-simulated",
            "messages": [{"text": "Just me, no pets. Is it available?"}],
            "metadata": {"rent": 800, "stage": 1, "phone": "+15555550199"},
        },
    )
    if status != 200:
        errors.append(f"webhook failed: status={status} body={body}")
        return errors
    dispatch = body.get("dispatch") or {}
    if not body.get("sla_met"):
        errors.append(f"sla not met: body={body}")
    if dispatch.get("sent"):
        errors.append(f"smoke expected simulation but sent live: dispatch={dispatch}")
    if not dispatch.get("simulated"):
        errors.append(f"dispatch was not simulated: dispatch={dispatch}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke test the local agent-core service")
    parser.add_argument("--base-url", default=os.environ.get("ENGINE_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--token", default="", help="X-Engine-Token override")
    parser.add_argument("--env", default=str(ROOT / ".env"), help="dotenv file to read first")
    args = parser.parse_args(argv)

    _load_dotenv(Path(args.env))
    token = args.token or os.environ.get("ENGINE_WEBHOOK_TOKEN", "")
    errors = run_smoke(args.base_url, token)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("engine smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
