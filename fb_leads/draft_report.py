"""Posting draft queue CSV, HTML, Markdown exports and checklist sync."""
from __future__ import annotations

import csv
import datetime as _dt
import html
import json
import sys
from pathlib import Path
from typing import Any

from .drafts import YES_NO, PostDraft, load, save

QUEUE_COLUMNS: tuple[str, ...] = (
    "id",
    "scheduled_for",
    "topic",
    "target_surface",
    "title",
    "copy_text",
    "price_text",
    "location",
    "images_note",
    "lead_ids",
    "approved_by_human",
    "scheduled_in_meta_business_suite",
    "posted_at",
    "notes",
)


def generate_queue(drafts_path: Path, out_dir: Path) -> dict[str, Any]:
    rows = sorted(load(drafts_path).values(), key=lambda draft: draft.scheduled_for)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "post_queue.csv"
    html_path = out_dir / "post_queue.html"
    md_path = out_dir / "post_queue.md"
    _write_csv(csv_path, rows)
    _write_html(html_path, rows)
    _write_markdown(md_path, rows)
    return {
        "rows": len(rows),
        "csv_path": str(csv_path),
        "html_path": str(html_path),
        "markdown_path": str(md_path),
        "drafts_path": str(drafts_path),
    }


def sync_queue_csv(csv_path: Path, drafts_path: Path) -> dict[str, Any]:
    rows = load(drafts_path)
    updated = 0
    invalid = 0
    unknown = 0
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            draft_id = raw.get("id", "")
            if draft_id not in rows:
                print(f"unknown draft id skipped: {draft_id}", file=sys.stderr)
                unknown += 1
                continue
            ok = True
            approved = (raw.get("approved_by_human") or "no").strip() or "no"
            scheduled = (raw.get("scheduled_in_meta_business_suite") or "no").strip() or "no"
            posted_at = (raw.get("posted_at") or "").strip()
            if approved not in YES_NO:
                print(f"invalid approved_by_human for {draft_id}: {approved}", file=sys.stderr)
                invalid += 1
                ok = False
            if scheduled not in YES_NO:
                print(
                    f"invalid scheduled_in_meta_business_suite for {draft_id}: {scheduled}",
                    file=sys.stderr,
                )
                invalid += 1
                ok = False
            if posted_at and not _is_iso_datetime(posted_at):
                print(f"invalid posted_at for {draft_id}: {posted_at}", file=sys.stderr)
                invalid += 1
                ok = False
            if not ok:
                continue
            draft = rows[draft_id]
            notes = raw.get("notes") or ""
            if (
                draft.approved_by_human != approved
                or draft.scheduled_in_meta_business_suite != scheduled
                or draft.posted_at != posted_at
                or draft.notes != notes
            ):
                draft.approved_by_human = approved
                draft.scheduled_in_meta_business_suite = scheduled
                draft.posted_at = posted_at
                draft.notes = notes
                updated += 1
    if invalid == 0:
        save(drafts_path, rows.values())
    return {
        "updated": updated if invalid == 0 else 0,
        "invalid": invalid,
        "unknown": unknown,
        "csv_path": str(csv_path),
        "drafts_path": str(drafts_path),
    }


def sync_exit_code(summary: dict[str, Any]) -> int:
    return 1 if int(summary.get("invalid", 0)) else 0


def _write_csv(path: Path, rows: list[PostDraft]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(QUEUE_COLUMNS))
        writer.writeheader()
        for draft in rows:
            writer.writerow(_csv_row(draft))


def _write_html(path: Path, rows: list[PostDraft]) -> None:
    table_rows = "\n".join(_html_row(draft) for draft in rows)
    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Facebook Posting Draft Queue</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 1200px; margin: 2rem auto; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; vertical-align: top; padding: .5rem .75rem; border-bottom: 1px solid #ddd; }}
  pre {{ white-space: pre-wrap; background: #f7f7f7; padding: .75rem; border-radius: .4rem; }}
  code {{ white-space: nowrap; }}
</style></head><body>
<h1>Facebook Posting Draft Queue</h1>
<p>{len(rows)} drafts. Schedule manually in Meta Business Suite; this page never posts.</p>
<table>
<thead><tr><th>Slot</th><th>Topic</th><th>Copy</th><th>Checklist</th></tr></thead>
<tbody>
{table_rows}
</tbody>
</table>
</body></html>"""
    path.write_text(doc, encoding="utf-8")


def _write_markdown(path: Path, rows: list[PostDraft]) -> None:
    chunks = ["# Facebook Posting Draft Queue", "", f"{len(rows)} drafts.", ""]
    for draft in rows:
        chunks.extend(
            [
                f"## {draft.scheduled_for} — {draft.topic}",
                "",
                f"- Surface: {draft.target_surface}",
                f"- Approved: {draft.approved_by_human}",
                f"- Scheduled in Meta Business Suite: {draft.scheduled_in_meta_business_suite}",
                f"- Posted at: {draft.posted_at or '(not posted)'}",
                f"- Notes: {draft.notes}",
                "",
                "```text",
                draft.copy_text,
                "```",
                "",
            ]
        )
    path.write_text("\n".join(chunks), encoding="utf-8")


def _csv_row(draft: PostDraft) -> dict[str, str]:
    return {
        "id": draft.id,
        "scheduled_for": draft.scheduled_for,
        "topic": draft.topic,
        "target_surface": draft.target_surface,
        "title": draft.title,
        "copy_text": draft.copy_text,
        "price_text": draft.price_text,
        "location": draft.location,
        "images_note": draft.images_note,
        "lead_ids": ",".join(draft.lead_ids),
        "approved_by_human": draft.approved_by_human,
        "scheduled_in_meta_business_suite": draft.scheduled_in_meta_business_suite,
        "posted_at": draft.posted_at,
        "notes": draft.notes,
    }


def _html_row(draft: PostDraft) -> str:
    copy = html.escape(draft.copy_text)
    checklist = html.escape(
        f"approved: {draft.approved_by_human}\n"
        f"scheduled: {draft.scheduled_in_meta_business_suite}\n"
        f"posted_at: {draft.posted_at or '(not posted)'}\n"
        f"notes: {draft.notes}"
    )
    title = html.escape(draft.title)
    return (
        f"<tr><td>{html.escape(draft.scheduled_for)}</td>"
        f"<td><strong>{html.escape(draft.topic)}</strong><br>{title}<br>"
        f"<small>{html.escape(draft.target_surface)}</small></td>"
        f"<td><pre>{copy}</pre></td>"
        f"<td><pre>{checklist}</pre></td></tr>"
    )


def _is_iso_datetime(value: str) -> bool:
    try:
        _dt.datetime.fromisoformat(value)
        return True
    except ValueError:
        return False


def print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))
