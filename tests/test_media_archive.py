"""Tests for media_archive core functions."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from media_archive import (
    ArchiveConfig,
    archive,
    asset_name,
    extract_audio_args,
    loop_video_args,
    remote_paths,
    upload_verified,
)


class NamingTests(unittest.TestCase):
    def test_asset_name_slugifies(self):
        self.assertEqual(
            asset_name('My: "Video"?', "20260612-010203", "mp4"),
            "My Video-20260612-010203.mp4",
        )

    def test_remote_paths_schema(self):
        paths = remote_paths("r2:bucket/", "Demo Clip", "20260612-010203")
        self.assertEqual(paths["audio"], "r2:bucket/audio/Demo Clip-20260612-010203.mp3")
        self.assertEqual(paths["video"], "r2:bucket/video/loops/Demo Clip-20260612-010203.mp4")


class FfmpegArgsTests(unittest.TestCase):
    def test_loop_args_include_stream_loop(self):
        args = loop_video_args("in.mp4", Path("out.mp4"), loops=3)
        self.assertIn("-stream_loop", args)
        self.assertEqual(args[args.index("-stream_loop") + 1], "2")
        self.assertIn("copy", args)

    def test_no_loop_flag_for_single_pass(self):
        args = loop_video_args("in.mp4", Path("out.mp4"), loops=1)
        self.assertNotIn("-stream_loop", args)

    def test_extract_audio_args(self):
        args = extract_audio_args(Path("v.mp4"), Path("a.mp3"), "128k")
        self.assertIn("-vn", args)
        self.assertEqual(args[args.index("-b:a") + 1], "128k")


class UploadTests(unittest.TestCase):
    def test_upload_verified_deletes_nothing_and_passes_on_size_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "a.mp3"
            local.write_bytes(b"x" * 10)
            with mock.patch("media_archive.run") as run_mock, \
                 mock.patch("media_archive.remote_size", return_value=10):
                upload_verified(local, "r2:bucket/audio/a.mp3")
            run_mock.assert_called_once_with(
                ["rclone", "copyto", str(local), "r2:bucket/audio/a.mp3"]
            )

    def test_upload_verified_raises_on_size_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "a.mp3"
            local.write_bytes(b"x" * 10)
            with mock.patch("media_archive.run"), \
                 mock.patch("media_archive.remote_size", return_value=3):
                with self.assertRaises(RuntimeError):
                    upload_verified(local, "r2:bucket/audio/a.mp3")


class ArchiveFlowTests(unittest.TestCase):
    def _config(self, tmp: str, keep_local: bool = False) -> ArchiveConfig:
        return ArchiveConfig(
            source="https://example.com/in.m3u8",
            remote="r2:bucket",
            workdir=Path(tmp) / "staging",
            name="Demo",
            loops=2,
            keep_local=keep_local,
        )

    def _fake_run(self, args):
        if args[0] == "ffmpeg":
            Path(args[-1]).write_bytes(b"media")
        return mock.Mock(stdout="")

    def test_archive_deletes_local_only_after_user_types_yes(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp)
            with mock.patch("media_archive.run", side_effect=self._fake_run), \
                 mock.patch("media_archive.remote_size", return_value=5):
                result = archive(config, ask=lambda _: "yes")
            self.assertEqual(list(config.workdir.iterdir()), [])
            self.assertTrue(result["local_deleted"])
            self.assertIn("r2:bucket/video/loops/Demo-", result["video"])
            self.assertIn("r2:bucket/audio/Demo-", result["audio"])

    def test_archive_preserves_local_on_any_other_answer(self):
        for answer in ("", "no", "y", "YES please"):
            with tempfile.TemporaryDirectory() as tmp:
                config = self._config(tmp)
                with mock.patch("media_archive.run", side_effect=self._fake_run), \
                     mock.patch("media_archive.remote_size", return_value=5):
                    result = archive(config, ask=lambda _: answer)
                self.assertEqual(len(list(config.workdir.iterdir())), 2)
                self.assertFalse(result["local_deleted"])

    def test_archive_accepts_yes_with_whitespace_and_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp)
            with mock.patch("media_archive.run", side_effect=self._fake_run), \
                 mock.patch("media_archive.remote_size", return_value=5):
                result = archive(config, ask=lambda _: "  Yes \n")
            self.assertTrue(result["local_deleted"])

    def test_archive_keep_local_flag_skips_prompt_and_preserves_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp, keep_local=True)
            ask = mock.Mock()
            with mock.patch("media_archive.run", side_effect=self._fake_run), \
                 mock.patch("media_archive.remote_size", return_value=5):
                result = archive(config, ask=ask)
            ask.assert_not_called()
            self.assertEqual(len(list(config.workdir.iterdir())), 2)
            self.assertFalse(result["local_deleted"])

    def test_archive_aborts_cleanup_on_failed_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp)
            with mock.patch("media_archive.run", side_effect=self._fake_run), \
                 mock.patch("media_archive.remote_size", return_value=999):
                with self.assertRaises(RuntimeError):
                    archive(config)
            self.assertEqual(len(list(config.workdir.iterdir())), 2)


if __name__ == "__main__":
    unittest.main()
