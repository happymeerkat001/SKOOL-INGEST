"""CLI entry point for the Facebook lead capture + triage harness."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from . import ingest
from . import report
from . import scoring


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
    ingest.print_json(
        {
            "ingest": ingest_summary,
            "score": score_summary,
            "report": report_summary,
        }
    )
    return 0


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
    pingest.add_argument(
        "--live",
        action="store_true",
        help="stub only: live Facebook capture is not implemented in v1",
    )
    pingest.set_defaults(func=cmd_ingest)

    pstatus = sub.add_parser("status", help="summarize the JSONL lead store")
    pstatus.add_argument("--leads", default="manifest/fb_leads/leads.jsonl", help="JSONL store")
    pstatus.set_defaults(func=cmd_status)

    pscore = sub.add_parser("score", help="score leads with deterministic local rules")
    pscore.add_argument("--leads", default="manifest/fb_leads/leads.jsonl", help="JSONL store")
    pscore.add_argument(
        "--only-unscored",
        action="store_true",
        help="skip leads whose score_band is already set",
    )
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
