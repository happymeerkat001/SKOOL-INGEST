"""LeadCandidate model and JSONL store for Facebook lead triage."""
from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

SCHEMA_VERSION = 1

SOURCE_TYPES = {
    "marketplace_listing",
    "group_post",
    "page_post",
    "manual_note",
    "csv_import",
    "other",
}
EXTRACTION_STATUSES = {"ok", "partial", "failed"}
REVIEW_STATUSES = {"pending", "approved", "rejected"}
SCORE_BANDS = {"hot", "warm", "low", "unscored"}

TRACKING_QUERY_PARAMS = {
    "fbclid",
    "ref",
    "tracking",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "refsrc",
    "ref_src",
    "__tn__",
    "eid",
    "paipv",
}

FIELDS: tuple[str, ...] = (
    "id",
    "schema_version",
    "source_url",
    "source_type",
    "title",
    "body_text",
    "price_text",
    "price_value",
    "currency",
    "location",
    "posted_at",
    "seller_name",
    "images",
    "capture_time",
    "capture_path",
    "extraction",
    "score",
    "score_band",
    "score_reasons",
    "tags",
    "review_status",
    "review_notes",
    "updated_at",
)

_SCORE_AND_REVIEW_FIELDS = {
    "score",
    "score_band",
    "score_reasons",
    "tags",
    "review_status",
    "review_notes",
}


@dataclasses.dataclass
class LeadCandidate:
    source_url: str = ""
    source_type: str = "other"
    title: str = ""
    body_text: str = ""
    price_text: str = ""
    price_value: float | None = None
    currency: str = ""
    location: str = ""
    posted_at: str = ""
    seller_name: str = ""
    images: list[dict[str, str]] = dataclasses.field(default_factory=list)
    capture_time: str = ""
    capture_path: str = ""
    extraction: str = "partial"
    score: int = 0
    score_band: str = "unscored"
    score_reasons: list[str] = dataclasses.field(default_factory=list)
    tags: list[str] = dataclasses.field(default_factory=list)
    review_status: str = "pending"
    review_notes: str = ""
    updated_at: str = ""
    id: str = ""
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.source_url = normalize_url(self.source_url)
        self.source_type = _coerce_choice(self.source_type, SOURCE_TYPES, "other")
        self.extraction = _coerce_choice(self.extraction, EXTRACTION_STATUSES, "partial")
        self.review_status = _coerce_choice(self.review_status, REVIEW_STATUSES, "pending")
        self.score_band = _coerce_choice(self.score_band, SCORE_BANDS, "unscored")
        self.score = int(self.score or 0)
        self.images = _coerce_image_list(self.images)
        self.score_reasons = [str(item) for item in (self.score_reasons or [])]
        self.tags = [str(item) for item in (self.tags or [])]
        if not self.capture_time:
            self.capture_time = now_iso()
        if not self.updated_at:
            self.updated_at = now_iso()
        if not self.id:
            self.id = make_id(
                self.source_url,
                self.title,
                capture_path=self.capture_path,
                body_text=self.body_text,
            )

    def as_dict(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in FIELDS}


def now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat(timespec="seconds")


def normalize_url(url: str) -> str:
    """Normalize source URLs for stable IDs by stripping common tracking params."""
    if not url:
        return ""
    parsed = urlparse(url.strip())
    kept = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_PARAMS and not key.lower().startswith("utm_")
    ]
    query = urlencode(kept, doseq=True)
    path = re.sub(r"/+$", "", parsed.path) or parsed.path
    return urlunparse((parsed.scheme, parsed.netloc.lower(), path, "", query, ""))


def make_id(
    source_url: str,
    title: str,
    *,
    capture_path: str = "",
    body_text: str = "",
) -> str:
    """Stable sha256 prefix for a lead candidate."""
    h = hashlib.sha256()
    normalized_url = normalize_url(source_url)
    if normalized_url:
        h.update(normalized_url.encode("utf-8"))
        h.update(b"\x00")
        h.update(title.strip().encode("utf-8"))
    else:
        body_hash = hashlib.sha256(body_text.encode("utf-8")).hexdigest()
        h.update(capture_path.encode("utf-8"))
        h.update(b"\x00")
        h.update(body_hash.encode("ascii"))
    return h.hexdigest()[:16]


def load(path: Path) -> dict[str, LeadCandidate]:
    """Load a JSONL store into a dict keyed by lead id."""
    if not path.exists():
        return {}
    rows: dict[str, LeadCandidate] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            raw = json.loads(line)
            if not isinstance(raw, dict):
                continue
            known = {field: raw[field] for field in FIELDS if field in raw}
            lead = LeadCandidate(**known)
            rows[lead.id] = lead
    return rows


def save(path: Path, rows: Iterable[LeadCandidate]) -> None:
    """Write leads to JSONL, one object per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row.as_dict(), ensure_ascii=False, sort_keys=False) + "\n")


def merge(existing: LeadCandidate, incoming: LeadCandidate) -> LeadCandidate:
    """Merge a fresh extraction into an existing lead, preserving operator fields and scores."""
    data = incoming.as_dict()
    for field in _SCORE_AND_REVIEW_FIELDS:
        data[field] = getattr(existing, field)
    data["id"] = existing.id
    data["schema_version"] = max(existing.schema_version, incoming.schema_version)
    data["updated_at"] = now_iso()
    return LeadCandidate(**data)


def upsert(path: Path, incoming: LeadCandidate) -> dict[str, LeadCandidate]:
    """Load, merge or insert one lead, save, and return the full store."""
    rows = load(path)
    if incoming.id in rows:
        rows[incoming.id] = merge(rows[incoming.id], incoming)
    else:
        rows[incoming.id] = incoming
    save(path, rows.values())
    return rows


def _coerce_choice(value: str, allowed: set[str], default: str) -> str:
    return value if value in allowed else default


def _coerce_image_list(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            out.append({str(key): str(val) for key, val in item.items()})
    return out
