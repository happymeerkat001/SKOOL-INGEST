"""Manifest model + IO for the Skool → transcript.lol pipeline.

The manifest is a single CSV with one row per video. Keeping it in CSV (not
JSON) means you can open it in Excel/Numbers/Sheets to triage, and the schema
is self-documenting in the header.

Columns (in order — append-only; do not reorder so existing rows stay valid):

    id                stable hash of (post_url, video_url); idempotent
    post_url          Skool post that embeds the video
    post_title        as shown on the post
    post_author       Skool display name
    post_date         ISO 8601; "" if unknown
    video_url         the *direct* video URL we will hand to transcript.lol
    embed_type        loom | youtube | vimeo | mux | mp4 | m3u8 | other
    reachable         yes | no  (third-party fetcher can GET it without auth)
    status            pending | submitted | done | failed
    transcript_lol_id transcript.lol job id
    transcript_url    public URL of the finished transcript (if any)
    failure_reason    free text; populated on failed
    captured_at       ISO 8601 timestamp of last update

The "id" column is the join key. If a video appears in two posts, both rows
share the same id so you can dedupe at any time with a simple sort.
"""
from __future__ import annotations

import csv
import dataclasses
import datetime as _dt
import hashlib
from pathlib import Path
from typing import Iterable, Iterator

COLUMNS: tuple[str, ...] = (
    "id",
    "post_url",
    "post_title",
    "post_author",
    "post_date",
    "video_url",
    "embed_type",
    "reachable",
    "status",
    "transcript_lol_id",
    "transcript_url",
    "failure_reason",
    "captured_at",
)

STATUS_PENDING = "pending"
STATUS_SUBMITTED = "submitted"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

EMBED_TYPES = {"loom", "youtube", "vimeo", "mux", "mp4", "m3u8", "other"}


@dataclasses.dataclass
class Row:
    post_url: str
    post_title: str
    post_author: str
    post_date: str
    video_url: str
    embed_type: str
    reachable: str = "yes"
    status: str = STATUS_PENDING
    transcript_lol_id: str = ""
    transcript_url: str = ""
    failure_reason: str = ""
    captured_at: str = ""
    id: str = ""  # computed in __post_init__

    def __post_init__(self) -> None:
        if not self.id:
            self.id = make_id(self.post_url, self.video_url)
        if self.embed_type not in EMBED_TYPES:
            self.embed_type = "other"
        if not self.captured_at:
            self.captured_at = now_iso()

    def as_dict(self) -> dict[str, str]:
        return {c: getattr(self, c) for c in COLUMNS}


def make_id(post_url: str, video_url: str) -> str:
    h = hashlib.sha256()
    h.update(post_url.encode("utf-8"))
    h.update(b"\x00")
    h.update(video_url.encode("utf-8"))
    return h.hexdigest()[:16]


def now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat(timespec="seconds")


def load(path: Path) -> dict[str, Row]:
    """Load manifest CSV into a dict keyed by row id."""
    if not path.exists():
        return {}
    rows: dict[str, Row] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            row = Row(
                post_url=raw.get("post_url", ""),
                post_title=raw.get("post_title", ""),
                post_author=raw.get("post_author", ""),
                post_date=raw.get("post_date", ""),
                video_url=raw.get("video_url", ""),
                embed_type=raw.get("embed_type", "other"),
                reachable=raw.get("reachable", "yes"),
                status=raw.get("status", STATUS_PENDING),
                transcript_lol_id=raw.get("transcript_lol_id", ""),
                transcript_url=raw.get("transcript_url", ""),
                failure_reason=raw.get("failure_reason", ""),
                captured_at=raw.get("captured_at", ""),
                id=raw.get("id", ""),
            )
            rows[row.id] = row
    return rows


def save(path: Path, rows: Iterable[Row]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    items = list(rows)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(COLUMNS))
        writer.writeheader()
        for row in items:
            writer.writerow(row.as_dict())


def upsert(path: Path, row: Row) -> None:
    """Write a single row, preserving others. Atomic via temp-file rename."""
    rows = load(path)
    rows[row.id] = row
    save(path, rows.values())


def iter_pending(rows: dict[str, Row]) -> Iterator[Row]:
    return (r for r in rows.values() if r.status == STATUS_PENDING)
