"""Tests for Facebook posting draft model, store, scheduling, and CLI."""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fb_leads.drafts import PostDraft, load, merge, next_slot, save, upsert
from fb_leads.models import LeadCandidate, save as save_leads
from fb_leads.post_templates import render

REPO = Path(__file__).resolve().parents[1]


def test_post_draft_jsonl_round_trip_preserves_fields(tmp_path: Path):
    path = tmp_path / "post_drafts.jsonl"
    draft = PostDraft(
        topic="room-303",
        lead_ids=["lead1", "lead2"],
        template_id="room_listing",
        title="Room 303",
        copy_text="Paste this exact post copy.",
        price_text="$650/mo",
        location="Houston, TX",
        images_note="Attach room and kitchen photos",
        target_surface="marketplace",
        scheduled_for="2026-07-04T02:30:00-05:00",
        timezone="America/Chicago",
        approved_by_human="yes",
        scheduled_in_meta_business_suite="no",
        posted_at="2026-07-04T09:00:00-05:00",
        notes="posted manually",
    )

    save(path, [draft])
    loaded = load(path)

    assert list(loaded) == [draft.id]
    got = loaded[draft.id]
    assert got.lead_ids == ["lead1", "lead2"]
    assert got.copy_text == "Paste this exact post copy."
    assert got.approved_by_human == "yes"
    assert got.posted_at == "2026-07-04T09:00:00-05:00"


def test_merge_refreshes_copy_but_preserves_checklist_fields():
    existing = PostDraft(
        topic="room-303",
        title="Old",
        copy_text="old copy",
        scheduled_for="2026-07-04T02:30:00-05:00",
        timezone="America/Chicago",
        approved_by_human="yes",
        scheduled_in_meta_business_suite="yes",
        posted_at="2026-07-04T10:00:00-05:00",
        notes="already scheduled",
    )
    incoming = PostDraft(
        topic="room-303",
        title="New",
        copy_text="new copy",
        scheduled_for="2026-07-05T02:30:00-05:00",
        timezone="America/Chicago",
        id=existing.id,
    )

    merged = merge(existing, incoming)

    assert merged.title == "New"
    assert merged.copy_text == "new copy"
    assert merged.scheduled_for == "2026-07-05T02:30:00-05:00"
    assert merged.approved_by_human == "yes"
    assert merged.scheduled_in_meta_business_suite == "yes"
    assert merged.posted_at == "2026-07-04T10:00:00-05:00"
    assert merged.notes == "already scheduled"


def test_next_slot_before_230_uses_today_and_after_230_uses_tomorrow():
    tz = "America/Chicago"
    before = datetime(2026, 7, 3, 1, 0, tzinfo=ZoneInfo(tz))
    after = datetime(2026, 7, 3, 3, 0, tzinfo=ZoneInfo(tz))

    assert next_slot([], tz, before) == "2026-07-03T02:30:00-05:00"
    assert next_slot([], tz, after) == "2026-07-04T02:30:00-05:00"


def test_next_slot_auto_spaces_across_successive_nights():
    tz = "America/Chicago"
    now = datetime(2026, 7, 3, 1, 0, tzinfo=ZoneInfo(tz))
    first = next_slot([], tz, now)
    existing = [PostDraft(topic="a", copy_text="a", scheduled_for=first, timezone=tz)]

    second = next_slot(existing, tz, now)

    assert first == "2026-07-03T02:30:00-05:00"
    assert second == "2026-07-04T02:30:00-05:00"


def test_explicit_datetime_upsert_can_share_an_existing_date(tmp_path: Path):
    path = tmp_path / "post_drafts.jsonl"
    first = PostDraft(topic="a", copy_text="a", scheduled_for="2026-07-03T02:30:00-05:00")
    second = PostDraft(topic="b", copy_text="b", scheduled_for="2026-07-03T04:00:00-05:00")

    upsert(path, first)
    rows = upsert(path, second)

    assert len(rows) == 2
    assert {row.scheduled_for for row in rows.values()} == {
        "2026-07-03T02:30:00-05:00",
        "2026-07-03T04:00:00-05:00",
    }


def test_invalid_timezone_fails_clearly_in_cli(tmp_path: Path):
    drafts_path = tmp_path / "post_drafts.jsonl"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fb_leads",
            "draft-add",
            "--topic",
            "bad-zone",
            "--copy",
            "hello",
            "--tz",
            "Mars/Olympus",
            "--drafts",
            str(drafts_path),
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "invalid timezone" in result.stderr.lower()
    assert not drafts_path.exists()


def test_dst_spring_forward_slot_is_deterministic():
    tz = "America/New_York"
    now = datetime(2026, 3, 8, 1, 0, tzinfo=ZoneInfo(tz))

    slot = next_slot([], tz, now)

    assert slot.startswith("2026-03-08T02:30:00")


def test_repeated_save_is_diff_stable(tmp_path: Path):
    path = tmp_path / "post_drafts.jsonl"
    draft = PostDraft(topic="stable", copy_text="same", scheduled_for="2026-07-04T02:30:00-05:00")

    save(path, [draft])
    first = path.read_text(encoding="utf-8")
    save(path, load(path).values())
    second = path.read_text(encoding="utf-8")

    assert second == first


def test_template_render_substitutes_all_slots():
    copy = render(
        "room_listing",
        {
            "location": "Houston, TX",
            "price": "$650/mo",
            "room_desc": "furnished private room",
            "move_in": "August 1",
        },
    )

    assert "Houston, TX" in copy
    assert "$650/mo" in copy
    assert "furnished private room" in copy
    assert "August 1" in copy


def test_draft_add_template_stores_rendered_copy_exactly(tmp_path: Path):
    drafts_path = tmp_path / "post_drafts.jsonl"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fb_leads",
            "draft-add",
            "--topic",
            "room-303",
            "--template",
            "room_listing",
            "--price",
            "$650/mo",
            "--location",
            "Houston, TX",
            "--room-desc",
            "furnished private room",
            "--move-in",
            "August 1",
            "--surface",
            "marketplace",
            "--tz",
            "America/Chicago",
            "--drafts",
            str(drafts_path),
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    draft = next(iter(load(drafts_path).values()))
    assert draft.template_id == "room_listing"
    assert draft.copy_text == render(
        "room_listing",
        {
            "location": "Houston, TX",
            "price": "$650/mo",
            "room_desc": "furnished private room",
            "move_in": "August 1",
        },
    )
    assert draft.target_surface == "marketplace"


def test_draft_add_missing_template_slot_fails_without_writing(tmp_path: Path):
    drafts_path = tmp_path / "post_drafts.jsonl"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fb_leads",
            "draft-add",
            "--topic",
            "missing-slot",
            "--template",
            "room_listing",
            "--price",
            "$650/mo",
            "--location",
            "Houston, TX",
            "--drafts",
            str(drafts_path),
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "missing template slot" in result.stderr.lower()
    assert not drafts_path.exists()


def test_draft_add_copy_file_stores_verbatim_copy(tmp_path: Path):
    drafts_path = tmp_path / "post_drafts.jsonl"
    copy_path = tmp_path / "draft.txt"
    copy_path.write_text("Line one\nLine two\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fb_leads",
            "draft-add",
            "--topic",
            "handwritten",
            "--copy-file",
            str(copy_path),
            "--drafts",
            str(drafts_path),
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    draft = next(iter(load(drafts_path).values()))
    assert draft.copy_text == "Line one\nLine two\n"
    assert draft.template_id == ""


def test_draft_add_from_approved_lead_records_reference_and_prefills_slots(tmp_path: Path):
    drafts_path = tmp_path / "post_drafts.jsonl"
    leads_path = tmp_path / "leads.jsonl"
    lead = LeadCandidate(
        title="Approved lead",
        body_text="Private room",
        price_text="$700/mo",
        location="Dallas, TX",
        review_status="approved",
        capture_path="captures/fb/lead.html",
    )
    save_leads(leads_path, [lead])

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fb_leads",
            "draft-add",
            "--topic",
            "lead-context",
            "--template",
            "room_listing",
            "--from-lead",
            lead.id,
            "--leads",
            str(leads_path),
            "--room-desc",
            "private room",
            "--move-in",
            "soon",
            "--drafts",
            str(drafts_path),
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    draft = next(iter(load(drafts_path).values()))
    assert draft.lead_ids == [lead.id]
    assert "$700/mo" in draft.copy_text
    assert "Dallas, TX" in draft.copy_text


def test_draft_add_from_pending_lead_refuses_unless_overridden(tmp_path: Path):
    drafts_path = tmp_path / "post_drafts.jsonl"
    leads_path = tmp_path / "leads.jsonl"
    lead = LeadCandidate(
        title="Pending lead",
        body_text="Private room",
        price_text="$700/mo",
        location="Dallas, TX",
        review_status="pending",
        capture_path="captures/fb/lead.html",
    )
    save_leads(leads_path, [lead])
    base = [
        sys.executable,
        "-m",
        "fb_leads",
        "draft-add",
        "--topic",
        "lead-context",
        "--copy",
        "manual copy",
        "--from-lead",
        lead.id,
        "--leads",
        str(leads_path),
        "--drafts",
        str(drafts_path),
    ]

    refused = subprocess.run(base, cwd=REPO, text=True, capture_output=True, check=False)
    allowed = subprocess.run(
        [*base, "--allow-unapproved"], cwd=REPO, text=True, capture_output=True, check=False
    )

    assert refused.returncode == 2
    assert "not approved" in refused.stderr.lower()
    assert allowed.returncode == 0
    assert next(iter(load(drafts_path).values())).lead_ids == [lead.id]


def test_draft_add_unknown_lead_fails(tmp_path: Path):
    leads_path = tmp_path / "leads.jsonl"
    drafts_path = tmp_path / "post_drafts.jsonl"
    save_leads(leads_path, [])

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fb_leads",
            "draft-add",
            "--topic",
            "unknown",
            "--copy",
            "manual copy",
            "--from-lead",
            "missing",
            "--leads",
            str(leads_path),
            "--drafts",
            str(drafts_path),
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "unknown lead id" in result.stderr.lower()


def test_draft_list_empty_store_returns_zero_summary(tmp_path: Path):
    drafts_path = tmp_path / "post_drafts.jsonl"

    result = subprocess.run(
        [sys.executable, "-m", "fb_leads", "draft-list", "--drafts", str(drafts_path)],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert '"total": 0' in result.stdout


def test_draft_publish_refuses_and_leaves_store_untouched(tmp_path: Path):
    drafts_path = tmp_path / "post_drafts.jsonl"
    save(drafts_path, [PostDraft(topic="no-post", copy_text="manual only")])
    before = drafts_path.read_text(encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "fb_leads", "draft-publish", "--drafts", str(drafts_path)],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "schedule manually in Meta Business Suite" in result.stderr
    assert drafts_path.read_text(encoding="utf-8") == before


def test_draft_publish_refuses_even_with_future_env_gate(tmp_path: Path, monkeypatch):
    drafts_path = tmp_path / "post_drafts.jsonl"
    save(drafts_path, [PostDraft(topic="no-post", copy_text="manual only")])
    monkeypatch.setenv("FB_LEADS_ENABLE_META_API", "1")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fb_leads",
            "draft-publish",
            "--drafts",
            str(drafts_path),
            "--i-understand-official-api",
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "not implemented in v1" in result.stderr


def test_new_draft_modules_have_no_network_imports():
    for rel in ["fb_leads/drafts.py", "fb_leads/post_templates.py", "fb_leads/draft_report.py"]:
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "import requests" not in text
        assert "from requests" not in text
        assert "import httpx" not in text
        assert "from httpx" not in text
        assert "urllib.request" not in text
