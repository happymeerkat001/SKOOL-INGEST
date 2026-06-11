#!/usr/bin/env python3
from __future__ import annotations

import html as html_lib
import json
import re
from collections import OrderedDict
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import dotenv_values
from playwright.sync_api import Page, sync_playwright

from skool_ingest.manifest import Row, save

BASE = "https://www.skool.com/coliving-freedom-unlocked-5532/classroom"
COURSE_RE = re.compile(
    r'\{"id":"([a-f0-9]{32})","name":"([A-Za-z0-9_-]+)","metadata":\{.*?"title":"(.*?)"\},"createdAt":.*?"unitType":"course","rootId":"([a-f0-9]{32})"',
    re.S,
)
POST_OBJ_RE = re.compile(
    r'\{"course":\{"id":"([a-f0-9]{32})","name":"([A-Za-z0-9_-]+)","metadata":\{.*?"title":"(.*?)"(?:(?:.*?"videoLink":"(.*?)")|(?:.*?"videoId":"([a-f0-9]{32})"))?.*?\},"createdAt":.*?"unitType":"module","parentId":"([a-f0-9]{32})"',
    re.S,
)
POST_LINK_RE = re.compile(r'/classroom/([A-Za-z0-9_-]{8})\?md=([a-f0-9]{32})$')


def infer_embed_type(url: str) -> str:
    u = url.lower()
    if 'youtube.com' in u or 'youtu.be' in u:
        return 'youtube'
    if 'loom.com' in u:
        return 'loom'
    if 'vimeo.com' in u or 'vimeocdn.com' in u:
        return 'vimeo'
    if 'stream.video.skool.com' in u or u.endswith('.m3u8'):
        return 'm3u8'
    if 'mux.com' in u:
        return 'mux'
    if u.endswith('.mp4'):
        return 'mp4'
    return 'other'


def uniq_by(items, key_fn):
    out = OrderedDict()
    for item in items:
        out[key_fn(item)] = item
    return list(out.values())


def resolve_skool_stream(page: Page, post_url: str) -> str:
    seen: list[str] = []

    def on_response(resp):
        u = resp.url
        ul = u.lower()
        if ('stream.video.skool.com' in ul and '.m3u8' in ul) or ('manifest-' in ul and 'rendition.m3u8' in ul):
            seen.append(u)

    page.on('response', on_response)
    try:
        page.goto(post_url, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_timeout(4000)
        for sel in [
            'div[class*="MuxThumbnailWrapper"]',
            'div[class*="ThumbnailImage"]',
            'svg',
        ]:
            loc = page.locator(sel).first
            try:
                if loc.count() > 0:
                    loc.click(force=True, timeout=5000)
                    break
            except Exception:
                continue
        page.wait_for_timeout(8000)
    finally:
        try:
            page.remove_listener('response', on_response)
        except Exception:
            pass
    for url in seen:
        if 'stream.video.skool.com' in url:
            return url
    return seen[0] if seen else ''


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    cfg = dotenv_values(root / '.env')
    email = cfg.get('SKOOL_USERNAME') or cfg.get('SKOOL_Username')
    password = cfg.get('SKOOL_PASSWORD') or cfg.get('SKOOL_Password')
    classroom = cfg.get('SKOOL_CLASSROOM_URL') or BASE
    if not email or not password:
        raise SystemExit('missing SKOOL_USERNAME/SKOOL_PASSWORD in .env')

    manifest_path = root / 'manifest' / 'skool_videos.csv'
    unresolved_path = root / 'manifest' / 'skool_videos_unresolved.json'

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()

        page.goto('https://www.skool.com/login', wait_until='domcontentloaded', timeout=60000)
        page.fill('input[type="email"]', str(email))
        page.fill('input[type="password"]', str(password))
        page.click('button[type="submit"]')
        page.wait_for_timeout(8000)
        page.goto(classroom, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_timeout(5000)

        root_html = page.content()
        course_objs = []
        for course_id, short_name, title, root_id in COURSE_RE.findall(root_html):
            if course_id == 'eef8ff5f21cd4080b529ed13bc7a2187':
                continue
            course_objs.append({
                'course_id': course_id,
                'short_name': short_name,
                'title': html_lib.unescape(title).replace('\\u0026', '&'),
                'root_id': root_id,
            })
        course_objs = uniq_by(course_objs, lambda x: x['short_name'])

        rows: list[Row] = []
        unresolved: list[dict] = []

        for course in course_objs:
            course_url = f"{classroom}/{course['short_name']}"
            page.goto(course_url, wait_until='domcontentloaded', timeout=60000)
            page.wait_for_timeout(4000)
            html = page.content()

            link_items = page.locator('a').evaluate_all(
                "els => els.map(a => ({href:a.href, text:(a.innerText||'').trim()})).filter(x => x.href)"
            )
            post_links = []
            for item in link_items:
                href = item['href']
                text = (item.get('text') or '').strip()
                if POST_LINK_RE.search(href):
                    qs = parse_qs(urlparse(href).query)
                    md = (qs.get('md') or [''])[0]
                    post_links.append({'href': href, 'text': text, 'md': md})
            post_links = uniq_by(post_links, lambda x: x['href'])
            posts_by_md = {x['md']: x for x in post_links if x['md']}
            posts_by_title = {x['text']: x for x in post_links if x['text']}

            obj_rows = []
            for post_id, _short, title, video_link, video_id, parent_id in POST_OBJ_RE.findall(html):
                if parent_id != course['root_id']:
                    continue
                title_clean = html_lib.unescape(title).replace('\\u0026', '&')
                obj_rows.append({
                    'post_id': post_id,
                    'title': title_clean,
                    'video_link': video_link or '',
                    'video_id': video_id or '',
                    'parent_id': parent_id,
                })
            obj_rows = uniq_by(obj_rows, lambda x: x['post_id'])

            for obj in obj_rows:
                post = posts_by_md.get(obj['post_id']) or posts_by_title.get(obj['title'])
                if not post and obj['post_id'] == course['root_id']:
                    post = {'href': course_url, 'text': obj['title'], 'md': obj['post_id']}
                if not post:
                    unresolved.append({
                        'reason': 'no_matching_post_link',
                        'course': course,
                        'object': obj,
                    })
                    continue

                video_url = obj['video_link']
                if not video_url and obj['video_id']:
                    video_url = resolve_skool_stream(page, post['href'])

                if video_url:
                    rows.append(Row(
                        post_url=post['href'],
                        post_title=obj['title'],
                        post_author='',
                        post_date='',
                        video_url=video_url,
                        embed_type=infer_embed_type(video_url),
                        reachable='yes',
                    ))
                else:
                    unresolved.append({
                        'reason': 'video_unresolved_after_click',
                        'course': course,
                        'post': post,
                        'object': obj,
                    })

        browser.close()

    rows = uniq_by(rows, lambda r: r.id)
    save(manifest_path, rows)
    unresolved_path.write_text(json.dumps(unresolved, indent=2), encoding='utf-8')

    print(f'courses={len(course_objs)}')
    print(f'rows_written={len(rows)}')
    print(f'unresolved={len(unresolved)}')
    print(f'manifest={manifest_path}')
    print(f'unresolved_json={unresolved_path}')
    if rows:
        counts = OrderedDict()
        for r in rows:
            counts[r.embed_type] = counts.get(r.embed_type, 0) + 1
        print('embed_counts=' + json.dumps(counts))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())