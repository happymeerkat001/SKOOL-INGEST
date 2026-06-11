"""Tests for the one-shot Skool login helper's cookie writer."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from skool_login import _is_skool, _write_netscape  # noqa: E402


def test_is_skool_matches_apex_and_subdomains():
    assert _is_skool("skool.com")
    assert _is_skool(".skool.com")
    assert _is_skool("www.skool.com")
    assert _is_skool("WWW.SKOOL.COM")
    assert not _is_skool("example.com")
    assert not _is_skool("notskool.com")


def test_write_netscape_basic(tmp_path: Path):
    cookies = [
        {"domain": ".skool.com", "path": "/", "secure": True, "expires": 1700000000,
         "name": "skool_session", "value": "abc123"},
        {"domain": "skool.com", "path": "/", "secure": False, "expires": 0,
         "name": "guest_id", "value": "g-42"},
    ]
    out = tmp_path / "cookies.txt"
    _write_netscape(cookies, out)
    text = out.read_text()
    # Header is present
    assert text.startswith("# Netscape HTTP Cookie File")
    # Each cookie is tab-separated
    lines = [l for l in text.splitlines() if l and not l.startswith("#")]
    assert len(lines) == 2
    # First cookie preserves the leading dot
    parts = lines[0].split("\t")
    assert parts[0] == ".skool.com"
    assert parts[1] == "TRUE"
    assert parts[2] == "/"
    assert parts[3] == "TRUE"  # secure
    assert parts[4] == "1700000000"
    assert parts[5] == "skool_session"
    assert parts[6] == "abc123"
    # Second cookie (session) gets expires=0
    parts2 = lines[1].split("\t")
    assert parts2[4] == "0"
    assert parts2[3] == "FALSE"  # not secure


def test_write_netscape_session_cookie_handled(tmp_path: Path):
    cookies = [
        {"domain": "skool.com", "path": "/", "secure": False, "expires": -1,
         "name": "transient", "value": "x"},
    ]
    out = tmp_path / "cookies.txt"
    _write_netscape(cookies, out)
    line = [l for l in out.read_text().splitlines() if l and not l.startswith("#")][0]
    assert line.split("\t")[4] == "0"  # -1 → 0


def test_write_netscape_creates_parent_dir(tmp_path: Path):
    cookies = [{"domain": "skool.com", "path": "/", "secure": False, "expires": 0,
                "name": "x", "value": "y"}]
    out = tmp_path / "does" / "not" / "exist" / "cookies.txt"
    _write_netscape(cookies, out)
    assert out.exists()
