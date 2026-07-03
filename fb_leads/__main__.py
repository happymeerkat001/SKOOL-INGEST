"""CLI entry point for the Facebook lead capture + triage harness."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import draft_report
from . import drafts
from . import ingest
from . import post_templates
from . import report
from . import scoring
from .models import load as load_leads


def cmd_ingest(args: argparse.Namespace) -> int:
    leads_path = Path(args.leads)
    if args.live:
        return ingest.refuse_live_capture(leads_path)
    summary = ingest.ingest_captures(Path(args.captures), leads_path)
    ingest.print_json(summary)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    ingest.print_json(ingest.status_summary(Path(args.leads)))
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    summary = scoring.score_store(Path(args.leads), only_unscored=args.only_unscored)
    ingest.print_json(summary)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    summary = report.generate_report(Path(args.leads), Path(args.out_dir))
    ingest.print_json(summary)
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    summary = report.sync_review_csv(Path(args.csv), Path(args.leads))
    ingest.print_json(summary)
    return report.sync_exit_code(summary)


def cmd_run(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    leads_path = out_dir / "leads.jsonl"
    ingest_summary = ingest.ingest_captures(Path(args.captures), leads_path)
    score_summary = scoring.score_store(leads_path)
    report_summary = report.generate_report(leads_path, out_dir)
    ingest.print_json({"ingest": ingest_summary, "score": score_summary, "report": report_summary})
    return 0


def cmd_draft_add(args: argparse.Namespace) -> int:
    try:
        draft = _build_draft_from_args(args)
    except (ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    rows = drafts.upsert(Path(args.drafts), draft)
    ingest.print_json({"created": draft.id, "total": len(rows), "drafts_path": args.drafts})
    return 0


def _build_draft_from_args(args: argparse.Namespace) -> drafts.PostDraft:
    zone_name = args.tz or drafts.default_timezone_name()
    existing = drafts.load(Path(args.drafts))
    scheduled_for = (
        drafts.normalize_explicit_at(args.at, zone_name)
        if args.at
        else drafts.next_slot(existing.values(), zone_name)
    )
    lead_ids: list[str] = []
    lead_slots: dict[str, str] = {}
    if args.from_lead:
        leads = load_leads(Path(args.leads))
        if args.from_lead not in leads:
            raise ValueError(f"unknown lead id: {args.from_lead}")
        lead = leads[args.from_lead]
        if lead.review_status != "approved" and not args.allow_unapproved:
            raise ValueError(f"lead is not approved: {args.from_lead}")
        lead_ids = [lead.id]
        lead_slots = {
            "price": lead.price_text,
            "location": lead.location,
            "room_desc": lead.title,
        }
    copy_text = _copy_text_from_args(args, lead_slots)
    return drafts.PostDraft(
        topic=args.topic,
        lead_ids=lead_ids,
        template_id=args.template if not (args.copy or args.copy_file) else "",
        title=args.title or args.topic,
        copy_text=copy_text,
        price_text=args.price or lead_slots.get("price", ""),
        location=args.location or lead_slots.get("location", ""),
        images_note=args.images_note,
        target_surface=args.surface,
        scheduled_for=scheduled_for,
        timezone=zone_name,
    )


def _copy_text_from_args(args: argparse.Namespace, lead_slots: dict[str, str]) -> str:
    if args.copy_file:
        return Path(args.copy_file).read_text(encoding="utf-8")
    if args.copy:
        return args.copy
    if args.template:
        slots = {
            "price": args.price or lead_slots.get("price", ""),
            "location": args.location or lead_slots.get("location", ""),
            "room_desc": args.room_desc or lead_slots.get("room_desc", ""),
            "move_in": args.move_in or "",
        }
        try:
            return post_templates.render(args.template, slots)
        except KeyError as exc:
            raise KeyError(str(exc).strip('"')) from exc
    raise ValueError("provide --copy, --copy-file, or --template")


def cmd_draft_list(args: argparse.Namespace) -> int:
    ingest.print_json(drafts.status_summary(Path(args.drafts)))
    return 0


def cmd_draft_export(args: argparse.Namespace) -> int:
    summary = draft_report.generate_queue(Path(args.drafts), Path(args.out_dir))
    ingest.print_json(summary)
    return 0


def cmd_draft_sync(args: argparse.Namespace) -> int:
    summary = draft_report.sync_queue_csv(Path(args.csv), Path(args.drafts))
    ingest.print_json(summary)
    return draft_report.sync_exit_code(summary)


def cmd_draft_publish(args: argparse.Namespace) -> int:
    _ = args
    return drafts.refuse_publish()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    parser = argparse.ArgumentParser(prog="fb_leads")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pingest = sub.add_parser("ingest", help="ingest saved Facebook captures into JSONL")
    pingest.add_argument("--captures", default="captures/fb", help="folder of saved captures")
    pingest.add_argument("--leads", default="manifest/fb_leads/leads.jsonl", help="JSONL store")
    pingest.add_argument("--live", action="store_true", help="stub only: live Facebook capture is not implemented in v1")
    pingest.set_defaults(func=cmd_ingest)

    pstatus = sub.add_parser("status", help="summarize the JSONL lead store")
    pstatus.add_argument("--leads", default="manifest/fb_leads/leads.jsonl", help="JSONL store")
    pstatus.set_defaults(func=cmd_status)

    pscore = sub.add_parser("score", help="score leads with deterministic local rules")
    pscore.add_argument("--leads", default="manifest/fb_leads/leads.jsonl", help="JSONL store")
    pscore.add_argument("--only-unscored", action="store_true", help="skip leads whose score_band is already set")
    pscore.set_defaults(func=cmd_score)

    preport = sub.add_parser("report", help="write review_queue.csv and review.html")
    preport.add_argument("--leads", default="manifest/fb_leads/leads.jsonl", help="JSONL store")
    preport.add_argument("--out-dir", default="manifest/fb_leads", help="report output dir")
    preport.set_defaults(func=cmd_report)

    psync = sub.add_parser("sync", help="merge review CSV edits back into JSONL")
    psync.add_argument("--csv", default="manifest/fb_leads/review_queue.csv", help="review CSV")
    psync.add_argument("--leads", default="manifest/fb_leads/leads.jsonl", help="JSONL store")
    psync.set_defaults(func=cmd_sync)

    prun = sub.add_parser("run", help="run ingest + score + report in one dry-run pipeline")
    prun.add_argument("--captures", default="captures/fb", help="folder of saved captures")
    prun.add_argument("--out-dir", default="manifest/fb_leads", help="pipeline output dir")
    prun.set_defaults(func=cmd_run)

    pdraft_add = sub.add_parser("draft-add", help="create a posting draft")
    pdraft_add.add_argument("--topic", required=True)
    pdraft_add.add_argument("--template", default="")
    pdraft_add.add_argument("--copy", default="")
    pdraft_add.add_argument("--copy-file", default="")
    pdraft_add.add_argument("--from-lead", default="")
    pdraft_add.add_argument("--leads", default="manifest/fb_leads/leads.jsonl")
    pdraft_add.add_argument("--allow-unapproved", action="store_true")
    pdraft_add.add_argument("--price", default="")
    pdraft_add.add_argument("--location", default="")
    pdraft_add.add_argument("--room-desc", default="")
    pdraft_add.add_argument("--move-in", default="")
    pdraft_add.add_argument("--surface", default="other")
    pdraft_add.add_argument("--title", default="")
    pdraft_add.add_argument("--images-note", default="")
    pdraft_add.add_argument("--at", default="", help="explicit scheduled_for datetime")
    pdraft_add.add_argument("--tz", default="", help="IANA timezone name")
    pdraft_add.add_argument("--drafts", default="manifest/fb_leads/post_drafts.jsonl")
    pdraft_add.set_defaults(func=cmd_draft_add)

    pdraft_list = sub.add_parser("draft-list", help="summarize posting draft queue")
    pdraft_list.add_argument("--drafts", default="manifest/fb_leads/post_drafts.jsonl")
    pdraft_list.set_defaults(func=cmd_draft_list)

    pdraft_export = sub.add_parser("draft-export", help="export post_queue CSV/HTML/Markdown")
    pdraft_export.add_argument("--drafts", default="manifest/fb_leads/post_drafts.jsonl")
    pdraft_export.add_argument("--out-dir", default="manifest/fb_leads")
    pdraft_export.set_defaults(func=cmd_draft_export)

    pdraft_sync = sub.add_parser("draft-sync", help="merge post_queue.csv checklist edits")
    pdraft_sync.add_argument("--csv", default="manifest/fb_leads/post_queue.csv")
    pdraft_sync.add_argument("--drafts", default="manifest/fb_leads/post_drafts.jsonl")
    pdraft_sync.set_defaults(func=cmd_draft_sync)

    pdraft_publish = sub.add_parser("draft-publish", help="refusal stub: no auto-posting in v1")
    pdraft_publish.add_argument("--drafts", default="manifest/fb_leads/post_drafts.jsonl")
    pdraft_publish.add_argument("--i-understand-official-api", action="store_true")
    pdraft_publish.set_defaults(func=cmd_draft_publish)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
