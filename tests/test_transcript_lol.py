"""Tests for the transcript.lol v1 client.

We mock ``requests.Session`` so these run without a real API key. The goal
is to verify the client matches the published contract (workspace-scoped,
not flat job-id) and that the field-name fallbacks are sensible.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skool_ingest.transcript_lol import Job, TranscriptLol, TranscriptLolError  # noqa: E402


def make_client():
    """Build a client with a MagicMock session and a known key."""
    os.environ["TRANSCRIPT_LOL_API_KEY"] = "test-key"
    s = MagicMock(spec=requests.Session)
    s.headers = {}
    c = TranscriptLol(api_key="test-key", session=s)
    return c, s


def resp_ok(json_body, status_code=200):
    r = MagicMock()
    r.ok = status_code < 400
    r.status_code = status_code
    r.content = b"x" if json_body is None else b'{"x":1}'
    r.json.return_value = json_body
    r.text = str(json_body)
    r.request = SimpleNamespace(method="GET", url="https://x")
    return r


def test_init_rejects_empty_key():
    os.environ.pop("TRANSCRIPT_LOL_API_KEY", None)
    try:
        TranscriptLol(api_key="")
    except TranscriptLolError as exc:
        assert "TRANSCRIPT_LOL_API_KEY" in str(exc)
    else:
        raise AssertionError("expected error on empty key")


def test_get_account_calls_correct_endpoint():
    c, s = make_client()
    s.get.return_value = resp_ok({"id": "u1", "email": "x@y"})
    acct = c.get_account()
    assert acct == {"id": "u1", "email": "x@y"}
    # Called the right URL with bearer auth
    called_url = s.get.call_args[0][0]
    assert called_url == "https://transcript.lol/api/v1/me"
    assert s.headers["Authorization"] == "Bearer test-key"


def test_list_spaces_unwraps_data_field():
    c, s = make_client()
    s.get.return_value = resp_ok({"data": [{"id": "sp1", "name": "Default"}]})
    spaces = c.list_spaces()
    assert len(spaces) == 1
    assert spaces[0]["id"] == "sp1"


def test_list_spaces_unwraps_spaces_field():
    c, s = make_client()
    s.get.return_value = resp_ok({"spaces": [{"id": "sp1", "name": "X"}]})
    spaces = c.list_spaces()
    assert spaces[0]["name"] == "X"


def test_list_spaces_handles_bare_list():
    c, s = make_client()
    s.get.return_value = resp_ok([{"id": "sp1"}])
    spaces = c.list_spaces()
    assert len(spaces) == 1


def test_ensure_space_creates_when_missing():
    c, s = make_client()
    # First call (list) returns empty; second call (create) returns new space
    s.get.return_value = resp_ok({"data": []})
    s.post.return_value = resp_ok({"id": "new-space-id", "name": "skool-ingest"})
    sid = c.ensure_space("skool-ingest")
    assert sid == "new-space-id"
    # Cached
    sid2 = c.ensure_space("skool-ingest")
    assert sid2 == "new-space-id"
    # Only one POST (the create) happened; only one GET (the initial list)
    assert s.get.call_count == 1
    assert s.post.call_count == 1


def test_ensure_space_reuses_existing():
    c, s = make_client()
    s.get.return_value = resp_ok({"data": [{"id": "existing", "name": "skool-ingest"}]})
    sid = c.ensure_space("skool-ingest")
    assert sid == "existing"
    # No POSTs
    s.post.assert_not_called()


def test_submit_uses_correct_scoped_url():
    c, s = make_client()
    s.get.return_value = resp_ok({"data": [{"id": "sp1", "name": "skool-ingest"}]})
    s.post.return_value = resp_ok({"id": "rec1", "status": "queued"})
    job = c.submit("https://youtu.be/abc", space_id="sp1")
    assert job.id == "rec1"
    assert job.space_id == "sp1"
    assert job.status == "queued"
    called_url = s.post.call_args[0][0]
    assert called_url == "https://transcript.lol/api/v1/spaces/sp1/recordings"
    payload = s.post.call_args[1]["json"]
    assert payload == {"url": "https://youtu.be/abc"}


def test_submit_with_language():
    c, s = make_client()
    s.post.return_value = resp_ok({"id": "rec1", "status": "queued"})
    c.submit("https://x", language="es", space_id="sp1")
    payload = s.post.call_args[1]["json"]
    assert payload["language"] == "es"


def test_fetch_parses_status_from_nested():
    c, s = make_client()
    s.get.return_value = resp_ok({"id": "rec1", "data": {"status": "processing"}})
    job = c.fetch("rec1", space_id="sp1")
    assert job.status == "processing"


def test_get_transcript_returns_text_field():
    c, s = make_client()
    s.get.return_value = resp_ok({"text": "hello world"})
    text = c.get_transcript("rec1", space_id="sp1")
    assert text == "hello world"


def test_get_transcript_returns_stringified_raw():
    c, s = make_client()
    s.get.return_value = resp_ok({"_raw_text": "raw body"})
    text = c.get_transcript("rec1", space_id="sp1")
    assert "raw body" in text


def test_wait_terminal_done_fetches_text():
    c, s = make_client()
    # First call: fetch returns "done"; second call: get_transcript returns text
    s.get.side_effect = [
        resp_ok({"id": "rec1", "status": "done"}),
        resp_ok({"text": "the transcript body"}),
    ]
    job = c.wait("rec1", space_id="sp1", poll_every=0.0)
    assert job.status == "done"
    assert job.text == "the transcript body"


def test_wait_terminal_failed():
    c, s = make_client()
    s.get.return_value = resp_ok({"id": "rec1", "status": "failed", "error": "auth"})
    job = c.wait("rec1", space_id="sp1", poll_every=0.0)
    assert job.status == "failed"


def test_wait_terminal_completed_synonym():
    c, s = make_client()
    s.get.side_effect = [
        resp_ok({"id": "rec1", "status": "completed"}),
        resp_ok({"text": "x"}),
    ]
    job = c.wait("rec1", space_id="sp1", poll_every=0.0)
    assert job.status == "completed"
    assert job.text == "x"


def test_wait_times_out():
    c, s = make_client()
    s.get.return_value = resp_ok({"status": "processing"})
    try:
        c.wait("rec1", space_id="sp1", poll_every=0.0, max_wait=0.5)
    except TranscriptLolError as exc:
        assert "did not finish" in str(exc)
    else:
        raise AssertionError("expected timeout error")


def test_non_2xx_raises():
    c, s = make_client()
    s.get.return_value = resp_ok({}, status_code=401)
    try:
        c.get_account()
    except TranscriptLolError as exc:
        assert "401" in str(exc)
    else:
        raise AssertionError("expected 401 error")
