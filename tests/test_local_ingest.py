"""Tests for local_ingest core functions."""

import base64
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from local_ingest import (  # noqa: E402
    IngestConfig,
    build_markdown,
    download_video,
    extract_audio,
    jwt_exp,
    process_row,
    read_manifest,
    slugify,
    token_minutes_remaining,
    update_manifest,
    write_with_retry,
)


class SlugifyTests(unittest.TestCase):
    def test_keeps_readable_title(self):
        self.assertEqual(slugify("Houston Property"), "Houston Property")

    def test_strips_invalid_chars(self):
        self.assertEqual(slugify('a<b>c:d/e\\f|g?h*"x'), "abcdefghx")

    def test_strips_trailing_dot_and_collapses_whitespace(self):
        self.assertEqual(slugify("  Title   with   spaces  ."), "Title with spaces")

    def test_handles_empty(self):
        self.assertEqual(slugify(""), "untitled")

    def test_truncates_long_titles(self):
        long = "A" * 400
        self.assertEqual(len(slugify(long)), 160)


class WriteWithRetryTests(unittest.TestCase):
    def test_writes_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "note.md"
            write_with_retry(path, "hello world")
            self.assertEqual(path.read_text(encoding="utf-8"), "hello world")


class ManifestTests(unittest.TestCase):
    def test_read_and_update_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.csv"
            path.write_text(
                "id,post_title,video_url,status,failure_reason\n"
                "abc,Houston,https://x,pending,\n"
                "def,Other,https://y,pending,\n",
                encoding="utf-8",
            )
            rows = read_manifest(path)
            self.assertEqual(len(rows), 2)
            update_manifest(path, "abc", status="LOCAL_TRANSCRIBED", failure_reason="")
            rows = read_manifest(path)
            self.assertEqual(rows[0]["status"], "LOCAL_TRANSCRIBED")
            self.assertEqual(rows[1]["status"], "pending")


class ProcessRowTests(unittest.TestCase):
    def _build_config(self, workdir, vault, manifest, archive):
        return IngestConfig(
            workdir=workdir,
            vault_dir=vault,
            manifest_path=manifest,
            archive_dir=archive,
            whisper_model="tiny.en",
            whisper_device="cpu",
            whisper_compute="int8",
            skip_download=True,
            skip_transcribe=True,
        )

    def test_skips_existing_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp) / "wd"
            vault = Path(tmp) / "vault"
            archive = Path(tmp) / "archive"
            for d in (workdir, vault, archive, archive / "video", archive / "audio"):
                d.mkdir(parents=True)
            existing = archive / "video" / "abc-Houston.mp4"
            existing.write_text("placeholder", encoding="utf-8")
            manifest = Path(tmp) / "manifest.csv"
            manifest.write_text(
                "id,post_title,post_url,video_url,embed_type\n"
                "abc,Houston,https://x,https://y,m3u8\n",
                encoding="utf-8",
            )
            config = self._build_config(workdir, vault, manifest, archive)
            result = process_row(
                {
                    "id": "abc",
                    "post_title": "Houston",
                    "post_url": "https://x",
                    "video_url": "https://y",
                    "embed_type": "m3u8",
                },
                config,
                manifest,
            )
            self.assertEqual(result.get("skipped"), "exists")
            self.assertEqual(existing.read_text(encoding="utf-8"), "placeholder")

    def test_writes_note_when_skip_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp) / "wd"
            vault = Path(tmp) / "vault"
            archive = Path(tmp) / "archive"
            for d in (workdir, vault, archive, archive / "video", archive / "audio"):
                d.mkdir(parents=True)
            manifest = Path(tmp) / "manifest.csv"
            manifest.write_text(
                "id,post_title,post_url,video_url,embed_type,status,failure_reason\n"
                "abc,Houston,https://x,https://y,m3u8,pending,\n",
                encoding="utf-8",
            )
            config = self._build_config(workdir, vault, manifest, archive)
            result = process_row(
                {
                    "id": "abc",
                    "post_title": "Houston",
                    "post_url": "https://x",
                    "video_url": "https://y",
                    "embed_type": "m3u8",
                },
                config,
                manifest,
            )
            self.assertNotIn("error", result)
            note = vault / "abc-Houston.md"
            self.assertTrue(note.exists())
            updated = read_manifest(manifest)
            self.assertEqual(updated[0]["status"], "LOCAL_TRANSCRIBED")


class BuildMarkdownTests(unittest.TestCase):
    def test_includes_segments(self):
        md = build_markdown(
            "Sample",
            "https://post",
            "https://vid",
            {
                "language": "en",
                "duration": 12.5,
                "segments": [
                    {"start": 0.0, "end": 2.5, "text": "hello world"},
                    {"start": 2.5, "end": 5.0, "text": "second line"},
                ],
            },
        )
        self.assertIn("# Sample", md)
        self.assertIn("hello world", md)
        self.assertIn("second line", md)
        self.assertIn("12.5", md)


def _make_jwt(payload: dict) -> str:
    """Build a JWT-like string with arbitrary payload, no signature check."""
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').decode("ascii").rstrip("=")
    body = base64.urlsafe_b64encode(
        json.dumps(payload).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"{header}.{body}.sig"


class JwtExpTests(unittest.TestCase):
    def test_parses_future_exp(self):
        future = int(time.time()) + 3600
        token = _make_jwt({"exp": future})
        self.assertEqual(jwt_exp(token), future)

    def test_returns_none_when_no_exp(self):
        token = _make_jwt({"sub": "abc"})
        self.assertIsNone(jwt_exp(token))

    def test_returns_none_for_garbage(self):
        self.assertIsNone(jwt_exp("not-a-jwt"))
        self.assertIsNone(jwt_exp("only.two.parts"))

    def test_token_minutes_remaining_from_url(self):
        future = int(time.time()) + 600  # 10 min
        token = _make_jwt({"exp": future})
        url = f"https://stream.video.skool.com/abc.m3u8?token={token}"
        mins = token_minutes_remaining(url)
        self.assertIsNotNone(mins)
        self.assertGreater(mins, 9.0)
        self.assertLess(mins, 11.0)

    def test_token_minutes_remaining_handles_missing_token(self):
        self.assertIsNone(token_minutes_remaining("https://x.com/file.m3u8"))


class DownloadCommandTests(unittest.TestCase):
    def test_download_video_builds_stream_copy_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "out.mp4"
            with mock.patch("subprocess.run") as run:
                def fake_run(args, **kwargs):
                    dest.write_bytes(b"\x00" * 4096)
                    return mock.Mock(returncode=0, stderr="")

                run.side_effect = fake_run
                download_video(
                    "https://x.com/stream.m3u8?token=abc",
                    dest,
                    headers="Referer: https://www.skool.com/\r\n",
                    max_seconds=0,
                )
            args = run.call_args.args[0]
            self.assertIn("ffmpeg", args)
            self.assertIn("-c", args)
            self.assertIn("copy", args)
            self.assertIn(str(dest), args)
            self.assertIn("https://x.com/stream.m3u8?token=abc", args)

    def test_download_video_falls_back_to_ytdlp_when_ffmpeg_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "out.mp4"

            def fake_run(args, **kwargs):
                if args[0] == "ffmpeg":
                    return mock.Mock(returncode=1, stderr="ffmpeg failed")
                if args[0] == "yt-dlp":
                    dest.write_bytes(b"\x00" * 4096)
                    return mock.Mock(returncode=0, stderr="")
                raise AssertionError(args)

            with mock.patch("subprocess.run", side_effect=fake_run) as run:
                result = download_video(
                    "https://x.com/stream.m3u8?token=abc",
                    dest,
                    headers="Referer: https://www.skool.com/\r\n",
                    max_seconds=0,
                )
            self.assertEqual(result["bytes"], 4096)
            self.assertEqual(run.call_args_list[0].args[0][0], "ffmpeg")
            self.assertEqual(run.call_args_list[1].args[0][0], "yt-dlp")
            self.assertIn("--referer", run.call_args_list[1].args[0])
            self.assertIn("--fragment-retries", run.call_args_list[1].args[0])
            self.assertIn("--retry-sleep", run.call_args_list[1].args[0])

    def test_download_video_can_use_ytdlp_directly(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "out.mp4"

            def fake_run(args, **kwargs):
                self.assertEqual(args[0], "yt-dlp")
                dest.write_bytes(b"\x00" * 4096)
                return mock.Mock(returncode=0, stderr="")

            with mock.patch("subprocess.run", side_effect=fake_run) as run:
                result = download_video(
                    "https://x.com/stream.m3u8?token=abc",
                    dest,
                    headers="Referer: https://www.skool.com/\r\n",
                    downloader="yt-dlp",
                )
            self.assertEqual(result["downloader"], "yt-dlp")
            self.assertEqual(len(run.call_args_list), 1)

    def test_extract_audio_uses_no_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.mp4"
            src.write_bytes(b"\x00" * 4096)
            dest = Path(tmp) / "out.mp3"
            with mock.patch("subprocess.run") as run:
                def fake_run(args, **kwargs):
                    dest.write_bytes(b"\x00" * 4096)
                    return mock.Mock(returncode=0, stderr="")

                run.side_effect = fake_run
                extract_audio(src, dest)
            args = run.call_args.args[0]
            self.assertIn("ffmpeg", args)
            self.assertIn(str(src), args)
            self.assertIn(str(dest), args)
            self.assertNotIn("https://", " ".join(args))


class ProcessRowArchiveTests(unittest.TestCase):
    def _build_config(self, workdir, vault, manifest, archive):
        return IngestConfig(
            workdir=workdir,
            vault_dir=vault,
            manifest_path=manifest,
            archive_dir=archive,
            whisper_model="tiny.en",
            whisper_device="cpu",
            whisper_compute="int8",
            skip_download=True,
            skip_transcribe=True,
        )

    def test_skip_existing_checks_archive_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp) / "wd"
            vault = Path(tmp) / "vault"
            archive = Path(tmp) / "archive"
            for d in (workdir, vault, archive, archive / "video", archive / "audio"):
                d.mkdir(parents=True)
            existing_video = archive / "video" / "abc-Houston.mp4"
            existing_video.write_text("placeholder", encoding="utf-8")
            manifest = Path(tmp) / "manifest.csv"
            manifest.write_text(
                "id,post_title,post_url,video_url,embed_type,status,failure_reason\n"
                "abc,Houston,https://x,https://y,m3u8,pending,\n",
                encoding="utf-8",
            )
            config = self._build_config(workdir, vault, manifest, archive)
            result = process_row(
                {
                    "id": "abc",
                    "post_title": "Houston",
                    "post_url": "https://x",
                    "video_url": "https://y",
                    "embed_type": "m3u8",
                },
                config,
                manifest,
            )
            self.assertEqual(result.get("skipped"), "exists")
            self.assertEqual(existing_video.read_text(encoding="utf-8"), "placeholder")

    def test_completed_media_is_moved_to_archive_not_copied(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp) / "wd"
            vault = Path(tmp) / "vault"
            archive = Path(tmp) / "archive"
            for d in (workdir / "video", workdir / "audio", vault, archive):
                d.mkdir(parents=True)
            staging_video = workdir / "video" / "abc-Houston.mp4"
            staging_audio = workdir / "audio" / "abc-Houston.mp3"
            staging_video.write_bytes(b"video")
            staging_audio.write_bytes(b"audio")
            manifest = Path(tmp) / "manifest.csv"
            manifest.write_text(
                "id,post_title,post_url,video_url,embed_type,status,failure_reason\n"
                "abc,Houston,https://x,https://y,m3u8,pending,\n",
                encoding="utf-8",
            )
            config = self._build_config(workdir, vault, manifest, archive)
            result = process_row(
                {
                    "id": "abc",
                    "post_title": "Houston",
                    "post_url": "https://x",
                    "video_url": "https://y",
                    "embed_type": "m3u8",
                },
                config,
                manifest,
            )
            self.assertEqual(result["archive_video"], str(archive / "video" / "abc-Houston.mp4"))
            self.assertFalse(staging_video.exists())
            self.assertFalse(staging_audio.exists())
            self.assertEqual((archive / "video" / "abc-Houston.mp4").read_bytes(), b"video")
            self.assertEqual((archive / "audio" / "abc-Houston.mp3").read_bytes(), b"audio")


if __name__ == "__main__":
    unittest.main()
