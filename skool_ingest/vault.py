"""Write per-video transcript markdown files into the Obsidian vault.

For every ``Row`` in the manifest with ``status == done`` and a non-empty
``transcript_url``, this module writes one ``<nnn>-<slug>.md`` file into
the configured vault directory, plus a top-level MOC (``_index.md``) that
links to all of them. The MOC is the one you'd bookmark.

Slug rules (deterministic, safe on every filesystem):

    * lowercase, ASCII
    * spaces and runs of non-alphanumerics → single dash
    * collapsed dashes
    * truncated to 60 chars
    * dedupe suffix ``-2``, ``-3``... if collision

Number prefix is the row's position in the manifest (after de-dupe) so
filenames sort chronologically by Skool post order.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from . import manifest


def slugify(text: str, max_len: int = 60) -> str:
    text = (text or "").lower()
    # Transliterate common unicode punctuation
    repl = {
        "’": "", "‘": "", "“": "", "”": "", "–": "-", "—": "-", "_": "-",
        "/": "-", "\\": "-", ":": "-", ".": "-", ",": "-", "?": "", "!": "",
        "(": "", ")": "", "[": "", "]": "", "{": "", "}": "",
    }
    for k, v in repl.items():
        text = text.replace(k, v)
    text = re.sub(r"[^a-z0-9-]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    if len(text) > max_len:
        text = text[:max_len].rstrip("-")
    return text or "untitled"


def safe_join(base: Path, *parts: str) -> Path | None:
    """Join paths and refuse anything that escapes ``base`` (path traversal)."""
    base = base.resolve()
    candidate = (base.joinpath(*parts)).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


def transcript_body_path(row: manifest.Row) -> Path:
    """Where the raw transcript text is on disk (written by fanout.write_transcript)."""
    return Path.cwd() / "manifest" / "transcripts" / f"{row.id}.txt"


def write_capture(
    vault_dir: Path,
    index: int,
    row: manifest.Row,
    *,
    summary: str | None = None,
) -> Path | None:
    """Write one transcript capture into ``<vault_dir>/Skool Ingest/``.

    Returns the path written, or ``None`` if the slug escaped the vault.
    """
    slug = slugify(f"{row.post_title}-{row.id[:6]}")
    folder = safe_join(vault_dir, "Skool Ingest")
    if folder is None:
        return None
    out = safe_join(folder, f"{index:03d}-{slug}.md")
    if out is None:
        return None
    folder.mkdir(parents=True, exist_ok=True)

    body_file = transcript_body_path(row)
    body = ""
    if body_file.exists():
        body = body_file.read_text(encoding="utf-8")

    fm_lines = [
        "---",
        f"title: \"{_yaml_escape(row.post_title)}\"",
        f"source_url: \"{_yaml_escape(row.post_url)}\"",
        f"video_url: \"{_yaml_escape(row.video_url)}\"",
        f"embed_type: \"{row.embed_type}\"",
        f"transcript_url: \"{_yaml_escape(row.transcript_url)}\"",
        f"captured_at: \"{row.captured_at}\"",
        f"row_id: \"{row.id}\"",
        "---",
        "",
        f"# {row.post_title}",
        "",
        f"- **Source post:** <{row.post_url}>",
        f"- **Source video:** <{row.video_url}>  ({row.embed_type})",
        f"- **Transcript:** <{row.transcript_url}>",
        f"- **Captured at:** {row.captured_at}",
        "",
    ]
    if summary:
        fm_lines += ["## TL;DR", "", summary, ""]
    fm_lines += ["## Transcript", "", body.strip() or "_(transcript not available)_", ""]
    out.write_text("\n".join(fm_lines), encoding="utf-8")
    return out


def write_moc(vault_dir: Path, captures: list[tuple[int, manifest.Row, Path]]) -> Path | None:
    """Write a MOC at ``<vault_dir>/Skool Ingest/_index.md`` linking all captures."""
    folder = safe_join(vault_dir, "Skool Ingest")
    if folder is None:
        return None
    folder.mkdir(parents=True, exist_ok=True)
    out = safe_join(folder, "_index.md")
    if out is None:
        return None

    by_embed: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for _idx, row, _path in captures:
        by_embed[row.embed_type] = by_embed.get(row.embed_type, 0) + 1
        by_status[row.status] = by_status.get(row.status, 0) + 1

    lines = [
        "---",
        "title: \"Skool Coliving Unlocked — Transcripts\"",
        f"generated_at: \"{manifest.now_iso()}\"",
        f"total_captures: {len(captures)}",
        "---",
        "",
        "# Skool Coliving Unlocked — Transcripts",
        "",
        f"- **Total:** {len(captures)}",
        f"- **By embed type:** {by_embed}",
        f"- **By status:** {by_status}",
        "",
        "## Captures",
        "",
    ]
    for idx, row, path in captures:
        rel = path.relative_to(folder)
        lines.append(f"- [{idx:03d} {row.post_title}]({rel.as_posix()}) — {row.embed_type}, {row.status}")
    lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def _yaml_escape(value: str) -> str:
    """Escape a string for safe inclusion inside a YAML double-quoted scalar."""
    if value is None:
        return ""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def render_all(
    vault_dir: Path,
    rows: Iterable[manifest.Row],
    *,
    include_failed: bool = False,
) -> list[tuple[int, manifest.Row, Path]]:
    """Render every capturable row to disk; return the list of (index, row, path)."""
    rows_list = list(rows)
    captures: list[tuple[int, manifest.Row, Path]] = []
    idx = 0
    for row in rows_list:
        if row.status == manifest.STATUS_DONE:
            idx += 1
            path = write_capture(vault_dir, idx, row)
            if path is not None:
                captures.append((idx, row, path))
        elif include_failed and row.status == manifest.STATUS_FAILED:
            idx += 1
            path = write_capture(vault_dir, idx, row)
            if path is not None:
                captures.append((idx, row, path))
    write_moc(vault_dir, captures)
    return captures
