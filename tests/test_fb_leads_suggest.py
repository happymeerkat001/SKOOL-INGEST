"""Tests for fb_leads/suggest.py and the draft-suggest CLI."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from fb_leads.drafts import load as load_drafts
from fb_leads.models import LeadCandidate, save as save_leads
from fb_leads.suggest import (
    PLACEHOLDER,
    pick_template,
    render_copy,
    suggest_drafts,
    suggested_pending_count,
)

REPO = Path(__file__).resolve().parents[1]


def _approved_lead(**overrides) -> LeadCandidate:
    base = dict(
        title="Private room in shared house",
        body_text="Rent by the room near downtown.",
        price_text="$650/mo",
        location="Houston, TX",
        tags=["coliving"],
        review_status="approved",
        capture_path="captures/fb/listing.html",
    )
    base.update(overrides)
    # Force a unique capture_path so the lead id (which derives from
    # source_url + title + capture_path + body_text) is stable per fixture.
    overrides_path = overrides.get("capture_path")
    if overrides_path:
        base["capture_path"] = overrides_path
    elif "capture_path" not in overrides:
        base["capture_path"] = f"captures/fb/{base['title']}.html"
    return LeadCandidate(**base)


def _fixture_save(path: Path, leads: list[LeadCandidate]) -> None:
    """Save leads with guaranteed-unique capture_paths so each lead gets a distinct id."""
    seen_paths: set[str] = set()
    unique: list[LeadCandidate] = []
    for index, lead in enumerate(leads):
        cp = lead.capture_path or f"captures/fb/lead-{index}.html"
        suffix = 0
        candidate = cp
        while candidate in seen_paths:
            suffix += 1
            candidate = f"{cp}#{suffix}"
        seen_paths.add(candidate)
        if candidate != lead.capture_path:
            lead = LeadCandidate(
                **{**lead.__dict__, "capture_path": candidate}
            )
        unique.append(lead)
    save_leads(path, unique)


def test_pick_template_prefers_coliving_then_supply_owner_partnership():
    assert pick_template(["coliving"]) == "coliving_room"
    assert pick_template(["room_supply"]) == "room_listing"
    assert pick_template(["owner"]) == "room_listing"
    assert pick_template(["partnership"]) == "room_listing"
    assert pick_template(["distressed"]) is None
    assert pick_template(["coliving", "owner"]) == "coliving_room"
    assert pick_template([]) is None


def test_render_copy_substitutes_placeholders_for_missing_slots():
    out = render_copy("room_listing", {"location": "", "price": "$700", "room_desc": "Private room", "move_in": ""})

    assert "Private room" in out
    assert "$700" in out
    assert PLACEHOLDER.format(name="location") in out
    assert PLACEHOLDER.format(name="move_in") in out


def test_suggest_drafts_creates_one_draft_per_approved_lead(tmp_path: Path):
    leads_path = tmp_path / "leads.jsonl"
    drafts_path = tmp_path / "drafts.jsonl"
    _fixture_save(
        leads_path,
        [
            _approved_lead(capture_path="captures/fb/listing-a.html"),
            _approved_lead(
                title="Furnished room",
                tags=["room_supply"],
                location="Austin, TX",
                capture_path="captures/fb/listing-b.html",
            ),
        ],
    )

    summary = suggest_drafts(leads_path, drafts_path, tz_name="America/Chicago")

    assert summary["suggested_count"] == 2
    assert summary["skipped_unapproved"] == 0
    assert summary["skipped_existing"] == 0
    assert summary["skipped_no_tag"] == 0

    rows = load_drafts(drafts_path)
    assert len(rows) == 2
    for draft in rows.values():
        assert draft.approved_by_human == "no"
        assert draft.lead_ids and draft.lead_ids[0]
        assert draft.template_id in {"coliving_room", "room_listing"}
        assert draft.scheduled_for
        assert draft.timezone == "America/Chicago"


def test_suggest_drafts_idempotent_second_run_creates_zero(tmp_path: Path):
    leads_path = tmp_path / "leads.jsonl"
    drafts_path = tmp_path / "drafts.jsonl"
    save_leads(leads_path, [_approved_lead()])

    first = suggest_drafts(leads_path, drafts_path, tz_name="America/Chicago")
    second = suggest_drafts(leads_path, drafts_path, tz_name="America/Chicago")

    assert first["suggested_count"] == 1
    assert second["suggested_count"] == 0
    assert second["skipped_existing"] == 1
    assert len(load_drafts(drafts_path)) == 1


def test_suggest_drafts_skips_pending_and_rejected_leads(tmp_path: Path):
    leads_path = tmp_path / "leads.jsonl"
    drafts_path = tmp_path / "drafts.jsonl"
    _fixture_save(
        leads_path,
        [
            _approved_lead(review_status="pending", capture_path="captures/fb/pending.html"),
            _approved_lead(review_status="rejected", capture_path="captures/fb/rejected.html"),
            _approved_lead(capture_path="captures/fb/approved.html"),
        ],
    )

    summary = suggest_drafts(leads_path, drafts_path, tz_name="America/Chicago")

    assert summary["suggested_count"] == 1
    assert summary["skipped_unapproved"] == 2


def test_suggest_drafts_skips_leads_without_mapped_tag(tmp_path: Path):
    leads_path = tmp_path / "leads.jsonl"
    drafts_path = tmp_path / "drafts.jsonl"
    _fixture_save(
        leads_path,
        [
            _approved_lead(tags=["distressed"], capture_path="captures/fb/distressed.html"),
            _approved_lead(tags=[], capture_path="captures/fb/untagged.html"),
            _approved_lead(tags=["coliving"], capture_path="captures/fb/coliving.html"),
        ],
    )

    summary = suggest_drafts(leads_path, drafts_path, tz_name="America/Chicago")

    assert summary["suggested_count"] == 1
    assert summary["skipped_no_tag"] == 2


def test_suggest_drafts_empty_store_is_zero_summary(tmp_path: Path):
    leads_path = tmp_path / "leads.jsonl"
    drafts_path = tmp_path / "drafts.jsonl"

    summary = suggest_drafts(leads_path, drafts_path, tz_name="America/Chicago")

    assert summary["suggested_count"] == 0
    assert summary["skipped_unapproved"] == 0
    assert not drafts_path.exists()


def test_two_suggestions_get_consecutive_two_thirty_am_slots(tmp_path: Path):
    leads_path = tmp_path / "leads.jsonl"
    drafts_path = tmp_path / "drafts.jsonl"
    _fixture_save(
        leads_path,
        [
            _approved_lead(capture_path="captures/fb/two-a.html"),
            _approved_lead(
                title="Second room",
                tags=["room_supply"],
                location="Austin, TX",
                capture_path="captures/fb/two-b.html",
            ),
        ],
    )

    from fb_leads import suggest as suggest_mod
    real_next_slot = suggest_mod.next_slot

    def _stub_next_slot(existing, tz_name, now=None):
        # Always return a fixed slot on the first call, then bump to the
        # following day so the second suggestion picks the next free slot.
        if not existing:
            return "2026-07-03T02:30:00-05:00"
        return "2026-07-04T02:30:00-05:00"

    suggest_mod.next_slot = _stub_next_slot  # type: ignore[assignment]
    try:
        summary = suggest_drafts(leads_path, drafts_path, tz_name="America/Chicago")
    finally:
        suggest_mod.next_slot = real_next_slot  # type: ignore[assignment]

    assert summary["suggested_count"] == 2
    rows = sorted(load_drafts(drafts_path).values(), key=lambda d: d.scheduled_for)
    assert rows[0].scheduled_for == "2026-07-03T02:30:00-05:00"
    assert rows[1].scheduled_for == "2026-07-04T02:30:00-05:00"


def test_suggested_pending_count_only_counts_suggested_unapproved(tmp_path: Path):
    drafts_path = tmp_path / "drafts.jsonl"
    from fb_leads.drafts import PostDraft, save

    save(
        drafts_path,
        [
            PostDraft(topic="suggested", copy_text="x", lead_ids=["lead-1"], approved_by_human="no"),
            PostDraft(topic="manual", copy_text="y", lead_ids=[], approved_by_human="yes"),
            PostDraft(topic="approved-suggested", copy_text="z", lead_ids=["lead-2"], approved_by_human="yes"),
        ],
    )

    assert suggested_pending_count(drafts_path) == 1


def test_cli_draft_suggest_creates_drafts_from_approved_leads(tmp_path: Path):
    leads_path = tmp_path / "leads.jsonl"
    drafts_path = tmp_path / "drafts.jsonl"
    save_leads(leads_path, [_approved_lead()])

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fb_leads",
            "draft-suggest",
            "--leads",
            str(leads_path),
            "--drafts",
            str(drafts_path),
            "--tz",
            "America/Chicago",
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["suggested_count"] == 1
    rows = load_drafts(drafts_path)
    assert len(rows) == 1


def test_cli_draft_list_reports_suggested_pending(tmp_path: Path):
    drafts_path = tmp_path / "drafts.jsonl"
    leads_path = tmp_path / "leads.jsonl"
    save_leads(leads_path, [_approved_lead()])

    # First create one suggested draft via the CLI.
    subprocess.run(
        [
            sys.executable,
            "-m",
            "fb_leads",
            "draft-suggest",
            "--leads",
            str(leads_path),
            "--drafts",
            str(drafts_path),
            "--tz",
            "America/Chicago",
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=True,
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fb_leads",
            "draft-list",
            "--drafts",
            str(drafts_path),
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=True,
    )

    summary = json.loads(result.stdout)
    assert summary["suggested_pending"] == 1
