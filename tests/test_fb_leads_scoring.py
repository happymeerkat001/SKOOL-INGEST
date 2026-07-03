"""Tests for deterministic Facebook lead scoring."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from fb_leads import ingest
from fb_leads.models import LeadCandidate, load, save
from fb_leads.scoring import HOT_THRESHOLD, WARM_THRESHOLD, score_lead, score_store

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fb_leads" / "captures"
REPO = Path(__file__).resolve().parents[1]


def test_score_lead_fires_multiple_positive_rules_with_reasons_and_tags():
    lead = LeadCandidate(
        title="Landlord wants coliving partner",
        body_text="I am a landlord looking to rent by the room with utilities included.",
        capture_path="captures/fb/a.html",
    )

    scored = score_lead(lead)

    assert scored.score >= HOT_THRESHOLD
    assert scored.score_band == "hot"
    assert "owner" in scored.tags
    assert "coliving" in scored.tags
    assert any("landlord" in reason and "+" in reason and "owner" in reason for reason in scored.score_reasons)
    assert any("rent by the room" in reason and "coliving" in reason for reason in scored.score_reasons)


def test_demand_post_gets_negative_weight_and_low_band():
    lead = LeadCandidate(
        title="Looking for a room",
        body_text="Need a place by Friday. Looking for a room near downtown.",
        capture_path="captures/fb/demand.html",
    )

    scored = score_lead(lead)

    assert scored.score < WARM_THRESHOLD
    assert scored.score_band == "low"
    assert "demand" in scored.tags
    assert any("looking for a room" in reason for reason in scored.score_reasons)


def test_caution_marker_gets_negative_weight_and_tag():
    lead = LeadCandidate(
        title="Cheap room",
        body_text="Deposit before viewing. Cashapp only.",
        capture_path="captures/fb/caution.html",
    )

    scored = score_lead(lead)

    assert scored.score < 0
    assert scored.score_band == "low"
    assert "caution" in scored.tags


def test_warm_threshold_boundary_is_inclusive():
    lead = LeadCandidate(
        title="Private room",
        body_text="Private room available.",
        capture_path="captures/fb/warm.html",
    )

    scored = score_lead(lead)

    assert scored.score == WARM_THRESHOLD
    assert scored.score_band == "warm"


def test_scoring_same_lead_twice_is_deterministic():
    lead = LeadCandidate(
        title="Vacant landlord property",
        body_text="Landlord has a vacant furnished house and is motivated.",
        capture_path="captures/fb/deterministic.html",
    )

    first = score_lead(lead)
    second = score_lead(lead)

    assert first.score == second.score
    assert first.score_band == second.score_band
    assert first.score_reasons == second.score_reasons
    assert first.tags == second.tags


def test_score_store_preserves_review_fields_on_rescore(tmp_path: Path):
    leads_path = tmp_path / "leads.jsonl"
    lead = LeadCandidate(
        title="Landlord vacant house",
        body_text="Landlord has a vacant furnished house.",
        capture_path="captures/fb/review.html",
        review_status="approved",
        review_notes="already called",
    )
    save(leads_path, [lead])

    summary = score_store(leads_path)
    rows = load(leads_path)
    scored = rows[lead.id]

    assert summary["scored"] == 1
    assert scored.review_status == "approved"
    assert scored.review_notes == "already called"
    assert scored.score > 0
    assert scored.score_band in {"warm", "hot"}


def test_failed_extraction_empty_body_scores_zero_low_without_crash():
    lead = LeadCandidate(
        title="",
        body_text="",
        extraction="failed",
        capture_path="captures/fb/failed.html",
    )

    scored = score_lead(lead)

    assert scored.score == 0
    assert scored.score_band == "low"
    assert scored.score_reasons == []
    assert scored.tags == []


def test_score_store_only_unscored_skips_already_scored(tmp_path: Path):
    leads_path = tmp_path / "leads.jsonl"
    scored = LeadCandidate(
        title="Existing",
        body_text="Landlord vacant house",
        score=99,
        score_band="hot",
        score_reasons=["manual"],
        tags=["manual"],
        capture_path="captures/fb/existing.html",
    )
    unscored = LeadCandidate(
        title="Private room",
        body_text="Private room available",
        capture_path="captures/fb/unscored.html",
    )
    save(leads_path, [scored, unscored])

    summary = score_store(leads_path, only_unscored=True)
    rows = load(leads_path)

    assert summary["scored"] == 1
    assert summary["skipped"] == 1
    assert rows[scored.id].score == 99
    assert rows[scored.id].score_reasons == ["manual"]
    assert rows[unscored.id].score == WARM_THRESHOLD


def test_cli_score_updates_store_and_status_bands(tmp_path: Path):
    leads_path = tmp_path / "cli-score.jsonl"
    ingest.ingest_captures(FIXTURES, leads_path)

    result = subprocess.run(
        [sys.executable, "-m", "fb_leads", "score", "--leads", str(leads_path)],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    summary = json.loads(result.stdout)
    assert summary["scored"] == 8
    rows = load(leads_path)
    assert any(row.score_band in {"warm", "hot"} for row in rows.values())

    status = subprocess.run(
        [sys.executable, "-m", "fb_leads", "status", "--leads", str(leads_path)],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    assert status.returncode == 0
    status_summary = json.loads(status.stdout)
    assert "unscored" not in status_summary["by_score_band"]
