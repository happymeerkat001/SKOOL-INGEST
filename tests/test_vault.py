"""Tests for the vault output module."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skool_ingest import manifest, vault  # noqa: E402


def test_slugify_strips_punctuation():
    assert vault.slugify("Hello, World!") == "hello-world"
    assert vault.slugify("AI/ML: What's next?") == "ai-ml-what-s-next"
    assert vault.slugify("Test — with em-dash & unicode 你好") == "test-with-em-dash-unicode"


def test_slugify_handles_empty():
    assert vault.slugify("") == "untitled"
    assert vault.slugify("---") == "untitled"


def test_slugify_truncates():
    long = "a" * 200
    s = vault.slugify(long)
    assert len(s) <= 60


def test_safe_join_blocks_traversal(tmp_path: Path):
    base = tmp_path / "vault"
    base.mkdir()
    assert vault.safe_join(base, "subdir", "file.md") is not None
    assert vault.safe_join(base, "..", "escape.md") is None
    assert vault.safe_join(base, "/etc/passwd") is None


def test_write_capture_creates_file(tmp_path: Path):
    # Pre-stage a transcript body keyed by the row's actual id
    row_id = "abc123def456"
    body = tmp_path / "manifest" / "transcripts" / f"{row_id}.txt"
    body.parent.mkdir(parents=True)
    body.write_text("Hello this is a test transcript.", encoding="utf-8")

    # Simulate writing relative to a fake CWD
    import os
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        row = manifest.Row(
            post_url="https://skool.example/p/1",
            post_title="My Talk on AI",
            post_author="andres",
            post_date="2024-01-01",
            video_url="https://www.loom.com/share/abc",
            embed_type="loom",
            status="done",
            transcript_url="https://transcript.lol/x",
            transcript_lol_id="abc",
            id=row_id,
        )
        out = vault.write_capture(vault_dir, 1, row)
        assert out is not None
        assert out.exists()
        text = out.read_text()
        assert "My Talk on AI" in text
        assert "Hello this is a test transcript" in text
        # Frontmatter sanity
        assert text.startswith("---\n")
        assert "transcript_url:" in text
    finally:
        os.chdir(old_cwd)


def test_render_all_writes_moc(tmp_path: Path):
    body = tmp_path / "manifest" / "transcripts" / "row1.txt"
    body.parent.mkdir(parents=True)
    body.write_text("body 1", encoding="utf-8")

    import os
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        r1 = manifest.Row(
            post_url="u1", post_title="Title 1", post_author="a", post_date="",
            video_url="https://loom.com/x", embed_type="loom",
            status="done", transcript_url="https://t.lol/1", id="row1abcdef",
        )
        r2 = manifest.Row(
            post_url="u2", post_title="Title 2", post_author="a", post_date="",
            video_url="https://vimeo.com/2", embed_type="vimeo",
            status="done", transcript_url="https://t.lol/2", id="row2abcdef",
        )
        r3 = manifest.Row(
            post_url="u3", post_title="Title 3", post_author="a", post_date="",
            video_url="https://example.com/3.m3u8", embed_type="m3u8",
            status="failed", failure_reason="auth wall", id="row3abcdef",
        )
        caps = vault.render_all(vault_dir, [r1, r2, r3], include_failed=True)
        assert len(caps) == 3
        idx = vault_dir / "Skool Ingest" / "_index.md"
        assert idx.exists()
        text = idx.read_text()
        assert "Total:** 3" in text
        assert "Title 1" in text
    finally:
        os.chdir(old_cwd)
