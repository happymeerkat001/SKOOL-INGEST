"""Smoke tests for the cred-free parts of skool-ingest.

These run without network and without any API keys. If they fail, the
package itself is broken. Real end-to-end behavior is exercised by the
``crawl`` and ``fanout`` subcommands, which do need Skool cookies and a
transcript.lol key.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the package importable when running ``pytest`` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skool_ingest import manifest  # noqa: E402
from skool_ingest.skool_crawl import detect_embed, load_netscape_cookies  # noqa: E402


def test_detect_embed_basic():
    assert detect_embed("https://www.loom.com/share/abc") == "loom"
    assert detect_embed("https://youtu.be/dQw4w9WgXcQ") == "youtube"
    assert detect_embed("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "youtube"
    assert detect_embed("https://vimeo.com/12345") == "vimeo"
    assert detect_embed("https://stream.mux.com/abc.m3u8") == "m3u8"
    assert detect_embed("https://example.com/clip.mp4") == "mp4"
    assert detect_embed("https://example.com/whatever") == "other"
    assert detect_embed("") == "other"


def test_manifest_roundtrip(tmp_path: Path):
    p = tmp_path / "m.csv"
    a = manifest.Row(
        post_url="https://skool.example/post/1",
        post_title="hello",
        post_author="andres",
        post_date="2024-01-01",
        video_url="https://www.loom.com/share/abc",
        embed_type="loom",
    )
    b = manifest.Row(
        post_url="https://skool.example/post/2",
        post_title="world",
        post_author="andres",
        post_date="2024-01-02",
        video_url="https://youtu.be/dQw4w9WgXcQ",
        embed_type="youtube",
    )
    manifest.save(p, [a, b])
    loaded = manifest.load(p)
    assert len(loaded) == 2
    assert loaded[a.id].post_title == "hello"
    assert loaded[b.id].embed_type == "youtube"


def test_manifest_id_is_stable_across_post_url():
    # Same post + video → same id, regardless of when the row was made.
    r1 = manifest.Row(post_url="p", post_title="t", post_author="a", post_date="",
                      video_url="v", embed_type="loom")
    r2 = manifest.Row(post_url="p", post_title="t2", post_author="a", post_date="",
                      video_url="v", embed_type="loom")
    assert r1.id == r2.id


def test_upsert_merges(tmp_path: Path):
    p = tmp_path / "m.csv"
    r = manifest.Row(post_url="p", post_title="t", post_author="a", post_date="",
                     video_url="v", embed_type="loom")
    manifest.upsert(p, r)
    r.status = manifest.STATUS_DONE
    r.transcript_url = "https://transcript.lol/x"
    manifest.upsert(p, r)
    loaded = manifest.load(p)
    assert loaded[r.id].status == "done"
    assert loaded[r.id].transcript_url == "https://transcript.lol/x"


def test_load_netscape_cookies(tmp_path: Path):
    p = tmp_path / "cookies.txt"
    p.write_text(
        "# Netscape HTTP Cookie File\n"
        "example.com\tFALSE\t/\tFALSE\t0\tsess\tabc123\n"
        "skool.com\tTRUE\t/\tFALSE\t0\tauth\txyz\n"
    )
    cookies = load_netscape_cookies(p)
    assert len(cookies) == 2
    assert cookies[0]["name"] == "sess"
    assert cookies[1]["name"] == "auth"


def test_transcript_lol_init_rejects_empty_key():
    os.environ.pop("TRANSCRIPT_LOL_API_KEY", None)
    from skool_ingest.transcript_lol import TranscriptLol, TranscriptLolError
    try:
        TranscriptLol(api_key="")
    except TranscriptLolError as exc:
        assert "TRANSCRIPT_LOL_API_KEY" in str(exc)
    else:
        raise AssertionError("expected TranscriptLolError on empty key")
