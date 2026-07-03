"""Tests for Facebook lead review reports and CSV sync."""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from fb_leads.models import LeadCandidate, load, save
from fb_leads.report import REVIEW_COLUMNS, generate_report, sync_review_csv

REPO = Path(__file__).resolve().parents[1]


def _sample_leads() -> list[LeadCandidate]:
    return [
        LeadCandidate(
            title="Warm private room",
            body_text="Private room available",
            score=4,
            score_band="warm",
            tags=["room_supply"],
            score_reasons=["matched 'private room' (+4, room_supply)"],
            source_type="marketplace_listing",
            source_url="https://facebook.com/marketplace/item/1",
            price_text="$650/mo",
            location="Houston, TX",
            posted_at="2026-07-01",
            seller_name="Seller One",
            capture_path="captures/fb/warm.html",
        ),
        LeadCandidate(
            title="Hot landlord lead",
            body_text="Landlord vacant house",
            score=9,
            score_band="hot",
            tags=["owner", "distressed"],
            score_reasons=["matched 'landlord' (+3, owner)", "matched 'vacant' (+3, distressed)"],
            source_type="group_post",
            source_url="https://facebook.com/groups/x/posts/2",
            location="Austin, TX",
            seller_name="Seller Two",
            capture_path="captures/fb/hot.html",
        ),
        LeadCandidate(
            title="Low demand <script>alert(1)</script>",
            body_text="Looking for a room <script>bad()</script>",
            score=-5,
            score_band="low",
            tags=["demand"],
            score_reasons=["matched 'looking for a room' (-5, demand)"],
            source_type="group_post",
            capture_path="captures/fb/low.html",
        ),
    ]


def test_generate_report_writes_csv_columns_sorted_by_score_and_html(tmp_path: Path):
    leads_path = tmp_path / "leads.jsonl"
    out_dir = tmp_path / "out"
    save(leads_path, _sample_leads())

    summary = generate_report(leads_path, out_dir)

    assert summary["rows"] == 3
    csv_path = out_dir / "review_queue.csv"
    html_path = out_dir / "review.html"
    assert csv_path.exists()
    assert html_path.exists()

    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0].keys() == set(REVIEW_COLUMNS)
    assert [row["title"] for row in rows] == [
        "Hot landlord lead",
        "Warm private room",
        "Low demand <script>alert(1)</script>",
    ]
    assert rows[0]["tags"] == "owner,distressed"
    assert "matched 'landlord'" in rows[0]["score_reasons"]

    html = html_path.read_text(encoding="utf-8")
    assert "Hot landlord lead" in html
    assert "captures/fb/hot.html" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "<script>bad()</script>" not in html


def test_sync_review_csv_updates_only_review_fields(tmp_path: Path):
    leads_path = tmp_path / "leads.jsonl"
    out_dir = tmp_path / "out"
    leads = _sample_leads()
    save(leads_path, leads)
    generate_report(leads_path, out_dir)

    csv_path = out_dir / "review_queue.csv"
    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    rows[0]["review_status"] = "approved"
    rows[0]["review_notes"] = "call tomorrow"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    summary = sync_review_csv(csv_path, leads_path)
    updated = load(leads_path)
    changed = updated[rows[0]["id"]]
    untouched = updated[rows[1]["id"]]

    assert summary["updated"] == 1
    assert summary["invalid"] == 0
    assert summary["unknown"] == 0
    assert changed.review_status == "approved"
    assert changed.review_notes == "call tomorrow"
    assert changed.title == "Hot landlord lead"
    assert untouched.review_status == "pending"


def test_sync_review_csv_rejects_invalid_status_and_preserves_store(tmp_path: Path, capsys):
    leads_path = tmp_path / "leads.jsonl"
    out_dir = tmp_path / "out"
    save(leads_path, _sample_leads())
    before = leads_path.read_text(encoding="utf-8")
    generate_report(leads_path, out_dir)

    csv_path = out_dir / "review_queue.csv"
    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    rows[0]["review_status"] = "maybe"
    rows[0]["review_notes"] = "bad status"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    summary = sync_review_csv(csv_path, leads_path)
    captured = capsys.readouterr()

    assert summary["invalid"] == 1
    assert summary["updated"] == 0
    assert "invalid review_status" in captured.err
    assert leads_path.read_text(encoding="utf-8") == before


def test_sync_review_csv_warns_and_skips_unknown_id(tmp_path: Path, capsys):
    leads_path = tmp_path / "leads.jsonl"
    out_dir = tmp_path / "out"
    save(leads_path, _sample_leads())
    generate_report(leads_path, out_dir)

    csv_path = out_dir / "review_queue.csv"
    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    rows[0]["id"] = "unknown-id"
    rows[0]["review_status"] = "approved"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    summary = sync_review_csv(csv_path, leads_path)
    captured = capsys.readouterr()

    assert summary["unknown"] == 1
    assert summary["updated"] == 0
    assert "unknown lead id" in captured.err


def test_cli_report_and_sync(tmp_path: Path):
    leads_path = tmp_path / "leads.jsonl"
    out_dir = tmp_path / "report"
    save(leads_path, _sample_leads())

    report_result = subprocess.run(
        [sys.executable, "-m", "fb_leads", "report", "--leads", str(leads_path), "--out-dir", str(out_dir)],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    assert report_result.returncode == 0
    report_summary = json.loads(report_result.stdout)
    assert report_summary["rows"] == 3

    csv_path = out_dir / "review_queue.csv"
    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    rows[0]["review_status"] = "rejected"
    rows[0]["review_notes"] = "not a fit"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    sync_result = subprocess.run(
        [sys.executable, "-m", "fb_leads", "sync", "--csv", str(csv_path), "--leads", str(leads_path)],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    assert sync_result.returncode == 0
    sync_summary = json.loads(sync_result.stdout)
    assert sync_summary["updated"] == 1
    assert load(leads_path)[rows[0]["id"]].review_status == "rejected"


def test_cli_sync_invalid_status_exits_nonzero(tmp_path: Path):
    leads_path = tmp_path / "leads.jsonl"
    out_dir = tmp_path / "report"
    save(leads_path, _sample_leads())
    generate_report(leads_path, out_dir)
    csv_path = out_dir / "review_queue.csv"
    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    rows[0]["review_status"] = "maybe"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    result = subprocess.run(
        [sys.executable, "-m", "fb_leads", "sync", "--csv", str(csv_path), "--leads", str(leads_path)],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "invalid review_status" in result.stderr
