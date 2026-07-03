"""Tests for saved Facebook capture extraction."""
from __future__ import annotations

from pathlib import Path

from fb_leads.extract import (
    extract_capture,
    extract_captures,
    load_sidecar,
    parse_price,
    should_skip_capture,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fb_leads" / "captures"


def test_marketplace_html_og_tags_extract_core_fields():
    lead = extract_capture(FIXTURES / "marketplace_room.html", load_sidecar(FIXTURES / "marketplace_room.html"))[0]

    assert lead.title == "$650/mo Private room in furnished coliving house"
    assert lead.source_url == "https://www.facebook.com/marketplace/item/111"
    assert lead.source_type == "marketplace_listing"
    assert lead.price_text == "$650/mo"
    assert lead.price_value == 650.0
    assert lead.currency == "USD"
    assert lead.location == "Houston, TX"
    assert lead.seller_name == "Maria Owner"
    assert lead.images == [{"src_name": "room1.jpg", "alt": "front room"}]
    assert lead.extraction == "ok"


def test_furniture_category_room_listing_uses_best_effort_text_extraction():
    lead = extract_capture(FIXTURES / "marketplace_furniture_bed.html", {})[0]

    assert lead.title == "Furnished room posted as furniture"
    assert "$650-$700" in lead.price_text
    assert lead.price_value is None
    assert lead.location == "Austin, TX"
    assert lead.seller_name == "David Host"
    assert "Private furnished room" in lead.body_text
    assert lead.extraction == "partial"


def test_group_post_extracts_poster_and_body_text():
    lead = extract_capture(FIXTURES / "group_post_landlord.html", {})[0]

    assert lead.source_type == "group_post"
    assert lead.title == "Vacant house near campus"
    assert lead.seller_name == "Angela Landlord"
    assert "corporate lease ideas" in lead.body_text
    assert lead.location == "Philadelphia, PA"


def test_text_note_applies_sidecar_source_and_overrides():
    path = FIXTURES / "notes" / "tired_landlord_note.txt"
    lead = extract_capture(path, load_sidecar(path))[0]

    assert lead.title == "Tired landlord lead"
    assert lead.source_url == "https://www.facebook.com/groups/landlords/posts/222"
    assert lead.source_type == "group_post"
    assert lead.location == "Houston, TX"
    assert lead.price_text == "$1800/mo"
    assert lead.price_value == 1800.0
    assert lead.capture_time == "2026-07-02T19:00:00+00:00"


def test_csv_import_returns_one_lead_per_row():
    leads = extract_capture(FIXTURES / "export_sample.csv", {})

    assert len(leads) == 2
    assert {lead.title for lead in leads} == {"Room for rent CSV", "Owner partner CSV"}
    assert all(lead.source_type == "csv_import" for lead in leads)
    assert leads[0].price_value == 700.0


def test_malformed_html_returns_failed_record_without_raising():
    lead = extract_capture(FIXTURES / "malformed.html", {})[0]

    assert lead.extraction == "failed"
    assert lead.capture_path.endswith("tests/fixtures/fb_leads/captures/malformed.html")
    assert "parse" in lead.body_text.lower()


def test_dispatcher_skips_meta_json_and_os_files():
    assert should_skip_capture(FIXTURES / "marketplace_room.meta.json")
    assert should_skip_capture(FIXTURES / ".DS_Store")

    leads = extract_captures(FIXTURES)
    titles = {lead.title for lead in leads}
    assert "Room for rent CSV" in titles
    assert "Owner partner CSV" in titles
    assert "Tired landlord lead" in titles
    assert "ignored" not in titles


def test_parse_price_edge_cases():
    assert parse_price("650") == ("650", 650.0, "")
    assert parse_price("$650/mo") == ("$650/mo", 650.0, "USD")
    assert parse_price("$650-$700") == ("$650-$700", None, "USD")
    assert parse_price("free") == ("free", None, "")
