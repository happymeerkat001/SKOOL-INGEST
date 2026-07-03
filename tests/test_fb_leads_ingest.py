"""Tests for the Facebook lead ingest pipeline and CLI."""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from fb_leads import ingest
from fb_leads.models import load, save

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fb_leads" / "captures"
REPO = Path(__file__).resolve().parents[1]


def test_ingest_fixtures_writes_jsonl_with_repo_relative_capture_paths(tmp_path: Path):
    leads_path = tmp_path / "leads.jsonl"

    summary = ingest.ingest_captures(FIXTURES, leads_path)
    rows = load(leads_path)

    assert summary["files_read"] == 7
    assert summary["leads_new"] == 8
    assert summary["leads_updated"] == 0
    assert summary["failed"] == 1
    assert len(rows) == 8
    assert all(row.capture_path.startswith("tests/fixtures/fb_leads/captures/") for row in rows.values())
    assert all(row.capture_time for row in rows.values())


def test_second_ingest_is_idempotent_and_preserves_review_fields(tmp_path: Path):
    leads_path = tmp_path / "leads.jsonl"
    ingest.ingest_captures(FIXTURES, leads_path)
    rows = load(leads_path)
    first_id = next(iter(rows))
    rows[first_id].review_status = "approved"
    rows[first_id].review_notes = "follow up Friday"
    save(leads_path, rows.values())

    summary = ingest.ingest_captures(FIXTURES, leads_path)
    updated = load(leads_path)

    assert summary["leads_new"] == 0
    assert summary["leads_updated"] == 8
    assert len(updated) == 8
    assert updated[first_id].review_status == "approved"
    assert updated[first_id].review_notes == "follow up Friday"


def test_empty_captures_dir_succeeds_without_corrupting_store(tmp_path: Path):
    captures = tmp_path / "captures"
    captures.mkdir()
    leads_path = tmp_path / "leads.jsonl"

    summary = ingest.ingest_captures(captures, leads_path)

    assert summary == {
        "files_read": 0,
        "leads_seen": 0,
        "leads_new": 0,
        "leads_updated": 0,
        "failed": 0,
        "leads_path": str(leads_path),
    }
    assert load(leads_path) == {}


def test_live_stub_refuses_and_leaves_store_untouched(tmp_path: Path, capsys):
    leads_path = tmp_path / "leads.jsonl"

    code = ingest.refuse_live_capture(leads_path)
    captured = capsys.readouterr()

    assert code == 2
    assert "live capture not implemented in v1" in captured.err
    assert not leads_path.exists()


def test_status_summary_counts_by_band_review_source_and_extraction(tmp_path: Path):
    leads_path = tmp_path / "leads.jsonl"
    ingest.ingest_captures(FIXTURES, leads_path)

    summary = ingest.status_summary(leads_path)

    assert summary["total"] == 8
    assert summary["by_score_band"] == {"unscored": 8}
    assert summary["by_review_status"] == {"pending": 8}
    assert summary["by_extraction"]["failed"] == 1
    assert summary["by_source_type"]["csv_import"] == 2


def test_cli_ingest_prints_json_summary(tmp_path: Path):
    leads_path = tmp_path / "cli-leads.jsonl"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fb_leads",
            "ingest",
            "--captures",
            str(FIXTURES),
            "--leads",
            str(leads_path),
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    summary = json.loads(result.stdout)
    assert summary["files_read"] == 7
    assert summary["leads_new"] == 8
    assert leads_path.exists()


def test_cli_live_stub_exits_2_and_does_not_write_store(tmp_path: Path):
    leads_path = tmp_path / "live.jsonl"
    result = subprocess.run(
        [sys.executable, "-m", "fb_leads", "ingest", "--live", "--leads", str(leads_path)],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "live capture not implemented in v1" in result.stderr
    assert not leads_path.exists()


def test_cli_status_prints_json_counts(tmp_path: Path):
    leads_path = tmp_path / "status.jsonl"
    ingest.ingest_captures(FIXTURES, leads_path)

    result = subprocess.run(
        [sys.executable, "-m", "fb_leads", "status", "--leads", str(leads_path)],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    summary = json.loads(result.stdout)
    assert summary["total"] == 8
    assert summary["by_review_status"] == {"pending": 8}


def test_cli_run_full_pipeline_writes_consistent_artifacts(tmp_path: Path):
    out_dir = tmp_path / "fb_leads_dryrun"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fb_leads",
            "run",
            "--captures",
            str(FIXTURES),
            "--out-dir",
            str(out_dir),
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    summary = json.loads(result.stdout)
    assert summary["ingest"]["leads_seen"] == 8
    assert summary["score"]["scored"] == 8
    assert summary["report"]["rows"] == 8
    assert (out_dir / "leads.jsonl").exists()
    assert (out_dir / "review_queue.csv").exists()
    assert (out_dir / "review.html").exists()

    rows = load(out_dir / "leads.jsonl")
    with (out_dir / "review_queue.csv").open(newline="", encoding="utf-8") as fh:
        csv_rows = list(csv.DictReader(fh))
    html = (out_dir / "review.html").read_text(encoding="utf-8")
    assert len(rows) == len(csv_rows) == 8
    assert all(row["capture_path"] in html for row in csv_rows)


def test_cli_run_twice_is_idempotent_for_review_artifacts(tmp_path: Path):
    out_dir = tmp_path / "fb_leads_dryrun"
    cmd = [
        sys.executable,
        "-m",
        "fb_leads",
        "run",
        "--captures",
        str(FIXTURES),
        "--out-dir",
        str(out_dir),
    ]

    first = subprocess.run(cmd, cwd=REPO, text=True, capture_output=True, check=False)
    csv_first = (out_dir / "review_queue.csv").read_text(encoding="utf-8")
    html_first = (out_dir / "review.html").read_text(encoding="utf-8")
    second = subprocess.run(cmd, cwd=REPO, text=True, capture_output=True, check=False)

    assert first.returncode == 0
    assert second.returncode == 0
    assert (out_dir / "review_queue.csv").read_text(encoding="utf-8") == csv_first
    assert (out_dir / "review.html").read_text(encoding="utf-8") == html_first
