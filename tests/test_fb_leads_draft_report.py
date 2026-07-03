"""Tests for posting draft queue exports and checklist sync."""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from fb_leads.draft_report import QUEUE_COLUMNS, generate_queue, sync_queue_csv
from fb_leads.drafts import PostDraft, load, save

REPO = Path(__file__).resolve().parents[1]


def _sample_drafts() -> list[PostDraft]:
    return [
        PostDraft(
            topic="later",
            title="Later Slot",
            copy_text="Later copy",
            scheduled_for="2026-07-05T02:30:00-05:00",
            timezone="America/Chicago",
            target_surface="group",
        ),
        PostDraft(
            topic="first",
            title="First Slot",
            copy_text="First copy with <script>alert(1)</script>",
            price_text="$650/mo",
            location="Houston, TX",
            images_note="Attach room photo",
            scheduled_for="2026-07-04T02:30:00-05:00",
            timezone="America/Chicago",
            lead_ids=["lead1"],
        ),
        PostDraft(
            topic="third",
            title="Third Slot",
            copy_text="Copy with markdown fence marker ``` keep intact",
            scheduled_for="2026-07-06T02:30:00-05:00",
            timezone="America/Chicago",
        ),
    ]


def test_generate_queue_writes_csv_html_markdown_sorted_by_slot(tmp_path: Path):
    drafts_path = tmp_path / "post_drafts.jsonl"
    out_dir = tmp_path / "out"
    save(drafts_path, _sample_drafts())

    summary = generate_queue(drafts_path, out_dir)

    assert summary["rows"] == 3
    csv_path = out_dir / "post_queue.csv"
    html_path = out_dir / "post_queue.html"
    md_path = out_dir / "post_queue.md"
    assert csv_path.exists()
    assert html_path.exists()
    assert md_path.exists()

    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0].keys() == set(QUEUE_COLUMNS)
    assert [row["topic"] for row in rows] == ["first", "later", "third"]
    assert rows[0]["copy_text"] == "First copy with <script>alert(1)</script>"

    html = html_path.read_text(encoding="utf-8")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "<pre>" in html

    md = md_path.read_text(encoding="utf-8")
    assert "First copy with <script>alert(1)</script>" in md
    assert "Copy with markdown fence marker ``` keep intact" in md


def test_sync_queue_csv_updates_only_checklist_fields(tmp_path: Path):
    drafts_path = tmp_path / "post_drafts.jsonl"
    out_dir = tmp_path / "out"
    save(drafts_path, _sample_drafts())
    generate_queue(drafts_path, out_dir)
    csv_path = out_dir / "post_queue.csv"

    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    rows[0]["approved_by_human"] = "yes"
    rows[0]["scheduled_in_meta_business_suite"] = "yes"
    rows[0]["posted_at"] = "2026-07-04T09:00:00-05:00"
    rows[0]["notes"] = "scheduled manually"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=QUEUE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    summary = sync_queue_csv(csv_path, drafts_path)
    updated = load(drafts_path)
    changed = updated[rows[0]["id"]]
    untouched = updated[rows[1]["id"]]

    assert summary["updated"] == 1
    assert changed.approved_by_human == "yes"
    assert changed.scheduled_in_meta_business_suite == "yes"
    assert changed.posted_at == "2026-07-04T09:00:00-05:00"
    assert changed.notes == "scheduled manually"
    assert changed.copy_text == "First copy with <script>alert(1)</script>"
    assert untouched.approved_by_human == "no"


def test_sync_queue_csv_rejects_invalid_yes_no_and_preserves_store(tmp_path: Path, capsys):
    drafts_path = tmp_path / "post_drafts.jsonl"
    out_dir = tmp_path / "out"
    save(drafts_path, _sample_drafts())
    before = drafts_path.read_text(encoding="utf-8")
    generate_queue(drafts_path, out_dir)
    csv_path = out_dir / "post_queue.csv"

    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    rows[0]["approved_by_human"] = "maybe"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=QUEUE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    summary = sync_queue_csv(csv_path, drafts_path)
    captured = capsys.readouterr()

    assert summary["invalid"] == 1
    assert "invalid approved_by_human" in captured.err
    assert drafts_path.read_text(encoding="utf-8") == before


def test_sync_queue_csv_rejects_malformed_posted_at(tmp_path: Path, capsys):
    drafts_path = tmp_path / "post_drafts.jsonl"
    out_dir = tmp_path / "out"
    save(drafts_path, _sample_drafts())
    generate_queue(drafts_path, out_dir)
    csv_path = out_dir / "post_queue.csv"

    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    rows[0]["posted_at"] = "not a date"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=QUEUE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    summary = sync_queue_csv(csv_path, drafts_path)
    captured = capsys.readouterr()

    assert summary["invalid"] == 1
    assert "invalid posted_at" in captured.err


def test_sync_queue_csv_skips_unknown_id(tmp_path: Path, capsys):
    drafts_path = tmp_path / "post_drafts.jsonl"
    out_dir = tmp_path / "out"
    save(drafts_path, _sample_drafts())
    generate_queue(drafts_path, out_dir)
    csv_path = out_dir / "post_queue.csv"

    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    rows[0]["id"] = "unknown"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=QUEUE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    summary = sync_queue_csv(csv_path, drafts_path)
    captured = capsys.readouterr()

    assert summary["unknown"] == 1
    assert "unknown draft id" in captured.err


def test_re_export_after_sync_shows_checklist_values(tmp_path: Path):
    drafts_path = tmp_path / "post_drafts.jsonl"
    out_dir = tmp_path / "out"
    save(drafts_path, _sample_drafts())
    generate_queue(drafts_path, out_dir)
    csv_path = out_dir / "post_queue.csv"

    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    rows[0]["approved_by_human"] = "yes"
    rows[0]["notes"] = "ready"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=QUEUE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    sync_queue_csv(csv_path, drafts_path)
    generate_queue(drafts_path, out_dir)

    html = (out_dir / "post_queue.html").read_text(encoding="utf-8")
    md = (out_dir / "post_queue.md").read_text(encoding="utf-8")
    assert "approved: yes" in html
    assert "Notes: ready" in md


def test_cli_draft_export_and_sync(tmp_path: Path):
    drafts_path = tmp_path / "post_drafts.jsonl"
    out_dir = tmp_path / "out"
    save(drafts_path, _sample_drafts())

    exported = subprocess.run(
        [
            sys.executable,
            "-m",
            "fb_leads",
            "draft-export",
            "--drafts",
            str(drafts_path),
            "--out-dir",
            str(out_dir),
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    assert exported.returncode == 0
    assert json.loads(exported.stdout)["rows"] == 3

    csv_path = out_dir / "post_queue.csv"
    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    rows[0]["approved_by_human"] = "yes"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=QUEUE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    synced = subprocess.run(
        [
            sys.executable,
            "-m",
            "fb_leads",
            "draft-sync",
            "--csv",
            str(csv_path),
            "--drafts",
            str(drafts_path),
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    assert synced.returncode == 0
    assert json.loads(synced.stdout)["updated"] == 1
