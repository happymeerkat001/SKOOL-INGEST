"""Manifest → transcript.lol fan-out.

Reads ``manifest/skool_videos.csv`` (or whatever path is given), submits
every ``status=pending`` row to transcript.lol, polls for completion, and
writes back the result. Designed to be re-runnable: rows that already have
``status=done`` are skipped.

This module is the one that *actually* spends your transcript.lol minutes.
Treat it as the part to throttle if you want to spread the cost.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable
from pathlib import Path

from . import manifest
from .transcript_lol import Job, TranscriptLol, TranscriptLolError

log = logging.getLogger("skool_ingest.fanout")


def submit_one(client: TranscriptLol, row: manifest.Row) -> manifest.Row:
    """Submit a single row, blocking until terminal status. Returns updated row."""
    if row.status != manifest.STATUS_PENDING:
        log.info("skip %s (status=%s)", row.id, row.status)
        return row
    if row.reachable != "yes":
        log.info("skip %s (not reachable by transcript.lol)", row.id)
        return row

    log.info("submit %s → %s", row.id, row.video_url)
    try:
        job: Job = client.submit(row.video_url)
    except TranscriptLolError as exc:
        row.status = manifest.STATUS_FAILED
        row.failure_reason = f"submit: {exc}"
        row.captured_at = manifest.now_iso()
        return row

    row.status = manifest.STATUS_SUBMITTED
    row.transcript_lol_id = job.id
    row.captured_at = manifest.now_iso()

    try:
        job = client.wait(job.id, poll_every=5.0, max_wait=900.0)
    except TranscriptLolError as exc:
        row.status = manifest.STATUS_FAILED
        row.failure_reason = f"poll: {exc}"
        row.captured_at = manifest.now_iso()
        return row

    if job.status == "done" and job.text:
        row.status = manifest.STATUS_DONE
        row.transcript_url = job.transcript_url or ""
        # Persist the transcript text alongside the manifest for fast search.
        write_transcript(row, job.text)
    else:
        row.status = manifest.STATUS_FAILED
        row.failure_reason = job.error or f"job ended with status={job.status}"

    row.captured_at = manifest.now_iso()
    return row


def run(
    manifest_path: Path,
    client: TranscriptLol,
    *,
    transcripts_dir: Path | None = None,
    sleep_between: float = 1.0,
    only: Iterable[str] | None = None,
) -> dict[str, int]:
    """Submit every pending row, save the manifest, return summary counts."""
    transcripts_dir = transcripts_dir or manifest_path.parent / "transcripts"
    rows = manifest.load(manifest_path)
    counts: dict[str, int] = {"submitted": 0, "done": 0, "failed": 0, "skipped": 0}
    if only:
        wanted = set(only)
        rows = {rid: r for rid, r in rows.items() if rid in wanted}
        if not rows:
            log.warning("--only set but no matching rows in manifest")
    for row in rows.values():
        if row.status != manifest.STATUS_PENDING or row.reachable != "yes":
            counts["skipped"] += 1
            continue
        updated = submit_one(client, row)
        rows[row.id] = updated
        # Write after every row so a Ctrl-C loses at most one job.
        manifest.save(manifest_path, rows.values())
        counts["submitted"] += 1
        if updated.status in ("done", "failed"):
            counts[updated.status] += 1
        time.sleep(sleep_between)
    return counts


def write_transcript(row: manifest.Row, text: str) -> Path:
    """Save the raw transcript next to the manifest as ``<id>.txt``."""
    target = (Path.cwd() / "manifest" / "transcripts" / f"{row.id}.txt")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


# Re-export the Job dataclass for downstream tooling.
__all__ = ["run", "submit_one", "write_transcript", "Job"]
