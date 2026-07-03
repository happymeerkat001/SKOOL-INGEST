"""Posting draft model, JSONL store, and 2:30am slot logic."""
from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCHEMA_VERSION = 1
DEFAULT_POST_HOUR = 2
DEFAULT_POST_MINUTE = 30
YES_NO = {"yes", "no"}
TARGET_SURFACES = {"marketplace", "group", "page", "other"}

FIELDS: tuple[str, ...] = (
    "id",
    "schema_version",
    "topic",
    "lead_ids",
    "template_id",
    "title",
    "copy_text",
    "price_text",
    "location",
    "images_note",
    "target_surface",
    "scheduled_for",
    "timezone",
    "approved_by_human",
    "scheduled_in_meta_business_suite",
    "posted_at",
    "notes",
    "created_at",
    "updated_at",
)

CHECKLIST_FIELDS = {
    "approved_by_human",
    "scheduled_in_meta_business_suite",
    "posted_at",
    "notes",
}


@dataclasses.dataclass
class PostDraft:
    topic: str
    copy_text: str
    lead_ids: list[str] = dataclasses.field(default_factory=list)
    template_id: str = ""
    title: str = ""
    price_text: str = ""
    location: str = ""
    images_note: str = ""
    target_surface: str = "other"
    scheduled_for: str = ""
    timezone: str = ""
    approved_by_human: str = "no"
    scheduled_in_meta_business_suite: str = "no"
    posted_at: str = ""
    notes: str = ""
    created_at: str = ""
    updated_at: str = ""
    id: str = ""
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.lead_ids = [str(item) for item in (self.lead_ids or [])]
        self.target_surface = _coerce_choice(self.target_surface, TARGET_SURFACES, "other")
        self.approved_by_human = _coerce_choice(self.approved_by_human, YES_NO, "no")
        self.scheduled_in_meta_business_suite = _coerce_choice(
            self.scheduled_in_meta_business_suite, YES_NO, "no"
        )
        if not self.timezone:
            self.timezone = default_timezone_name()
        if not self.created_at:
            self.created_at = now_iso()
        if not self.updated_at:
            self.updated_at = self.created_at
        if not self.title:
            self.title = self.topic
        if not self.id:
            self.id = make_id(self.topic, self.created_at)

    def as_dict(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in FIELDS}


def now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat(timespec="seconds")


def default_timezone_name() -> str:
    local = _dt.datetime.now().astimezone().tzinfo
    key = getattr(local, "key", None)
    return str(key or "UTC")


def get_zone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"invalid timezone: {tz_name}") from exc


def make_id(topic: str, created_at: str) -> str:
    h = hashlib.sha256()
    h.update(topic.encode("utf-8"))
    h.update(b"\x00")
    h.update(created_at.encode("utf-8"))
    return h.hexdigest()[:16]


def load(path: Path) -> dict[str, PostDraft]:
    if not path.exists():
        return {}
    rows: dict[str, PostDraft] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            raw = json.loads(line)
            if not isinstance(raw, dict):
                continue
            known = {field: raw[field] for field in FIELDS if field in raw}
            draft = PostDraft(**known)
            rows[draft.id] = draft
    return rows


def save(path: Path, rows: Iterable[PostDraft]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row.as_dict(), ensure_ascii=False, sort_keys=False) + "\n")


def merge(existing: PostDraft, incoming: PostDraft) -> PostDraft:
    data = incoming.as_dict()
    for field in CHECKLIST_FIELDS:
        data[field] = getattr(existing, field)
    data["id"] = existing.id
    data["schema_version"] = max(existing.schema_version, incoming.schema_version)
    data["updated_at"] = now_iso()
    return PostDraft(**data)


def upsert(path: Path, incoming: PostDraft) -> dict[str, PostDraft]:
    rows = load(path)
    if incoming.id in rows:
        rows[incoming.id] = merge(rows[incoming.id], incoming)
    else:
        rows[incoming.id] = incoming
    save(path, rows.values())
    return rows


def next_slot(existing: Iterable[PostDraft], tz_name: str, now: _dt.datetime | None = None) -> str:
    zone = get_zone(tz_name)
    current = now.astimezone(zone) if now is not None else _dt.datetime.now(zone)
    candidate_date = current.date()
    candidate = _dt.datetime.combine(
        candidate_date,
        _dt.time(DEFAULT_POST_HOUR, DEFAULT_POST_MINUTE),
        tzinfo=zone,
    )
    if candidate <= current:
        candidate_date = candidate_date + _dt.timedelta(days=1)

    claimed_dates = {_scheduled_date(draft.scheduled_for, zone) for draft in existing}
    while candidate_date in claimed_dates:
        candidate_date = candidate_date + _dt.timedelta(days=1)
    return _dt.datetime.combine(
        candidate_date,
        _dt.time(DEFAULT_POST_HOUR, DEFAULT_POST_MINUTE),
        tzinfo=zone,
    ).isoformat(timespec="seconds")


def normalize_explicit_at(value: str, tz_name: str) -> str:
    zone = get_zone(tz_name)
    parsed = _dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    else:
        parsed = parsed.astimezone(zone)
    return parsed.isoformat(timespec="seconds")


def status_summary(path: Path) -> dict[str, Any]:
    rows = load(path)
    upcoming = sorted(rows.values(), key=lambda draft: draft.scheduled_for)
    return {
        "total": len(rows),
        "approved": _count_yes(rows.values(), "approved_by_human"),
        "scheduled": _count_yes(rows.values(), "scheduled_in_meta_business_suite"),
        "posted": sum(1 for draft in rows.values() if draft.posted_at),
        "upcoming": [
            {"id": draft.id, "scheduled_for": draft.scheduled_for, "topic": draft.topic}
            for draft in upcoming[:10]
        ],
        "drafts_path": str(path),
    }


def refuse_publish() -> int:
    print(
        "auto-posting is not implemented in v1; schedule manually in Meta Business Suite",
        file=sys.stderr,
    )
    return 2


def _scheduled_date(value: str, zone: ZoneInfo) -> _dt.date | None:
    if not value:
        return None
    try:
        return _dt.datetime.fromisoformat(value).astimezone(zone).date()
    except ValueError:
        return None


def _coerce_choice(value: str, allowed: set[str], default: str) -> str:
    return value if value in allowed else default


def _count_yes(rows: Iterable[PostDraft], field: str) -> int:
    return sum(1 for row in rows if getattr(row, field) == "yes")
