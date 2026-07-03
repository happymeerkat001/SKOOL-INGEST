"""Tests for the Facebook leads model + JSONL store."""
from __future__ import annotations

from pathlib import Path

from fb_leads.models import LeadCandidate, load, make_id, merge, normalize_url, save


def test_make_id_strips_facebook_tracking_params():
    base = "https://www.facebook.com/marketplace/item/123?fbclid=abc&ref=share"
    clean = "https://www.facebook.com/marketplace/item/123"

    assert normalize_url(base) == clean
    assert make_id(base, "Room for rent") == make_id(clean, "Room for rent")


def test_make_id_falls_back_to_capture_path_and_body_hash_when_source_url_missing():
    first = LeadCandidate(
        source_url="",
        title="Untitled capture",
        body_text="Landlord tired of managing a vacant house",
        capture_path="captures/fb/post.html",
    )
    second = LeadCandidate(
        source_url="",
        title="Different parsed title should not matter without a URL",
        body_text="Landlord tired of managing a vacant house",
        capture_path="captures/fb/post.html",
    )

    assert first.id == second.id


def test_jsonl_round_trip_preserves_lists_and_fields(tmp_path: Path):
    path = tmp_path / "leads.jsonl"
    lead = LeadCandidate(
        source_url="https://facebook.com/groups/x/posts/1?tracking=1",
        source_type="group_post",
        title="Owner looking for help",
        body_text="I own a vacant furnished house",
        price_text="$650/mo",
        price_value=650.0,
        currency="USD",
        location="Houston, TX",
        posted_at="2026-07-01T10:00:00+00:00",
        seller_name="Visible Name",
        images=[{"src_name": "image1.jpg", "alt": "front room"}],
        capture_time="2026-07-02T10:00:00+00:00",
        capture_path="captures/fb/group.html",
        extraction="ok",
        score=9,
        score_band="hot",
        score_reasons=["matched owner (+3, owner)"],
        tags=["owner", "room_supply"],
        review_status="approved",
        review_notes="call back",
    )

    save(path, [lead])
    loaded = load(path)

    assert list(loaded) == [lead.id]
    got = loaded[lead.id]
    assert got.images == [{"src_name": "image1.jpg", "alt": "front room"}]
    assert got.score_reasons == ["matched owner (+3, owner)"]
    assert got.tags == ["owner", "room_supply"]
    assert got.review_status == "approved"
    assert got.price_value == 650.0


def test_merge_refreshes_extracted_fields_but_preserves_review_and_score_fields():
    existing = LeadCandidate(
        source_url="https://facebook.com/marketplace/item/1",
        title="Old title",
        body_text="old body",
        capture_path="captures/fb/old.html",
        score=8,
        score_band="hot",
        score_reasons=["old reason"],
        tags=["owner"],
        review_status="approved",
        review_notes="call back",
    )
    incoming = LeadCandidate(
        source_url="https://facebook.com/marketplace/item/1?fbclid=abc",
        title="Old title",
        body_text="fresh body text",
        location="Austin, TX",
        capture_path="captures/fb/new.html",
        score=0,
        score_band="unscored",
        review_status="pending",
        review_notes="",
    )

    merged = merge(existing, incoming)

    assert merged.id == existing.id
    assert merged.body_text == "fresh body text"
    assert merged.location == "Austin, TX"
    assert merged.capture_path == "captures/fb/new.html"
    assert merged.score == 8
    assert merged.score_band == "hot"
    assert merged.score_reasons == ["old reason"]
    assert merged.tags == ["owner"]
    assert merged.review_status == "approved"
    assert merged.review_notes == "call back"


def test_load_ignores_unknown_extra_fields(tmp_path: Path):
    path = tmp_path / "leads.jsonl"
    path.write_text(
        '{"source_url":"https://facebook.com/p/1","title":"Title",'
        '"body_text":"body","capture_path":"captures/fb/a.html",'
        '"future_field":"safe to ignore"}\n',
        encoding="utf-8",
    )

    loaded = load(path)

    assert len(loaded) == 1
    lead = next(iter(loaded.values()))
    assert lead.title == "Title"


def test_load_empty_or_missing_store_returns_empty(tmp_path: Path):
    assert load(tmp_path / "missing.jsonl") == {}

    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert load(empty) == {}
