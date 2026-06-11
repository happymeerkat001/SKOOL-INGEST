"""Tests for the HTML extraction helpers in skool_crawl.

These do not hit the network. They feed canned HTML to the parsers and
check the resulting Row objects. The canned HTML mimics the structural
shape of a real logged-in Skool page so the tests will catch
regressions if Skool renames their CSS classes or restructures DOM.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skool_ingest import manifest  # noqa: E402
from skool_ingest.skool_crawl import (  # noqa: E402
    _extract_module_links,
    _extract_module_links_from_next_data,
    _extract_post_links,
    _extract_videos_from_post,
    detect_embed,
    load_netscape_cookies,
)


CLASSROOM_HTML = """
<html><body>
  <a href="/coliving-freedom-unlocked-5532/classroom">All</a>
  <a href="/coliving-freedom-unlocked-5532/classroom?cid=mod1">Module 1</a>
  <a href="/coliving-freedom-unlocked-5532/classroom?cid=mod2">Module 2</a>
</body></html>
"""


CLASSROOM_HTML_NEXT_DATA = """
<html><body>
  <script id="__NEXT_DATA__" type="application/json">
    {
      "props": {
        "pageProps": {
          "allCourses": [
            {"name": "5fbdb5d1"},
            {"name": "4437f016"},
            {"name": "5fbdb5d1"}
          ]
        }
      }
    }
  </script>
</body></html>
"""


MODULE_HTML = """
<html><body>
  <a href="/coliving-freedom-unlocked-5532/classroom/d38e0029">Post 1</a>
  <a href="/coliving-freedom-unlocked-5532/classroom/abc12345?md=170d705f957a453aa06419e835aeb8ff">Post 2</a>
  <a href="/coliving-freedom-unlocked-5532/classroom/zzz99999?md=3e91bd68f4d2405ab3c0c256360f64e9">Post 3</a>
</body></html>
"""


POST_HTML_LOOM = """
<html><head><title>Intro to coliving</title></head>
<body>
  <h1>Intro to coliving</h1>
  <iframe src="https://www.loom.com/embed/abcdef1234567890" allowfullscreen></iframe>
  <p>Some text below the video.</p>
</body></html>
"""


POST_HTML_MIXED = """
<html><head><title>Mixed embeds</title></head>
<body>
  <h1>Mixed embeds</h1>
  <iframe src="https://player.vimeo.com/video/987654321"></iframe>
  <video src="https://cdn.example.com/clip.mp4" controls></video>
  <a href="https://youtu.be/dQw4w9WgXcQ">Watch on YouTube</a>
  <a href="https://skool.com/coliving-freedom-unlocked-5532/classroom/next-post">Next</a>
</body></html>
"""


POST_HTML_MUX_M3U8 = """
<html><body>
  <h1>Session-protected recording</h1>
  <video>
    <source src="https://stream.mux.com/abc.m3u8">
  </video>
</body></html>
"""


POST_HTML_NEXT_DATA_YOUTUBE = """
<html><body>
  <script id="__NEXT_DATA__" type="application/json">
    {
      "props": {
        "pageProps": {
          "selectedModule": "mod-2",
          "course": {
            "children": [
              {
                "course": {
                  "id": "mod-1",
                  "metadata": {"title": "Other", "videoLink": "https://youtu.be/ignoreme"}
                }
              },
              {
                "course": {
                  "id": "mod-2",
                  "metadata": {
                    "title": "Next Data YouTube",
                    "videoLink": "https://youtu.be/abc123xyz"
                  }
                }
              }
            ]
          }
        }
      }
    }
  </script>
</body></html>
"""


POST_HTML_NEXT_DATA_MUX = """
<html><body>
  <script id="__NEXT_DATA__" type="application/json">
    {
      "props": {
        "pageProps": {
          "selectedModule": "mod-3",
          "course": {
            "children": [
              {
                "course": {
                  "id": "mod-3",
                  "metadata": {
                    "title": "Next Data Mux",
                    "videoId": "video-123"
                  }
                }
              }
            ]
          },
          "video": {
            "id": "video-123",
            "playbackId": "muxplaybackid",
            "playbackToken": "muxtoken"
          }
        }
      }
    }
  </script>
</body></html>
"""


def test_extract_module_links_finds_query_string_modules():
    urls = _extract_module_links(CLASSROOM_HTML, "https://www.skool.com/coliving-freedom-unlocked-5532/classroom")
    # both "Module 1" and "Module 2" should be picked up
    assert any("cid=mod1" in u for u in urls)
    assert any("cid=mod2" in u for u in urls)


def test_extract_module_links_from_next_data_finds_course_slugs():
    urls = _extract_module_links_from_next_data(
        CLASSROOM_HTML_NEXT_DATA,
        "https://www.skool.com/coliving-freedom-unlocked-5532/classroom",
    )
    assert urls == [
        "https://www.skool.com/coliving-freedom-unlocked-5532/classroom/5fbdb5d1",
        "https://www.skool.com/coliving-freedom-unlocked-5532/classroom/4437f016",
    ]


def test_extract_post_links_matches_post_pattern():
    urls = _extract_post_links(MODULE_HTML, "https://www.skool.com/coliving-freedom-unlocked-5532/classroom?cid=mod1")
    assert len(urls) == 3
    assert all("d38e0029" in u or "abc12345" in u or "zzz99999" in u for u in urls)


def test_extract_videos_from_post_finds_loom():
    rows = _extract_videos_from_post(
        POST_HTML_LOOM,
        "https://www.skool.com/g/classroom/d38e0029",
        "https://www.skool.com/g/classroom/d38e0029",
    )
    assert len(rows) == 1
    assert rows[0].embed_type == "loom"
    assert "loom.com/embed" in rows[0].video_url
    assert rows[0].post_title == "Intro to coliving"
    assert rows[0].reachable == "yes"


def test_extract_videos_from_post_finds_mixed():
    rows = _extract_videos_from_post(
        POST_HTML_MIXED,
        "https://www.skool.com/g/classroom/mixed",
        "https://www.skool.com/g/classroom/mixed",
    )
    # 3 video embeds: vimeo, mp4, youtube (next-post link is filtered out)
    embeds = {r.embed_type for r in rows}
    assert embeds == {"vimeo", "mp4", "youtube"}
    # All three are classified reachable=yes
    for r in rows:
        assert r.reachable == "yes", f"{r.embed_type} should be reachable"


def test_extract_videos_from_post_flags_m3u8_unreachable():
    rows = _extract_videos_from_post(
        POST_HTML_MUX_M3U8,
        "https://www.skool.com/g/classroom/protected",
        "https://www.skool.com/g/classroom/protected",
    )
    assert len(rows) == 1
    assert rows[0].embed_type == "m3u8"
    assert rows[0].reachable == "no"


def test_extract_videos_from_post_falls_back_to_next_data_video_link():
    rows = _extract_videos_from_post(
        POST_HTML_NEXT_DATA_YOUTUBE,
        "https://www.skool.com/g/classroom/abc?md=mod-2",
        "https://www.skool.com/g/classroom/abc?md=mod-2",
    )
    assert len(rows) == 1
    assert rows[0].post_title == "Next Data YouTube"
    assert rows[0].video_url == "https://youtu.be/abc123xyz"
    assert rows[0].embed_type == "youtube"
    assert rows[0].reachable == "yes"


def test_extract_videos_from_post_falls_back_to_next_data_mux():
    rows = _extract_videos_from_post(
        POST_HTML_NEXT_DATA_MUX,
        "https://www.skool.com/g/classroom/abc?md=mod-3",
        "https://www.skool.com/g/classroom/abc?md=mod-3",
    )
    assert len(rows) == 1
    assert rows[0].post_title == "Next Data Mux"
    assert rows[0].video_url == "https://stream.mux.com/muxplaybackid.m3u8?token=muxtoken"
    assert rows[0].embed_type == "m3u8"
    assert rows[0].reachable == "no"


def test_netscape_cookies_parser_handles_comments(tmp_path: Path):
    p = tmp_path / "c.txt"
    p.write_text(
        "# Netscape HTTP Cookie File\n"
        "# domain flag path secure expiry name value\n"
        "example.com\tFALSE\t/\tFALSE\t0\tsess\tabc\n"
    )
    cookies = load_netscape_cookies(p)
    assert len(cookies) == 1
    assert cookies[0]["name"] == "sess"
