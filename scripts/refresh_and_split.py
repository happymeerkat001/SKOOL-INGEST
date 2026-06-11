#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skool_ingest import manifest  # noqa: E402

DEFAULT_REPORT_DIR = Path(
    "/Users/leon/Library/Mobile Documents/iCloud~md~obsidian/Documents/AI-Vault/Land/Joe MCcall/Skool Ingest"
)
DEFAULT_VAULT_ROOT = Path(
    "/Users/leon/Library/Mobile Documents/iCloud~md~obsidian/Documents/AI-Vault"
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Refresh the Playwright scrape, split the manifest, and write a vault report."
    )
    p.add_argument(
        "--skip-scrape",
        action="store_true",
        help="reuse the current manifest instead of re-running the Playwright scrape",
    )
    p.add_argument(
        "--manifest",
        default=str(ROOT / "manifest" / "skool_videos.csv"),
        help="path to the canonical manifest CSV",
    )
    p.add_argument(
        "--report-dir",
        default=str(DEFAULT_REPORT_DIR),
        help="directory for the split CSVs + markdown report",
    )
    p.add_argument(
        "--vault-root",
        default=str(DEFAULT_VAULT_ROOT),
        help="Obsidian vault root for optional transcript capture rendering",
    )
    p.add_argument(
        "--render-done",
        action="store_true",
        help="render transcript captures for rows already marked done",
    )
    return p.parse_args()


def run_scrape() -> None:
    cmd = [str(ROOT / ".venv" / "bin" / "python"), str(ROOT / "scripts" / "skool_playwright_crawl.py")]
    env = dict(**__import__("os").environ)
    env["PYTHONPATH"] = str(ROOT)
    subprocess.run(cmd, cwd=ROOT, env=env, check=True)


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def short_video_url(url: str) -> str:
    if "token=" in url:
        prefix, _sep, _rest = url.partition("token=")
        return prefix + "token=[redacted]"
    return url


def render_report(rows: list[dict[str, str]], report_path: Path, file_names: dict[str, str]) -> None:
    youtube_rows = [r for r in rows if r.get("embed_type") == "youtube"]
    m3u8_rows = [r for r in rows if r.get("embed_type") == "m3u8"]
    other_rows = [r for r in rows if r.get("embed_type") not in {"youtube", "m3u8"}]

    def bullets(subset: list[dict[str, str]]) -> str:
        out = []
        for row in subset:
            out.append(
                "- {title}\n"
                "  - post: {post}\n"
                "  - video: {video}".format(
                    title=row.get("post_title", ""),
                    post=row.get("post_url", ""),
                    video=short_video_url(row.get("video_url", "")),
                )
            )
        return "\n".join(out) if out else "- none"

    text = f"""# {datetime.now().date()} Skool ingest report

## Result
- Scrape completed successfully
- Total videos captured: {len(rows)}
- YouTube: {len(youtube_rows)}
- Protected Skool m3u8: {len(m3u8_rows)}
- Other: {len(other_rows)}

## Files
- Full export: [[{file_names['all']}]]
- YouTube only: [[{file_names['youtube']}]]
- Protected m3u8 only: [[{file_names['m3u8']}]]
- Summary JSON: [[{file_names['summary']}]]

## Browser workflow status for transcript.lol
- transcript.lol is reachable in browser
- Logged-out flow shows Login/Register and an upload/transcribe interface, but transcription is disabled until auth
- Current browser session is not authenticated there
- So browser-based transcript submission appears possible in principle, but is currently blocked on user login/auth in this session

## Recommended next transcription path
### 1) YouTube items first
These are the easiest to process because they already point at public YouTube URLs.

{bullets(youtube_rows)}

### 2) Protected Skool m3u8 items second
These were resolved successfully during the authenticated scrape, but they are tokenized stream URLs tied to the authenticated session and may expire.

{bullets(m3u8_rows)}

## Suggested execution order
1. Use transcript.lol browser login or API key for the 4 YouTube items first.
2. For the 20 m3u8 items, either:
   - submit immediately while tokens are fresh, or
   - re-run the Playwright scrape right before upload to refresh links.
3. After transcription succeeds, render final captures into vault notes.

## Notes
- Manifest source: `{ROOT / 'manifest' / 'skool_videos.csv'}`
- Repo output remains the canonical machine-readable manifest.
- The split CSVs here are for review and workflow management inside the vault.
"""
    report_path.write_text(text, encoding="utf-8")


def maybe_render_done(manifest_path: Path, vault_root: Path) -> tuple[bool, str]:
    rows = manifest.load(manifest_path)
    done_count = sum(1 for row in rows.values() if row.status == manifest.STATUS_DONE)
    if done_count == 0:
        return False, "no done rows yet; skipped render"
    cmd = [
        str(ROOT / ".venv" / "bin" / "python"),
        "-m",
        "skool_ingest",
        "render",
        "--manifest",
        str(manifest_path),
        "--vault-dir",
        str(vault_root),
    ]
    subprocess.run(cmd, cwd=ROOT, check=True)
    return True, f"rendered {done_count} done rows into {vault_root / 'Skool Ingest'}"


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest)
    report_dir = Path(args.report_dir)
    vault_root = Path(args.vault_root)

    if not args.skip_scrape:
        run_scrape()

    if not manifest_path.exists():
        raise SystemExit(f"manifest not found: {manifest_path}")

    with manifest_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
        fieldnames = list(rows[0].keys()) if rows else list(manifest.COLUMNS)

    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    all_name = f"{stamp} skool-videos-all.csv"
    youtube_name = f"{stamp} skool-videos-youtube.csv"
    m3u8_name = f"{stamp} skool-videos-m3u8.csv"
    summary_name = f"{stamp} skool-videos-summary.json"
    report_name = f"{stamp} skool-ingest-report.md"

    youtube_rows = [r for r in rows if r.get("embed_type") == "youtube"]
    m3u8_rows = [r for r in rows if r.get("embed_type") == "m3u8"]
    other_rows = [r for r in rows if r.get("embed_type") not in {"youtube", "m3u8"}]

    write_csv(report_dir / all_name, rows, fieldnames)
    write_csv(report_dir / youtube_name, youtube_rows, fieldnames)
    write_csv(report_dir / m3u8_name, m3u8_rows, fieldnames)
    (report_dir / summary_name).write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "total_rows": len(rows),
                "counts_by_embed_type": dict(Counter(r.get("embed_type") or "unknown" for r in rows)),
                "youtube_rows": len(youtube_rows),
                "m3u8_rows": len(m3u8_rows),
                "other_rows": len(other_rows),
                "manifest_path": str(manifest_path),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    render_report(
        rows,
        report_dir / report_name,
        {
            "all": all_name,
            "youtube": youtube_name,
            "m3u8": m3u8_name,
            "summary": summary_name,
        },
    )

    render_result = None
    if args.render_done:
        rendered, render_result = maybe_render_done(manifest_path, vault_root)
    else:
        render_result = "render step not requested"

    print(
        json.dumps(
            {
                "manifest": str(manifest_path),
                "rows": len(rows),
                "youtube": len(youtube_rows),
                "m3u8": len(m3u8_rows),
                "other": len(other_rows),
                "report_dir": str(report_dir),
                "files": [all_name, youtube_name, m3u8_name, summary_name, report_name],
                "render": render_result,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
