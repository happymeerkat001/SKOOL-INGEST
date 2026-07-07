"""Suggest posting drafts from approved leads.

Pure, no I/O outside the caller-supplied stores. Idempotent: re-running with
the same leads/drafts store never creates duplicate drafts (deduped via the
existing lead_ids linkage on drafts).
"""
from __future__ import annotations

from typing import Any

from .drafts import PostDraft, default_timezone_name, load as load_drafts, next_slot, upsert
from .models import LeadCandidate, load as load_leads
from .post_templates import TEMPLATES, render

TAG_TEMPLATE_MAP: tuple[tuple[str, str], ...] = (
    ("coliving", "coliving_room"),
    ("room_supply", "room_listing"),
    ("owner", "room_listing"),
    ("partnership", "room_listing"),
)

DEFAULT_TARGET_SURFACE = "marketplace"
PLACEHOLDER = "[FILL: {name}]"

REQUIRED_SLOTS: tuple[str, ...] = ("location", "price", "room_desc", "move_in")


def pick_template(tags: list[str]) -> str | None:
    seen = {tag for tag in (tags or [])}
    for tag, template_id in TAG_TEMPLATE_MAP:
        if tag in seen:
            return template_id
    return None


def _placeholder(name: str) -> str:
    return PLACEHOLDER.format(name=name)


def _slots_for_lead(lead: LeadCandidate) -> dict[str, str]:
    raw = {
        "location": lead.location,
        "price": lead.price_text,
        "room_desc": lead.title,
        "move_in": "",
    }
    return {name: (value.strip() if isinstance(value, str) else "") for name, value in raw.items()}


def render_copy(template_id: str, slots: dict[str, str]) -> str:
    if template_id not in TEMPLATES:
        raise KeyError(f"unknown template: {template_id}")
    rendered_slots: dict[str, str] = {}
    for name in REQUIRED_SLOTS:
        value = slots.get(name, "") or ""
        rendered_slots[name] = value if value else _placeholder(name)
    try:
        return render(template_id, rendered_slots)
    except KeyError as exc:
        missing = exc.args[0]
        rendered_slots[missing] = _placeholder(missing)
        return render(template_id, rendered_slots)


def _linked_lead_ids(drafts_rows: dict[str, PostDraft]) -> set[str]:
    linked: set[str] = set()
    for draft in drafts_rows.values():
        linked.update(draft.lead_ids or [])
    return linked


def _topic_for_lead(lead: LeadCandidate) -> str:
    title = (lead.title or "").strip() or lead.id or "lead"
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in title).strip("-")
    return (slug or "lead")[:60]


def suggest_drafts(leads_path, drafts_path, tz_name: str | None = None) -> dict[str, Any]:
    leads = load_leads(leads_path)
    drafts = load_drafts(drafts_path)
    zone = tz_name or default_timezone_name()
    linked = _linked_lead_ids(drafts)

    suggested: list[str] = []
    skipped_unapproved = 0
    skipped_existing = 0
    skipped_no_tag = 0

    for lead in leads.values():
        if lead.review_status != "approved":
            skipped_unapproved += 1
            continue
        if lead.id in linked:
            skipped_existing += 1
            continue
        template_id = pick_template(lead.tags)
        if template_id is None:
            skipped_no_tag += 1
            continue
        slots = _slots_for_lead(lead)
        copy_text = render_copy(template_id, slots)
        existing_for_slot = drafts.values()
        scheduled_for = next_slot(existing_for_slot, zone)
        draft = PostDraft(
            topic=_topic_for_lead(lead),
            lead_ids=[lead.id],
            template_id=template_id,
            title=lead.title or _topic_for_lead(lead),
            copy_text=copy_text,
            price_text=lead.price_text,
            location=lead.location,
            target_surface=DEFAULT_TARGET_SURFACE,
            scheduled_for=scheduled_for,
            timezone=zone,
            approved_by_human="no",
        )
        drafts = upsert(drafts_path, draft)
        suggested.append(draft.id)

    return {
        "suggested": suggested,
        "suggested_count": len(suggested),
        "skipped_unapproved": skipped_unapproved,
        "skipped_existing": skipped_existing,
        "skipped_no_tag": skipped_no_tag,
        "leads_path": str(leads_path),
        "drafts_path": str(drafts_path),
    }


def suggested_pending_count(drafts_path) -> int:
    rows = load_drafts(drafts_path)
    return sum(
        1
        for draft in rows.values()
        if draft.lead_ids and draft.approved_by_human != "yes"
    )


__all__ = [
    "DEFAULT_TARGET_SURFACE",
    "PLACEHOLDER",
    "REQUIRED_SLOTS",
    "TAG_TEMPLATE_MAP",
    "pick_template",
    "render_copy",
    "suggest_drafts",
    "suggested_pending_count",
]
