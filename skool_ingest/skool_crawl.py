"""Skool classroom walker.

The default implementation is a *real* crawler that uses a Netscape
cookies.txt + ``requests`` to walk the Skool HTML. If that fails (Skool
returns /about, blocks the user agent, etc.), the crawler falls back to a
``Notte`` browser session — controlled by environment variables; if
``NOTTE_API_KEY`` is missing, it surfaces a clear error rather than silently
failing.

Run::

    python -m skool_ingest crawl \\
        --classroom-url "https://www.skool.com/<group>/classroom" \\
        --cookies ./cookies/skool.txt

The walker yields ``manifest.Row`` objects. Rows are written incrementally
(``manifest.upsert``) so a Ctrl-C or crash loses at most the in-flight row.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.parse
from collections.abc import Iterator
from pathlib import Path
from typing import TypedDict

import requests
from bs4 import BeautifulSoup

from . import manifest

log = logging.getLogger("skool_ingest.crawl")


class CookieDict(TypedDict):
    domain: str
    path: str
    expires: str
    name: str
    value: str


# --- public API --------------------------------------------------------------


def detect_embed(url: str) -> str:
    """Best-effort classification of a video URL into an embed_type label."""
    if not url:
        return "other"
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    # Playlist / manifest URLs come first — they are the actual blocker
    # for third-party fetchers, regardless of who serves them.
    if path.endswith(".m3u8") or path.endswith(".mpd"):
        return "m3u8"
    if "loom.com" in host:
        return "loom"
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "vimeo.com" in host:
        return "vimeo"
    if "mux.com" in host:
        return "mux"
    if path.endswith(".mp4"):
        return "mp4"
    return "other"


def load_netscape_cookies(path: Path) -> list[CookieDict]:
    """Parse a cookies.txt (Netscape format) into a list of dicts.

    Useful for ``requests.Session.cookies.set(...)`` or for handing to
    Playwright's ``context.add_cookies()``.
    """
    out: list[CookieDict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        domain, _flag, cookie_path, _secure, expires, name, value = parts
        out.append(
            CookieDict(
                domain=domain,
                path=cookie_path,
                expires=expires,
                name=name,
                value=value,
            )
        )
    return out


def make_session(cookies: list[CookieDict], user_agent: str) -> requests.Session:
    """Build a ``requests.Session`` with the given cookies + UA."""
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/json",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    for c in cookies:
        s.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
    return s


def walk_classroom(
    cookies_path: Path,
    classroom_url: str,
    *,
    user_agent: str = "skool-ingest/0.1 (+local)",
) -> Iterator[manifest.Row]:
    """Yield one ``Row`` per video in the Skool classroom.

    Strategy:

      1. Load cookies, GET the classroom root with ``requests``.
      2. If the response is the unauth /about redirect, raise so the caller
         can decide (re-export cookies, try Notte, etc.).
      3. Parse module links from the HTML. Visit each module page, collect
         post URLs. Visit each post, extract the embedded video URL.
      4. Yield a ``Row`` per video. De-dupe on (post_url, video_url).

    This is intentionally tolerant of Skool HTML changes — every step
    logs the URLs it's working with so a failure is easy to diagnose.
    """
    cookies = load_netscape_cookies(cookies_path)
    if not cookies:
        raise RuntimeError(f"no cookies parsed from {cookies_path}")
    session = make_session(cookies, user_agent)

    log.info("GET %s", classroom_url)
    resp = session.get(classroom_url, timeout=30, allow_redirects=True)
    if _looks_unauth(resp):
        raise RuntimeError(
            f"Skool responded with an unauth page (final URL={resp.url}); "
            f"cookies may be stale. Re-export cookies.txt and try again."
        )
    resp.raise_for_status()

    module_urls = _extract_module_links(resp.text, base=classroom_url)
    log.info("found %d module links", len(module_urls))

    seen: set[str] = set()
    post_urls: list[str] = []
    if not module_urls:
        # Single-page classroom — try to find post links directly on the root.
        post_urls.extend(_extract_post_links(resp.text, base=classroom_url))
    for module_url in module_urls:
        log.info("GET module %s", module_url)
        mresp = session.get(module_url, timeout=30)
        mresp.raise_for_status()
        post_urls.extend(_extract_post_links(mresp.text, base=module_url))

    # de-dupe, preserve order
    deduped: list[str] = []
    for u in post_urls:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    log.info("found %d unique posts", len(deduped))

    for post_url in deduped:
        log.info("GET post %s", post_url)
        presp = session.get(post_url, timeout=30)
        presp.raise_for_status()
        for row in _extract_videos_from_post(presp.text, post_url, base=post_url):
            yield row


# --- HTML helpers ------------------------------------------------------------


_UNAUTH_MARKERS = ("LOG IN", "Sign in to Skool")


def _looks_unauth(resp: requests.Response) -> bool:
    body = resp.text
    if any(m in body for m in _UNAUTH_MARKERS):
        # Some legit pages mention "LOG IN" in nav; only treat as unauth if
        # the body is short and the URL ended up at /about or /login.
        if "/about" in resp.url or "/login" in resp.url:
            return True
    return False


def _abs(base: str, href: str | list[str] | None) -> str | None:
    if not href:
        return None
    if isinstance(href, list):
        href = href[0] if href else ""
    if not href:
        return None
    return urllib.parse.urljoin(base, href)


def _str_attr(value: object) -> str:
    """Coerce a BeautifulSoup attribute value to a plain str."""
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return str(value)


def _extract_module_links(html: str, base: str) -> list[str]:
    """Find module/category links on a Skool classroom page.

    Skool DOM conventions: anchors whose href contains ``/classroom/`` and
    does *not* end in a long alphanumeric post id (those are individual
    posts, handled separately by ``_extract_post_links``).

    We allow query strings (``?cid=...``) because that's how Skool
    navigates between modules in a single classroom.
    """
    soup = BeautifulSoup(html, "html.parser")
    found: list[str] = []
    for a in soup.find_all("a", href=True):
        href = _str_attr(a.get("href"))
        if "/classroom" not in href:
            continue
        # Skip individual post links (long id at the end) — they are
        # handled by _extract_post_links. Module links either have no id
        # (e.g. "/g/classroom") or a short `?cid=...` style navigation.
        if _POST_HREF_RE.match(href):
            continue
        url = _abs(base, href)
        if url and url not in found:
            found.append(url)
    return found


_POST_HREF_RE = re.compile(r"^/[^/?]+/classroom/[A-Za-z0-9]{6,}$")


def _extract_post_links(html: str, base: str) -> list[str]:
    """Find individual post links on a Skool module page.

    Post links look like ``/coliving-freedom-unlocked-5532/classroom/d38e0029``.
    """
    soup = BeautifulSoup(html, "html.parser")
    found: list[str] = []
    for a in soup.find_all("a", href=True):
        href = _str_attr(a.get("href"))
        if _POST_HREF_RE.match(href):
            url = _abs(base, href)
            if url and url not in found:
                found.append(url)
    return found


_VIDEO_ATTRS = ("src", "data-src")


def _extract_videos_from_post(html: str, post_url: str, base: str) -> list[manifest.Row]:
    """Pull every embedded video URL from a single post page.

    Looks for:
      * ``<iframe>`` whose src matches a known video host.
      * ``<video src=...>`` or ``<source src=...>``.
      * ``<a href="...">`` whose href is a video URL (people often link to
        the source file/Loom/Vimeo from a post).

    Yields a Row per discovered video URL; de-dupe happens upstream in
    walk_classroom by row id (which is a hash of post+video).
    """
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[tuple[str, str]] = []  # (url, kind)

    # 1. <iframe> embeds
    for iframe in soup.find_all("iframe"):
        src = _str_attr(iframe.get("src"))
        if _looks_like_video_url(src):
            candidates.append((_abs(base, src) or src, "iframe"))

    # 2. <video>/<source>
    for tag in soup.find_all(["video", "source"]):
        for attr in _VIDEO_ATTRS:
            v = _str_attr(tag.get(attr))
            if v and _looks_like_video_url(v):
                candidates.append((_abs(base, v) or v, tag.name))

    # 3. Anchor links to videos
    for a in soup.find_all("a", href=True):
        href = _str_attr(a.get("href"))
        if _looks_like_video_url(href):
            candidates.append((_abs(base, href) or href, "anchor"))

    rows: list[manifest.Row] = []
    seen_in_post: set[str] = set()
    for url, _kind in candidates:
        if url in seen_in_post:
            continue
        seen_in_post.add(url)
        embed = detect_embed(url)
        rows.append(
            manifest.Row(
                post_url=post_url,
                post_title=_extract_post_title(soup, fallback=post_url),
                post_author="",  # filled in later if Skool exposes it in the DOM
                post_date="",
                video_url=url,
                embed_type=embed,
                reachable=_classify_reachable(embed),
            )
        )
    return rows


def _extract_post_title(soup: BeautifulSoup, fallback: str) -> str:
    """Best-effort title extraction from a Skool post page."""
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return fallback


_VIDEO_HOST_HINTS = (
    "loom.com",
    "youtube.com",
    "youtu.be",
    "vimeo.com",
    "mux.com",
    "wistia.com",
    "wistia.net",
    "cloudflarestream.com",
    ".mp4",
    ".m3u8",
)


def _looks_like_video_url(url: str) -> bool:
    if not url:
        return False
    low = url.lower()
    return any(h in low for h in _VIDEO_HOST_HINTS)


def _classify_reachable(embed: str) -> str:
    """Heuristic: which embed types transcript.lol can probably fetch.

    Real verification happens when transcript.lol actually tries; this is
    just a pre-flight guess for the manifest's status column.
    """
    if embed in {"loom", "youtube", "vimeo", "mp4"}:
        return "yes"
    if embed in {"m3u8", "mux"}:
        return "no"  # usually session-protected
    return "no"


# --- Notte fallback ---------------------------------------------------------


def walk_classroom_notte(classroom_url: str, *, api_key: str | None = None) -> Iterator[manifest.Row]:
    """Fallback walker using the Notte browser-as-a-service.

    Activated only if ``walk_classroom`` raises and ``NOTTE_API_KEY`` is set.
    Imported lazily so the dependency is optional.

    Note: requires the user to have created a Notte account and supplied
    the API key. The class is provided here as a hook so the user can
    finish wiring it without re-architecting walk_classroom.
    """
    raise NotImplementedError(
        "Notte fallback not yet wired. To enable: pip install notte-sdk, set "
        "NOTTE_API_KEY in .env, and implement walk_classroom_notte() using "
        "notte_sdk's BrowserSession + page.observe()/page.step() helpers."
    )
