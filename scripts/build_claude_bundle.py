"""Build a single consolidated markdown bundle of all available Skool transcripts
that Claude can ingest in one shot.

Reads from:
  - AI-Vault/Skool Ingest/*.md (real transcripts from transcript.lol)
  - manifest/transcripts/*.txt (raw transcript.lol text exports)

Writes:
  - AI-Vault/Skool Ingest/claude-bundle.md (or path arg)

Also writes a small manifest index for the bundle.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

VAULT_DEFAULT = Path(
    "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/AI-Vault/Skool Ingest"
).expanduser()
MANIFEST_TRANSCRIPTS = Path("manifest/transcripts")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vault-dir", type=Path, default=VAULT_DEFAULT)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--transcripts-dir", type=Path, default=MANIFEST_TRANSCRIPTS)
    return ap.parse_args()


def collect_vault_notes(vault_dir: Path) -> list[Path]:
    if not vault_dir.exists():
        return []
    notes = []
    skip_prefixes = ("_", "claude-bundle", "CLAUDE")
    for path in vault_dir.glob("*.md"):
        if any(path.name.startswith(prefix) for prefix in skip_prefixes):
            continue
        notes.append(path)
    return sorted(notes, key=lambda p: p.name.lower())


def collect_appendix_files() -> list[Path]:
    candidates = [
        Path("manifest/orchestrator-context.md"),
        Path("manifest/code-appendix.md"),
    ]
    return [p for p in candidates if p.exists() and p.stat().st_size > 0]


def has_real_transcript(text: str) -> bool:
    if "(transcript not available)" in text:
        return False
    if "Generated using Transcript LOL" in text:
        return len(text.strip().splitlines()) > 20
    return len(text.strip()) > 1500


def collect_raw_transcripts(transcripts_dir: Path) -> list[Path]:
    if not transcripts_dir.exists():
        return []
    files = []
    for path in transcripts_dir.glob("*.txt"):
        if path.stat().st_size > 200:
            files.append(path)
    return sorted(files)


def main() -> int:
    args = parse_args()
    vault_dir = args.vault_dir.expanduser()
    out_path = (args.out or vault_dir / "claude-bundle.md").expanduser()

    notes = collect_vault_notes(vault_dir)
    raw = collect_raw_transcripts(args.transcripts_dir.expanduser())
    appendices = collect_appendix_files()

    sections: list[str] = []
    sections.append("# Skool Coliving Freedom Unlocked — Transcript Bundle")
    sections.append("")
    sections.append(f"_Built {date.today().isoformat()} for Claude._")
    sections.append("")
    sections.append("## How to use this bundle")
    sections.append("")
    sections.append(
        "1. Read this entire bundle first.\n"
        "2. Treat each `## Session:` heading as one transcript.\n"
        "3. Cite by `## Session: <Title>` (and date in body if present).\n"
        "4. If a session is marked `(STUB)`, the source post was captured but the full transcript is not yet available — do not invent content from it.\n"
        "5. When asked for synthesis, do not regurgitate. Cluster, rank, cite.\n"
        "6. The `Orchestrator + Skool Ingest Context` and `Source Code Appendix` sections at the bottom of this bundle describe the engineering. Read them once."
    )
    sections.append("")
    sections.append("## Inventory")
    sections.append("")
    sections.append("| # | File | Type | Status |")
    sections.append("|---|------|------|--------|")

    bundle_index: list[dict] = []
    real_count = 0
    stub_count = 0

    for index, note in enumerate(notes, start=1):
        text = note.read_text(encoding="utf-8")
        is_real = has_real_transcript(text)
        status = "OK" if is_real else "STUB"
        if is_real:
            real_count += 1
        else:
            stub_count += 1
        sections.append(f"| {index} | `{note.name}` | vault-note | {status} |")
        bundle_index.append(
            {
                "index": index,
                "file": str(note),
                "type": "vault-note",
                "status": status,
                "title": note.stem,
            }
        )

    for index, txt in enumerate(raw, start=len(notes) + 1):
        text = txt.read_text(encoding="utf-8")
        sections.append(f"| {index} | `manifest/transcripts/{txt.name}` | raw-transcript | OK |")
        bundle_index.append(
            {
                "index": index,
                "file": str(txt),
                "type": "raw-transcript",
                "status": "OK",
                "title": txt.stem,
            }
        )
        real_count += 1

    sections.append("")
    sections.append(
        f"_Real transcripts: {real_count}. Stubs: {stub_count}. Total source files: {len(notes) + len(raw)}._"
    )
    sections.append("")
    sections.append("---")
    sections.append("")
    sections.append("# Transcripts")
    sections.append("")

    for note in notes:
        text = note.read_text(encoding="utf-8")
        is_real = has_real_transcript(text)
        title = note.stem
        banner = "## Session: " + title + (" (STUB)" if not is_real else "")
        sections.append(banner)
        sections.append("")
        sections.append(f"<!-- source: {note.name} -->")
        sections.append("")
        sections.append(text.strip())
        sections.append("")
        sections.append("---")
        sections.append("")

    for txt in raw:
        text = txt.read_text(encoding="utf-8")
        sections.append(f"## Session: {txt.stem}")
        sections.append("")
        sections.append(f"<!-- source: manifest/transcripts/{txt.name} -->")
        sections.append("")
        sections.append("```")
        sections.append(text.strip())
        sections.append("```")
        sections.append("")
        sections.append("---")
        sections.append("")

    if appendices:
        sections.append("# Appendix")
        sections.append("")
        sections.append(
            "Read these once so you can answer engineering questions. After reading, "
            "treat them as reference. The transcripts above are the content you are "
            "actually being asked to work with."
        )
        sections.append("")
        for appendix in appendices:
            sections.append("---")
            sections.append("")
            sections.append(f"<!-- appendix: {appendix.name} -->")
            sections.append("")
            sections.append(appendix.read_text(encoding="utf-8").rstrip())
            sections.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(sections), encoding="utf-8")

    index_path = out_path.with_suffix(".index.json")
    index_path.write_text(
        json.dumps(
            {
                "generated": date.today().isoformat(),
                "bundle": str(out_path),
                "real_count": real_count,
                "stub_count": stub_count,
                "appendices": [str(p) for p in appendices],
                "items": bundle_index,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"wrote {out_path} ({out_path.stat().st_size} bytes)")
    print(f"wrote {index_path}")
    print(f"real: {real_count}  stub: {stub_count}  appendices: {len(appendices)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
