"""CLI entry point for skool-ingest.

Usage (from inside the repo, with the venv active):

    # 1. Walk the Skool classroom and write the manifest (requires you to
    #    fill in ``walk_classroom`` first — see skool_crawl.py docstring).
    python -m skool_ingest crawl \
        --classroom-url "https://www.skool.com/coliving-freedom-unlocked-5532/classroom" \
        --cookies ./cookies/skool.txt \
        --out manifest/skool_videos.csv

    # 2. Fan out the manifest to transcript.lol.
    python -m skool_ingest fanout --manifest manifest/skool_videos.csv

    # 3. Dry-run summary of the current manifest.
    python -m skool_ingest status --manifest manifest/skool_videos.csv

The crawler command will fail loudly with a clear ``NotImplementedError``
until you implement ``walk_classroom`` — that's intentional. The
``fanout`` and ``status`` commands work against any pre-existing manifest.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from . import fanout, manifest
from .transcript_lol import TranscriptLol, TranscriptLolError


def cmd_crawl(args: argparse.Namespace) -> int:
    cookies = Path(args.cookies) if args.cookies else None
    if args.backend == "notte":
        from . import skool_crawl_notte
        if not skool_crawl_notte.is_available():
            print(
                "Notte backend requested but NOTTE_API_KEY is unset or "
                "notte-sdk is not installed. Set NOTTE_API_KEY in .env and "
                "run: .venv/bin/pip install notte-sdk",
                file=sys.stderr,
            )
            return 2
        out = Path(args.out)
        seen: dict[str, manifest.Row] = manifest.load(out)
        for row in skool_crawl_notte.walk_classroom_notte(
            args.classroom_url, cookies_path=cookies,
        ):
            seen[row.id] = row
            manifest.upsert(out, row)
        print(f"wrote {len(seen)} rows to {out} (via Notte)")
        return 0

    # Default backend: cookies.txt + requests
    from . import skool_crawl
    if cookies is None or not cookies.exists():
        print(f"cookies file not found: {cookies}", file=sys.stderr)
        print("export one from your logged-in Skool browser first", file=sys.stderr)
        return 2
    out = Path(args.out)
    seen: dict[str, manifest.Row] = manifest.load(out)
    for row in skool_crawl.walk_classroom(cookies, args.classroom_url):
        seen[row.id] = row
        manifest.upsert(out, row)
    print(f"wrote {len(seen)} rows to {out}")
    return 0


def cmd_fanout(args: argparse.Namespace) -> int:
    client = TranscriptLol()
    counts = fanout.run(
        Path(args.manifest),
        client,
        sleep_between=args.sleep,
    )
    print(json.dumps(counts, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    rows = manifest.load(Path(args.manifest))
    if not rows:
        print("manifest is empty (or missing)")
        return 0
    by_status = Counter(r.status for r in rows.values())
    by_embed = Counter(r.embed_type for r in rows.values())
    by_reachable = Counter(r.reachable for r in rows.values())
    print(f"total rows: {len(rows)}")
    print(f"by status:   {dict(by_status)}")
    print(f"by embed:    {dict(by_embed)}")
    print(f"reachable:   {dict(by_reachable)}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    """Render every capturable manifest row into the Obsidian vault."""
    from . import vault
    rows = manifest.load(Path(args.manifest))
    if not rows:
        print("manifest is empty (or missing); nothing to render")
        return 0
    vault_dir = Path(args.vault_dir).expanduser()
    if not vault_dir.exists():
        print(f"vault dir does not exist: {vault_dir}", file=sys.stderr)
        return 2
    captures = vault.render_all(
        vault_dir,
        rows.values(),
        include_failed=args.include_failed,
    )
    print(f"wrote {len(captures)} capture files + MOC into {vault_dir / 'Skool Ingest'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    p = argparse.ArgumentParser(prog="skool_ingest")
    sub = p.add_subparsers(dest="cmd", required=True)

    pcrawl = sub.add_parser("crawl", help="walk the Skool classroom → manifest")
    pcrawl.add_argument("--classroom-url", required=True)
    pcrawl.add_argument(
        "--cookies",
        default=None,
        help="path to Netscape cookies.txt; required for default backend, "
        "optional but recommended for --backend notte",
    )
    pcrawl.add_argument(
        "--backend",
        choices=["cookies", "notte"],
        default="cookies",
        help="crawler backend; 'notte' uses Notte's cloud browser (set "
        "NOTTE_API_KEY in .env). Default: cookies (local).",
    )
    pcrawl.add_argument("--out", default="manifest/skool_videos.csv")
    pcrawl.set_defaults(func=cmd_crawl)

    pfan = sub.add_parser("fanout", help="submit manifest → transcript.lol")
    pfan.add_argument("--manifest", default="manifest/skool_videos.csv")
    pfan.add_argument("--sleep", type=float, default=1.0, help="seconds between submits")
    pfan.set_defaults(func=cmd_fanout)

    pstat = sub.add_parser("status", help="summarize the current manifest")
    pstat.add_argument("--manifest", default="manifest/skool_videos.csv")
    pstat.set_defaults(func=cmd_status)

    prend = sub.add_parser("render", help="render the manifest into the Obsidian vault")
    prend.add_argument("--manifest", default="manifest/skool_videos.csv")
    prend.add_argument(
        "--vault-dir",
        default="~/Library/Mobile Documents/iCloud~md~obsidian/Documents/AI-Vault",
        help="path to the Obsidian vault root",
    )
    prend.add_argument(
        "--include-failed",
        action="store_true",
        help="also write captures for rows that failed transcript.lol submission",
    )
    prend.set_defaults(func=cmd_render)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except TranscriptLolError as exc:
        print(f"transcript.lol error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
