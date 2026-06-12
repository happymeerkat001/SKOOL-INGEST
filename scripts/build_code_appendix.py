"""build_code_appendix.py - Inline the key source files into a single .md so
Claude can read the actual code in one place.

Includes:
  - Obsidian-vault-orchestrator/cli/transcribe.py (auth + submit core)
  - Obsidian-vault-orchestrator/cli/export_transcripts.py (vault export)
  - skool-ingest/skool_ingest/manifest.py (CSV schema)
  - skool-ingest/scripts/local_ingest.py (local m3u8 download + transcribe)

Skipped (not interesting for "what's in the coliving content"):
  - media_captions.py
  - chrome-extension/*
  - hermes_worker.py
  - skool_crawl.py (crawler internals, 500+ lines)
  - tests/*
"""

from pathlib import Path
from datetime import date

OUT = Path("manifest/code-appendix.md")

FILES = [
    ("Obsidian-vault-orchestrator/cli/transcribe.py",
     Path("/Users/leon/Documents/Code/Obsidian-vault-orchestrator/cli/transcribe.py")),
    ("Obsidian-vault-orchestrator/cli/export_transcripts.py",
     Path("/Users/leon/Documents/Code/Obsidian-vault-orchestrator/cli/export_transcripts.py")),
    ("skool-ingest/skool_ingest/manifest.py",
     Path("/Users/leon/Documents/Code/skool-ingest/skool_ingest/manifest.py")),
    ("skool-ingest/scripts/local_ingest.py",
     Path("/Users/leon/Documents/Code/skool-ingest/scripts/local_ingest.py")),
]


def main() -> int:
    sections: list[str] = []
    sections.append("# Source Code Appendix")
    sections.append("")
    sections.append(f"_Inline for Claude, built {date.today().isoformat()}._")
    sections.append("")
    sections.append(
        "These are the actual source files that drive the Skool -> transcript.lol -> "
        "Obsidian pipeline. Read them as needed when answering questions about how "
        "the pipeline works, what's broken, or what a code change would touch."
    )
    sections.append("")

    for label, path in FILES:
        if not path.exists():
            sections.append(f"## {label}")
            sections.append("")
            sections.append(f"_MISSING: {path}_")
            sections.append("")
            continue
        sections.append(f"## {label}")
        sections.append("")
        sections.append(f"<!-- absolute path: {path} -->")
        sections.append("")
        sections.append("```python")
        sections.append(path.read_text(encoding="utf-8").rstrip())
        sections.append("```")
        sections.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(sections), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
