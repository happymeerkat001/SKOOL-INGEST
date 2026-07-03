"""Operator review CSV, static HTML report, and CSV sync-back."""
from __future__ import annotations

import csv
import html
import json
import sys
from pathlib import Path
from typing import Any

from .models import REVIEW_STATUSES, LeadCandidate, load, save

REVIEW_COLUMNS: tuple[str, ...] = (
    "id",
    "score",
    "score_band",
    "tags",
    "title",
    "price_text",
    "location",
    "posted_at",
    "seller_name",
    "source_type",
    "source_url",
    "capture_path",
    "score_reasons",
    "review_status",
    "review_notes",
)


def generate_report(leads_path: Path, out_dir: Path) -> dict[str, Any]:
    rows = sorted(load(leads_path).values(), key=lambda lead: lead.score, reverse=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "review_queue.csv"
    html_path = out_dir / "review.html"
    _write_review_csv(csv_path, rows)
    _write_review_html(html_path, rows)
    return {
        "rows": len(rows),
        "csv_path": str(csv_path),
        "html_path": str(html_path),
        "leads_path": str(leads_path),
    }


def sync_review_csv(csv_path: Path, leads_path: Path) -> dict[str, Any]:
    rows = load(leads_path)
    updated = 0
    invalid = 0
    unknown = 0
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            lead_id = raw.get("id", "")
            if lead_id not in rows:
                print(f"unknown lead id skipped: {lead_id}", file=sys.stderr)
                unknown += 1
                continue
            status = (raw.get("review_status") or "pending").strip() or "pending"
            if status not in REVIEW_STATUSES:
                print(f"invalid review_status for {lead_id}: {status}", file=sys.stderr)
                invalid += 1
                continue
            notes = raw.get("review_notes") or ""
            lead = rows[lead_id]
            if lead.review_status != status or lead.review_notes != notes:
                lead.review_status = status
                lead.review_notes = notes
                updated += 1
    if invalid == 0:
        save(leads_path, rows.values())
    return {
        "updated": updated if invalid == 0 else 0,
        "invalid": invalid,
        "unknown": unknown,
        "csv_path": str(csv_path),
        "leads_path": str(leads_path),
    }


def sync_exit_code(summary: dict[str, Any]) -> int:
    return 1 if int(summary.get("invalid", 0)) else 0


def _write_review_csv(path: Path, rows: list[LeadCandidate]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(REVIEW_COLUMNS))
        writer.writeheader()
        for lead in rows:
            writer.writerow(_review_row(lead))


def _write_review_html(path: Path, rows: list[LeadCandidate]) -> None:
    table_rows = "\n".join(_html_row(lead) for lead in rows)
    html_doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Facebook Lead Review Queue</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 1200px; margin: 2rem auto; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; vertical-align: top; padding: .5rem .75rem; border-bottom: 1px solid #ddd; }}
  .band-hot {{ color: #a40000; font-weight: 700; }}
  .band-warm {{ color: #8a5a00; font-weight: 700; }}
  .band-low {{ color: #555; }}
  code {{ white-space: nowrap; }}
</style></head><body>
<h1>Facebook Lead Review Queue</h1>
<p>{len(rows)} leads. Edit <code>review_queue.csv</code> for approve/reject/notes; this HTML is read-only.</p>
<table>
<thead><tr><th>Score</th><th>Band</th><th>Lead</th><th>Details</th><th>Reasons</th><th>Raw</th></tr></thead>
<tbody>
{table_rows}
</tbody>
</table>
</body></html>"""
    path.write_text(html_doc, encoding="utf-8")


def _review_row(lead: LeadCandidate) -> dict[str, str]:
    return {
        "id": lead.id,
        "score": str(lead.score),
        "score_band": lead.score_band,
        "tags": ",".join(lead.tags),
        "title": lead.title,
        "price_text": lead.price_text,
        "location": lead.location,
        "posted_at": lead.posted_at,
        "seller_name": lead.seller_name,
        "source_type": lead.source_type,
        "source_url": lead.source_url,
        "capture_path": lead.capture_path,
        "score_reasons": " | ".join(lead.score_reasons),
        "review_status": lead.review_status,
        "review_notes": lead.review_notes,
    }


def _html_row(lead: LeadCandidate) -> str:
    title = html.escape(lead.title)
    body = html.escape(lead.body_text)
    details = html.escape(
        " | ".join(
            item
            for item in [
                lead.price_text,
                lead.location,
                lead.posted_at,
                lead.seller_name,
                lead.source_type,
                ", ".join(lead.tags),
            ]
            if item
        )
    )
    reasons = html.escape("; ".join(lead.score_reasons))
    capture_path = html.escape(lead.capture_path, quote=True)
    source = html.escape(lead.source_url, quote=True)
    source_link = f'<br><a href="{source}" target="_blank">source</a>' if source else ""
    return (
        f'<tr><td>{lead.score}</td>'
        f'<td class="band-{html.escape(lead.score_band)}">{html.escape(lead.score_band)}</td>'
        f'<td><strong>{title}</strong><br><small>{body}</small></td>'
        f'<td>{details}</td>'
        f'<td>{reasons}</td>'
        f'<td><a href="{capture_path}">{capture_path}</a>{source_link}</td></tr>'
    )


def print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))
