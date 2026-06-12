"""local_ingest.py - Download protected m3u8 streams and transcribe them locally.

Designed for the case where transcript.lol's URL ingestion fails on protected
streams (Skool, Mux, Cloudflare-fronted HLS) but the original platform's
cookie session can still reach the source page.

Workflow:
1. Read the manifest of video URLs (CSV with embed_type=m3u8 rows).
2. Refresh tokens just-in-time by re-crawling the source page (optional).
3. ffmpeg stream-copies the m3u8 -> .mp4 (fast, no re-encode).
4. ffmpeg extracts mp3 from the local mp4 (no network).
5. faster-whisper transcribes the mp3 -> text with timestamps.
6. Build a vault-ready markdown note per recording.
7. The mp4 and mp3 are durably archived to --archive-dir (default iCloud).

Reusable inside Obsidian-vault-orchestrator for any future protected stream
that needs the same treatment.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

try:
    from faster_whisper import WhisperModel
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "faster-whisper is required. Install with: python3 -m pip install faster-whisper"
    ) from exc


DEFAULT_FFMPEG_HEADERS = (
    "Referer: https://www.skool.com/\r\n"
    "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36\r\n"
)
DEFAULT_ARCHIVE_DIR = Path(
    "~/Library/Mobile Documents/com~apple~CloudDocs/SkoolArchive"
).expanduser()
MIN_TOKEN_MINUTES = 10.0


def jwt_exp(token: str) -> int | None:
    """Extract the `exp` claim from a JWT-style token (no signature check).

    Returns the Unix timestamp of expiration, or None if it can't be parsed.
    """
    if not token or not isinstance(token, str):
        return None
    parts = token.split(".")
    if len(parts) < 2:
        return None
    body = parts[1]
    try:
        padded = body + "=" * (-len(body) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    exp = payload.get("exp")
    return int(exp) if isinstance(exp, (int, float)) else None


def token_minutes_remaining(url: str) -> float | None:
    """Pull the `token=` param from an m3u8 URL and report minutes until exp."""
    m = re.search(r"token=([^&]+)", url or "")
    if not m:
        return None
    exp = jwt_exp(m.group(1))
    if exp is None:
        return None
    return (exp - time.time()) / 60.0


@dataclass
class IngestConfig:
    workdir: Path
    vault_dir: Path
    manifest_path: Path
    archive_dir: Path = field(default_factory=lambda: DEFAULT_ARCHIVE_DIR)
    whisper_model: str = "tiny.en"
    whisper_device: str = "cpu"
    whisper_compute: str = "int8"
    max_seconds: int = 0
    sample_seconds: int = 0
    downloader: str = "auto"
    skip_existing: bool = True
    ffmpeg_headers: str = DEFAULT_FFMPEG_HEADERS
    skip_download: bool = False
    skip_transcribe: bool = False
    only_ids: set[str] = field(default_factory=set)
    embed_filter: str = "m3u8"
    min_token_minutes: float = MIN_TOKEN_MINUTES
    stats: dict = field(default_factory=dict)


def slugify(text: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", text or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.rstrip(". ").strip()
    return (cleaned or "untitled")[:160]


def _valid_file(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def _download_with_ytdlp(m3u8_url: str, dest: Path, t0: float, ffmpeg_error: str = "") -> dict:
    if shutil.which("yt-dlp") is None:
        raise RuntimeError(f"download_video failed and yt-dlp not found: {ffmpeg_error[-400:]}")
    if dest.exists():
        dest.unlink()
    ytdlp_args = [
        "yt-dlp",
        "--no-part",
        "--force-overwrites",
        "--referer",
        "https://www.skool.com/",
        "--user-agent",
        "Mozilla/5.0",
        "-N",
        "4",
        "--retries",
        "100",
        "--fragment-retries",
        "100",
        "--retry-sleep",
        "fragment:exp=1:60",
        "--retry-sleep",
        "http:exp=1:60",
        "-o",
        str(dest),
        m3u8_url,
    ]
    proc2 = subprocess.run(ytdlp_args, capture_output=True, text=True, timeout=3600)
    if proc2.returncode != 0 or not _valid_file(dest):
        detail = (proc2.stderr or proc2.stdout or ffmpeg_error)[-400:]
        raise RuntimeError(f"download_video failed: {detail}")
    return {"seconds": time.time() - t0, "bytes": dest.stat().st_size, "downloader": "yt-dlp"}


def download_video(
    m3u8_url: str,
    dest: Path,
    headers: str = DEFAULT_FFMPEG_HEADERS,
    max_seconds: int = 0,
    downloader: str = "auto",
) -> dict:
    """Stream-copy the m3u8 -> mp4; fall back to yt-dlp for stubborn HLS rows."""
    t0 = time.time()
    if downloader == "yt-dlp":
        return _download_with_ytdlp(m3u8_url, dest, t0)

    ffmpeg_args = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-user_agent",
        "Mozilla/5.0",
        "-headers",
        headers,
        "-i",
        m3u8_url,
        "-c",
        "copy",
        "-bsf:a",
        "aac_adtstoasc",
    ]
    if max_seconds:
        ffmpeg_args.extend(["-t", str(max_seconds)])
    ffmpeg_args.append(str(dest))

    ffmpeg_error = ""
    try:
        proc = subprocess.run(ffmpeg_args, capture_output=True, text=True, timeout=1800)
        ffmpeg_error = proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        proc = None
        ffmpeg_error = f"ffmpeg timed out after {exc.timeout}s"

    if proc is not None and proc.returncode == 0 and _valid_file(dest):
        return {"seconds": time.time() - t0, "bytes": dest.stat().st_size, "downloader": "ffmpeg"}

    if max_seconds or downloader == "ffmpeg":
        raise RuntimeError(f"download_video failed: {ffmpeg_error[-400:]}")
    return _download_with_ytdlp(m3u8_url, dest, t0, ffmpeg_error)


def extract_audio(src: Path, dest: Path) -> dict:
    """Extract mp3 (mono 16 kHz, 32 kbps) from a local file. No network."""
    args = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "32k",
        str(dest),
    ]
    t0 = time.time()
    proc = subprocess.run(args, capture_output=True, text=True, timeout=900)
    wall = time.time() - t0
    if proc.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        raise RuntimeError(f"extract_audio failed: {proc.stderr[-400:]}")
    return {"seconds": wall, "bytes": dest.stat().st_size}


def transcribe(audio_path: Path, config: IngestConfig) -> dict:
    model = WhisperModel(
        config.whisper_model,
        device=config.whisper_device,
        compute_type=config.whisper_compute,
    )
    t0 = time.time()
    segs, info = model.transcribe(
        str(audio_path),
        beam_size=1,
        vad_filter=True,
    )
    rows = []
    for s in segs:
        rows.append(
            {
                "start": round(s.start, 2),
                "end": round(s.end, 2),
                "text": s.text.strip(),
            }
        )
    return {
        "language": info.language,
        "duration": round(info.duration, 2),
        "wall_seconds": round(time.time() - t0, 2),
        "segments": rows,
    }


def build_markdown(
    title: str,
    post_url: str,
    video_url: str,
    transcript: dict,
    archive_video: Path | None = None,
    archive_audio: Path | None = None,
) -> str:
    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- Source: {post_url}")
    lines.append(f"- Video URL: {video_url}")
    if archive_video is not None:
        lines.append(f"- Archived video: `{archive_video}`")
    if archive_audio is not None:
        lines.append(f"- Archived audio: `{archive_audio}`")
    lines.append(f"- Language: {transcript.get('language')}")
    lines.append(f"- Audio duration: {transcript.get('duration')}s")
    lines.append(f"- Transcribed locally with faster-whisper")
    lines.append("")
    lines.append("## Transcript")
    lines.append("")
    for seg in transcript.get("segments", []):
        stamp = f"[{seg['start']:>6.2f} - {seg['end']:>6.2f}]"
        lines.append(f"{stamp} {seg['text']}")
    lines.append("")
    return "\n".join(lines)


def write_with_retry(path: Path, content: str, attempts: int = 10) -> None:
    last_exc: Exception | None = None
    for _ in range(max(1, attempts)):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return
        except OSError as exc:
            last_exc = exc
            time.sleep(0.5)
    raise last_exc or OSError(f"Could not write {path}")


def read_manifest(path: Path) -> list[dict]:
    return list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))


def update_manifest(manifest_path: Path, row_id: str, **fields) -> None:
    rows = read_manifest(manifest_path)
    for row in rows:
        if row.get("id") == row_id:
            row.update(fields)
    headers = list(rows[0].keys()) if rows else []
    with manifest_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def process_row(row: dict, config: IngestConfig, manifest_path: Path) -> dict:
    row_id = row.get("id", "")
    title = row.get("post_title") or "untitled"
    slug = slugify(title)
    workdir = config.workdir
    video_dir = workdir / "video"
    audio_dir = workdir / "audio"
    transcript_dir = workdir / "transcripts"
    archive_video_dir = config.archive_dir / "video"
    archive_audio_dir = config.archive_dir / "audio"
    note_dir = config.vault_dir
    staging_video = video_dir / f"{row_id}-{slug}.mp4"
    staging_audio = audio_dir / f"{row_id}-{slug}.mp3"
    archive_video = archive_video_dir / f"{row_id}-{slug}.mp4"
    archive_audio = archive_audio_dir / f"{row_id}-{slug}.mp3"
    transcript_path = transcript_dir / f"{row_id}-{slug}.json"
    note_path = note_dir / f"{row_id}-{slug}.md"

    if config.skip_existing and archive_video.exists():
        return {"id": row_id, "title": title, "skipped": "exists"}

    if not config.skip_download:
        video_dir.mkdir(parents=True, exist_ok=True)
        audio_dir.mkdir(parents=True, exist_ok=True)
        try:
            video_result = download_video(
                row["video_url"],
                staging_video,
                headers=config.ffmpeg_headers,
                max_seconds=config.max_seconds,
                downloader=config.downloader,
            )
            audio_result = extract_audio(staging_video, staging_audio)
        except Exception as exc:
            update_manifest(
                manifest_path,
                row_id,
                status="VIDEO_DOWNLOAD_FAILED",
                failure_reason=str(exc)[:200],
            )
            return {"id": row_id, "title": title, "error": "download", "detail": str(exc)[:200]}
    else:
        video_result = {"seconds": 0.0, "bytes": staging_video.stat().st_size if staging_video.exists() else 0}
        audio_result = {"seconds": 0.0, "bytes": staging_audio.stat().st_size if staging_audio.exists() else 0}

    if config.skip_transcribe:
        transcript = {"language": "en", "duration": 0.0, "segments": []}
    else:
        try:
            transcript = transcribe(staging_audio, config)
        except Exception as exc:
            update_manifest(
                manifest_path,
                row_id,
                status="LOCAL_TRANSCRIBE_FAILED",
                failure_reason=str(exc)[:200],
            )
            return {"id": row_id, "title": title, "error": "transcribe", "detail": str(exc)[:200]}

    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(json.dumps(transcript, indent=2, ensure_ascii=False), encoding="utf-8")
    markdown = build_markdown(
        title,
        row.get("post_url", ""),
        row.get("video_url", ""),
        transcript,
        archive_video=archive_video,
        archive_audio=archive_audio,
    )
    write_with_retry(note_path, markdown)

    archive_video_dir.mkdir(parents=True, exist_ok=True)
    archive_audio_dir.mkdir(parents=True, exist_ok=True)
    if staging_video.exists():
        shutil.move(str(staging_video), str(archive_video))
    if staging_audio.exists():
        shutil.move(str(staging_audio), str(archive_audio))

    update_manifest(
        manifest_path,
        row_id,
        status="LOCAL_TRANSCRIBED",
        failure_reason="",
    )
    return {
        "id": row_id,
        "title": title,
        "video_bytes": video_result.get("bytes"),
        "audio_bytes": audio_result.get("bytes"),
        "archive_video": str(archive_video),
        "archive_audio": str(archive_audio),
        "transcript_segments": len(transcript.get("segments", [])),
        "transcribe_seconds": transcript.get("wall_seconds"),
    }


def parse_args() -> IngestConfig:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", type=Path, default=Path("manifest/skool_videos.csv"))
    ap.add_argument("--workdir", type=Path, default=Path("manifest/local_ingest"))
    ap.add_argument("--vault-dir", type=Path, required=True)
    ap.add_argument(
        "--archive-dir",
        type=Path,
        default=DEFAULT_ARCHIVE_DIR,
        help="Durable archive root; video/ and audio/ subdirs will be created.",
    )
    ap.add_argument("--model", default="tiny.en")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--compute", default="int8")
    ap.add_argument("--max-seconds", type=int, default=0)
    ap.add_argument("--sample-seconds", type=int, default=0)
    ap.add_argument(
        "--downloader",
        choices=("auto", "ffmpeg", "yt-dlp"),
        default="auto",
        help="Video downloader. Use yt-dlp to skip ffmpeg for known-stubborn HLS rows.",
    )
    ap.add_argument("--skip-existing", action="store_true", default=True)
    ap.add_argument("--no-skip-existing", dest="skip_existing", action="store_false")
    ap.add_argument("--skip-download", action="store_true")
    ap.add_argument("--skip-transcribe", action="store_true")
    ap.add_argument("--only-ids", nargs="*", default=[])
    ap.add_argument("--embed-filter", default="m3u8")
    ap.add_argument(
        "--min-token-minutes",
        type=float,
        default=MIN_TOKEN_MINUTES,
        help="If a row's m3u8 token has fewer minutes left, log a warning.",
    )
    ap.add_argument(
        "--recrawl-cmd",
        default=None,
        help="Optional shell command to re-crawl Skool and refresh tokens. "
        "Invoked when the manifest token is stale.",
    )
    args = ap.parse_args()
    return IngestConfig(
        workdir=args.workdir.expanduser(),
        vault_dir=args.vault_dir.expanduser(),
        manifest_path=args.manifest.expanduser(),
        archive_dir=args.archive_dir.expanduser(),
        whisper_model=args.model,
        whisper_device=args.device,
        whisper_compute=args.compute,
        max_seconds=args.max_seconds,
        sample_seconds=args.sample_seconds,
        downloader=args.downloader,
        skip_existing=args.skip_existing,
        skip_download=args.skip_download,
        skip_transcribe=args.skip_transcribe,
        only_ids=set(args.only_ids or []),
        embed_filter=args.embed_filter,
        min_token_minutes=args.min_token_minutes,
        stats={"recrawl_cmd": args.recrawl_cmd},
    )


def main() -> int:
    config = parse_args()
    config.workdir.mkdir(parents=True, exist_ok=True)
    config.vault_dir.mkdir(parents=True, exist_ok=True)
    rows = read_manifest(config.manifest_path)
    if config.only_ids:
        rows = [r for r in rows if r.get("id") in config.only_ids]
    elif config.embed_filter:
        rows = [r for r in rows if r.get("embed_type") == config.embed_filter]

    summary = {
        "total": len(rows),
        "results": [],
        "token_warnings": [],
        "recrawls_triggered": 0,
    }

    recrawl_cmd = config.stats.get("recrawl_cmd") if isinstance(config.stats, dict) else None
    last_recrawl_at = 0.0
    for index, row in enumerate(rows, start=1):
        title = row.get("post_title") or "untitled"
        video_url = row.get("video_url", "")
        if video_url and "token=" in video_url:
            mins = token_minutes_remaining(video_url)
            if mins is not None and mins < config.min_token_minutes:
                warning = {
                    "id": row.get("id"),
                    "title": title,
                    "minutes_remaining": round(mins, 1),
                }
                summary["token_warnings"].append(warning)
                now = time.time()
                if recrawl_cmd and (now - last_recrawl_at) > 30:
                    print(
                        f"[token] {title}: {mins:.1f} min left, recrawling...",
                        flush=True,
                    )
                    proc = subprocess.run(
                        recrawl_cmd,
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=600,
                    )
                    last_recrawl_at = time.time()
                    summary["recrawls_triggered"] += 1
                    if proc.returncode != 0:
                        print(
                            f"[recrawl] FAILED rc={proc.returncode}: {proc.stderr[-300:]}",
                            flush=True,
                        )
                    else:
                        rows = read_manifest(config.manifest_path)
                        if config.only_ids:
                            rows = [r for r in rows if r.get("id") in config.only_ids]
                        elif config.embed_filter:
                            rows = [r for r in rows if r.get("embed_type") == config.embed_filter]
                        for r2 in rows:
                            if r2.get("id") == row.get("id"):
                                row = r2
                                video_url = row.get("video_url", "")
                                break

        print(f"[{index}/{len(rows)}] {title}", flush=True)
        result = process_row(row, config, config.manifest_path)
        print(f"    -> {result}", flush=True)
        summary["results"].append(result)
    summary_path = config.workdir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"summary: {summary_path}")
    print(
        f"recrawls={summary['recrawls_triggered']}  token_warnings={len(summary['token_warnings'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
