"""Notte-backed fallback for the Skool crawler.

Activated when:

    * the cookies.txt approach fails (Skool detects the requests UA, or
      the cookies have rotated), AND
    * ``NOTTE_API_KEY`` is set in the environment.

The Notte client drives a real Chromium in their cloud and gives us a
``scrape()`` call that returns structured data from any page. We use it to
walk the Skool classroom and pull out module/post URLs and video embeds.

We *do not* try to log in via Notte — that would require handing the
service your password, which violates the project's standing rule. We
*do* let you set the Skool session cookies into the Notte browser via
``NotteClient.sessions.set_cookies(...)`` so Notte inherits the session
that ``scripts/skool_login.py`` established (or any cookies.txt you drop
into the project).

To use:

    1. Sign up at https://console.notte.cc and grab an API key.
    2. ``cp .env.example .env`` and set ``NOTTE_API_KEY``.
    3. Run ``.venv/bin/python -m skool_ingest crawl ... --backend notte``.
    4. If Skool's login wall shows, the cookies are stale — re-run
       ``scripts/skool_login.py`` and try again.

Implementation notes:

    * Notte's ``Scrape`` action takes natural-language instructions and
      returns structured JSON via ``response_format``. We use that to
      extract (a) the list of module URLs from the classroom root and
      (b) the video URL(s) from each post page.
    * We deliberately do *not* ask the LLM to do anything clever with the
      transcript content — the goal is to *enumerate* videos, not to
      summarize them. A later stage hands URLs to transcript.lol.
    * Cost: each Notte scrape is metered. The classroom walk in this
      module makes 1 (root) + N (modules) + M (posts) calls. For a
      typical Skool group of 20 posts that's ~25 calls.
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from . import manifest

log = logging.getLogger("skool_ingest.crawl.notte")


def is_available() -> bool:
    """True iff Notte SDK is installed AND ``NOTTE_API_KEY`` is set."""
    if not os.environ.get("NOTTE_API_KEY"):
        return False
    try:
        import notte_sdk  # noqa: F401
        return True
    except ImportError:
        return False


def walk_classroom_notte(
    classroom_url: str,
    cookies_path: Path | None = None,
) -> Iterator[manifest.Row]:
    """Yield one ``Row`` per video in the Skool classroom via Notte.

    Steps:

        1. Open a Notte session. If ``cookies_path`` is given, set those
           cookies into the session so we inherit your Skool login.
        2. Scrape the classroom root with instructions to extract module
           URLs as JSON.
        3. For each module URL, scrape it to extract post URLs.
        4. For each post URL, scrape it to extract video URLs + title.
        5. Yield a ``Row`` per video.
    """
    from notte_sdk import NotteClient

    client = NotteClient(api_key=os.environ["NOTTE_API_KEY"])
    session = client.Session(perception_type="deep")  # deep = full DOM scrape

    with session:
        if cookies_path is not None and cookies_path.exists():
            log.info("loading cookies from %s into Notte session", cookies_path)
            _set_cookies(session, cookies_path)

        log.info("Notte: scraping classroom root %s", classroom_url)
        root = _scrape_json(
            session,
            classroom_url,
            instructions=(
                "Extract every module/category link on this page. A module "
                "link is an anchor whose href contains '/classroom' but is "
                "not a long alphanumeric id (those are individual posts). "
                "Return them as a JSON object: {\"modules\": [{\"href\": "
                "\"...\", \"text\": \"...\"}]}. Include the href exactly as "
                "it appears in the HTML. If the page shows posts directly "
                "without module links, return {\"modules\": []} and skip "
                "the post enumeration step in your caller."
            ),
        )
        modules = root.get("modules", [])
        log.info("Notte: found %d modules", len(modules))

        post_urls: list[str] = []
        if not modules:
            # Single-page classroom — try to enumerate post links on the root.
            post_urls.extend(_scrape_post_urls(session, classroom_url))
        else:
            for m in modules:
                module_href = m.get("href", "")
                module_url = _abs(classroom_url, module_href)
                if not module_url:
                    continue
                post_urls.extend(_scrape_post_urls(session, module_url))

        # de-dupe, preserve order
        seen: set[str] = set()
        deduped: list[str] = []
        for u in post_urls:
            if u not in seen:
                seen.add(u)
                deduped.append(u)
        log.info("Notte: found %d unique posts", len(deduped))

        for post_url in deduped:
            log.info("Notte: scraping post %s", post_url)
            post_data = _scrape_json(
                session,
                post_url,
                instructions=(
                    "Extract the post title and every video embedded in "
                    "this post. A video is an iframe whose src points to "
                    "loom.com / youtube.com / vimeo.com / youtu.be, OR a "
                    "<video>/<source> tag, OR a link whose href is a video "
                    "URL. Return JSON of the form: {\"title\": \"...\", "
                    "\"videos\": [{\"url\": \"...\", \"kind\": \"iframe|"
                    "video|anchor\"}]}. Use the full absolute URL for each "
                    "video. Include the post title exactly as it appears."
                ),
            )
            title = post_data.get("title") or post_url
            for v in post_data.get("videos", []):
                url = v.get("url", "")
                if not url:
                    continue
                embed = _detect_embed(url)
                yield manifest.Row(
                    post_url=post_url,
                    post_title=title,
                    post_author="",
                    post_date="",
                    video_url=url,
                    embed_type=embed,
                    reachable=_classify_reachable(embed),
                )


# --- helpers ---------------------------------------------------------------


def _set_cookies(session: Any, cookies_path: Path) -> None:
    """Push every cookie in a Netscape cookies.txt into the Notte session."""
    from .skool_crawl import load_netscape_cookies
    raw = load_netscape_cookies(cookies_path)
    if not raw:
        log.warning("no cookies parsed from %s", cookies_path)
        return
    # Notte's set_cookies expects a list of {name, value, domain, path} dicts.
    payload = [
        {
            "name": c["name"],
            "value": c["value"],
            "domain": c["domain"].lstrip("."),
            "path": c["path"],
        }
        for c in raw
    ]
    try:
        session.set_cookies(payload)
    except Exception as exc:
        log.warning("set_cookies failed (continuing without): %s", exc)


def _scrape_json(session: Any, url: str, *, instructions: str) -> dict[str, Any]:
    """Scrape ``url`` and ask Notte to return a JSON object matching the instructions."""
    try:
        response = session.scrape(
            url=url,
            instructions=instructions,
            response_format={"type": "json"},
        )
    except Exception as exc:
        log.error("Notte scrape failed for %s: %s", url, exc)
        return {}
    data = getattr(response, "data", None) or getattr(response, "structured", None)
    if data is None:
        return {}
    if isinstance(data, str):
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return {}
    if isinstance(data, dict):
        return data
    return {}


def _scrape_post_urls(session: Any, page_url: str) -> list[str]:
    data = _scrape_json(
        session,
        page_url,
        instructions=(
            "Extract every post link on this page. A post link is an "
            "anchor whose href matches the pattern /<group>/classroom/"
            "followed by a 6+ character alphanumeric id (e.g. /coliving-"
            "freedom-unlocked-5532/classroom/d38e0029). Return JSON: "
            "{\"posts\": [{\"href\": \"/...\"}]}. Use the href exactly "
            "as it appears, not the resolved URL."
        ),
    )
    out: list[str] = []
    for p in data.get("posts", []):
        href = p.get("href", "")
        if not href:
            continue
        full = _abs(page_url, href)
        if full:
            out.append(full)
    return out


def _abs(base: str, href: str) -> str | None:
    if not href:
        return None
    import urllib.parse
    return urllib.parse.urljoin(base, href)


def _detect_embed(url: str) -> str:
    """Re-export the embed detector from skool_crawl to keep this module self-contained."""
    from .skool_crawl import detect_embed
    return detect_embed(url)


def _classify_reachable(embed: str) -> str:
    """Same heuristic as skool_crawl._classify_reachable."""
    if embed in {"loom", "youtube", "vimeo", "mp4"}:
        return "yes"
    return "no"
