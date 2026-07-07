"""U2 tests: provenance, --only-approved, suggested_pending."""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from fb_leads.draft_report import QUEUE_COLUMNS, generate_queue, sync_queue_csv
from fb_leads.drafts import PostDraft, load, save
from fb_leads.models import LeadCandidate, save as save_leads

REPO = Path(__file__).resolve().parents[1]


def _sample_drafts(leads: list[LeadCandidate], approved: list[int] | None = None) -> list[PostDraft]:
    approved = approved or []
    drafts = [
        PostDraft(
            topic="manual",
            title="Manual draft",
            copy_text="manual copy",
            scheduled_for="2026-07-04T02:30:00-05:00",
            timezone="America/Chicago",
            lead_ids=[],
            template_id="",
        ),
        PostDraft(
            topic="suggested-a",
            title="Suggested A",
            copy_text="suggested A copy",
            scheduled_for="2026-07-05T02:30:00-05:00",
            timezone="America/Chicago",
            lead_ids=[leads[0].id],
            template_id="coliving_room",
        ),
        PostDraft(
            topic="suggested-b",
            title="Suggested B",
            copy_text="suggested B copy",
            scheduled_for="2026-07-06T02:30:00-05:00",
            timezone="America/Chicago",
            lead_ids=[leads[1].id],
            template_id="room_listing",
        ),
    ]
    for index in approved:
        drafts[index].approved_by_human = "yes"
    return drafts


def _sample_leads() -> list[LeadCandidate]:
    return [
        LeadCandidate(
            title="Spacious coliving room",
            body_text="Rent by the room",
            tags=["coliving"],
            review_status="approved",
            capture_path="captures/fb/coliving.html",
        ),
        LeadCandidate(
            title="Furnished private room",
            body_text="Private room",
            price_text="$700/mo",
            location="Austin, TX",
            tags=["room_supply"],
            review_status="approved",
            capture_path="captures/fb/private.html",
        ),
    ]


def _save_pair(tmp_path: Path, approved: list[int] | None = None) -> tuple[Path, Path, list[LeadCandidate]]:
    leads = _sample_leads()
    leads_path = tmp_path / "leads.jsonl"
    drafts_path = tmp_path / "drafts.jsonl"
    save_leads(leads_path, leads)
    save(drafts_path, _sample_drafts(leads, approved=approved))
    return leads_path, drafts_path, leads


def test_generate_queue_adds_provenance_columns(tmp_path: Path):
    leads_path, drafts_path, leads = _save_pair(tmp_path)

    generate_queue(drafts_path, tmp_path, leads_path=leads_path)

    csv_path = tmp_path / "post_queue.csv"
    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert set(rows[0].keys()) == set(QUEUE_COLUMNS)
    assert "source_lead" in QUEUE_COLUMNS
    assert "template_id" in QUEUE_COLUMNS

    # Manual draft: blank source_lead + template_id.
    manual = next(row for row in rows if row["topic"] == "manual")
    assert manual["source_lead"] == ""
    assert manual["template_id"] == ""

    # Suggested drafts: lead_id + lead title in source_lead, template_id set.
    suggested = next(row for row in rows if row["topic"] == "suggested-a")
    assert leads[0].id in suggested["source_lead"]
    assert "Spacious coliving room" in suggested["source_lead"]
    assert suggested["template_id"] == "coliving_room"


def test_generate_queue_html_markdown_show_provenance(tmp_path: Path):
    leads_path, drafts_path, leads = _save_pair(tmp_path)

    generate_queue(drafts_path, tmp_path, leads_path=leads_path)

    html = (tmp_path / "post_queue.html").read_text(encoding="utf-8")
    md = (tmp_path / "post_queue.md").read_text(encoding="utf-8")
    assert "Source lead" in html
    assert "Template" in html
    assert leads[0].id in html
    assert "coliving_room" in html
    assert f"Source lead: {leads[0].id}" in md
    assert "Template: coliving_room" in md


def test_generate_queue_only_approved_filter(tmp_path: Path):
    leads_path, drafts_path, _ = _save_pair(tmp_path, approved=[1])

    summary = generate_queue(
        drafts_path, tmp_path, only_approved=True, leads_path=leads_path
    )
    assert summary["rows"] == 1
    assert summary["only_approved"] is True

    csv_path = tmp_path / "post_queue.csv"
    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [row["topic"] for row in rows] == ["suggested-a"]

    md = (tmp_path / "post_queue.md").read_text(encoding="utf-8")
    assert "1 drafts" in md


def test_generate_queue_without_leads_path_uses_blank_provenance(tmp_path: Path):
    leads_path, drafts_path, leads = _save_pair(tmp_path)

    summary = generate_queue(drafts_path, tmp_path)

    csv_path = tmp_path / "post_queue.csv"
    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    suggested = next(row for row in rows if row["topic"] == "suggested-a")
    assert suggested["source_lead"] == leads[0].id
    assert summary["leads_path"] == ""


def test_sync_after_provenance_columns_still_round_trips_only_checklist(tmp_path: Path):
    _, drafts_path, _ = _save_pair(tmp_path)
    generate_queue(drafts_path, tmp_path)
    csv_path = tmp_path / "post_queue.csv"

    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    rows[1]["approved_by_human"] = "yes"
    rows[1]["notes"] = "ready"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=QUEUE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    summary = sync_queue_csv(csv_path, drafts_path)
    assert summary["updated"] == 1
    updated = load(drafts_path)
    changed = next(draft for draft in updated.values() if draft.topic == "suggested-a")
    assert changed.approved_by_human == "yes"
    assert changed.notes == "ready"
    assert changed.copy_text == "suggested A copy"


def test_cli_draft_export_only_approved_filters_to_paste_ready(tmp_path: Path):
    leads_path, drafts_path, _ = _save_pair(tmp_path, approved=[2])

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fb_leads",
            "draft-export",
            "--drafts",
            str(drafts_path),
            "--leads",
            str(leads_path),
            "--out-dir",
            str(tmp_path),
            "--only-approved",
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=True,
    )

    summary = json.loads(result.stdout)
    assert summary["rows"] == 1
    assert summary["only_approved"] is True

    csv_path = tmp_path / "post_queue.csv"
    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [row["topic"] for row in rows] == ["suggested-b"]
