"""Ingest operator-saved Facebook captures into the JSONL lead store."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from .extract import extract_capture, load_sidecar, should_skip_capture
from .models import LeadCandidate, load, merge, save


def iter_capture_files(captures_dir: Path) -> list[Path]:
    """Return ingestible capture files under a directory, excluding sidecars and OS files."""
    if not captures_dir.exists():
        return []
    return [
        path
        for path in sorted(captures_dir.rglob("*"))
        if path.is_file() and not should_skip_capture(path)
    ]


def ingest_captures(captures_dir: Path, leads_path: Path) -> dict[str, Any]:
    """Walk saved captures, extract leads, merge them into the JSONL store, and save."""
    existing = load(leads_path)
    rows: dict[str, LeadCandidate] = dict(existing)
    files = iter_capture_files(captures_dir)
    leads_seen = 0
    leads_new = 0
    leads_updated = 0
    failed = 0

    for capture in files:
        extracted = extract_capture(capture, load_sidecar(capture))
        for lead in extracted:
            leads_seen += 1
            if lead.extraction == "failed":
                failed += 1
            if lead.id in rows:
                rows[lead.id] = merge(rows[lead.id], lead)
                leads_updated += 1
            else:
                rows[lead.id] = lead
                leads_new += 1

    save(leads_path, rows.values())
    return {
        "files_read": len(files),
        "leads_seen": leads_seen,
        "leads_new": leads_new,
        "leads_updated": leads_updated,
        "failed": failed,
        "leads_path": str(leads_path),
    }


def refuse_live_capture(leads_path: Path | None = None) -> int:
    """Fail-loudly stub for live Facebook capture, intentionally absent in v1."""
    _ = leads_path
    print(
        "live capture not implemented in v1; save pages manually and run ingest --captures",
        file=sys.stderr,
    )
    return 2


def status_summary(leads_path: Path) -> dict[str, Any]:
    rows = load(leads_path)
    return {
        "total": len(rows),
        "by_score_band": dict(Counter(row.score_band for row in rows.values())),
        "by_review_status": dict(Counter(row.review_status for row in rows.values())),
        "by_source_type": dict(Counter(row.source_type for row in rows.values())),
        "by_extraction": dict(Counter(row.extraction for row in rows.values())),
        "leads_path": str(leads_path),
    }


def print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))
