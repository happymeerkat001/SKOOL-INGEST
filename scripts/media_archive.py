"""media_archive.py - Process a media source and archive it to remote storage.

Workflow:
1. ffmpeg renders the source into a looped mp4 (--loops, stream-copy).
2. ffmpeg extracts an mp3 from the local mp4 (no second network fetch).
3. Both assets upload via rclone to a remote bucket:
       <remote>/audio/<slug>-<stamp>.mp3
       <remote>/video/loops/<slug>-<stamp>.mp4
4. Each upload is verified (remote size must match local size).
5. After verification the script pauses and asks for explicit human
   confirmation; local copies are deleted only if the user types 'yes'.

rclone is provider-agnostic: S3, R2, B2, MinIO, SFTP, Drive, etc. all work
with the same `remote:bucket` syntax.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from local_ingest import slugify


@dataclass
class ArchiveConfig:
    source: str
    remote: str
    workdir: Path
    name: str
    loops: int = 1
    audio_bitrate: str = "128k"
    keep_local: bool = False


def run(args: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{args[0]} failed: {proc.stderr.strip()[:400]}")
    return proc


def asset_name(name: str, stamp: str, ext: str) -> str:
    return f"{slugify(name)}-{stamp}.{ext}"


def remote_paths(remote: str, name: str, stamp: str) -> dict[str, str]:
    base = remote.rstrip("/")
    return {
        "audio": f"{base}/audio/{asset_name(name, stamp, 'mp3')}",
        "video": f"{base}/video/loops/{asset_name(name, stamp, 'mp4')}",
    }


def loop_video_args(source: str, dest: Path, loops: int) -> list[str]:
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if loops > 1:
        args += ["-stream_loop", str(loops - 1)]
    args += ["-i", source, "-c", "copy", "-movflags", "+faststart", str(dest)]
    return args


def extract_audio_args(src: Path, dest: Path, bitrate: str) -> list[str]:
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src), "-vn", "-c:a", "libmp3lame", "-b:a", bitrate,
        str(dest),
    ]


def remote_size(remote_path: str) -> int | None:
    parent, _, leaf = remote_path.rpartition("/")
    proc = run(["rclone", "lsjson", parent, "--files-only"])
    for entry in json.loads(proc.stdout or "[]"):
        if entry.get("Name") == leaf:
            return int(entry.get("Size", -1))
    return None


def upload_verified(local: Path, remote_path: str) -> None:
    run(["rclone", "copyto", str(local), remote_path])
    expected = local.stat().st_size
    actual = remote_size(remote_path)
    if actual != expected:
        raise RuntimeError(
            f"upload verification failed for {remote_path}: "
            f"local={expected} remote={actual}"
        )


def confirm_local_cleanup(pairs: list[tuple[Path, str]], ask=input) -> bool:
    print("Upload verified. Local and remote copies:")
    for local, remote_path in pairs:
        print(f"  {local}  ->  {remote_path}")
    answer = ask("Verify the files in your cloud storage. Type 'yes' to delete local copies: ")
    return answer.strip().lower() == "yes"


def archive(config: ArchiveConfig, ask=input) -> dict:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    paths = remote_paths(config.remote, config.name, stamp)
    config.workdir.mkdir(parents=True, exist_ok=True)
    local_video = config.workdir / asset_name(config.name, stamp, "mp4")
    local_audio = config.workdir / asset_name(config.name, stamp, "mp3")

    run(loop_video_args(config.source, local_video, config.loops))
    run(extract_audio_args(local_video, local_audio, config.audio_bitrate))

    pairs = [(local_video, paths["video"]), (local_audio, paths["audio"])]
    for local, remote_path in pairs:
        upload_verified(local, remote_path)

    deleted = False
    if not config.keep_local and confirm_local_cleanup(pairs, ask=ask):
        for local, _ in pairs:
            local.unlink()
        deleted = True
    if not deleted:
        print("Local files preserved.")
    return {"video": paths["video"], "audio": paths["audio"], "local_deleted": deleted}


def parse_args(argv: list[str] | None = None) -> ArchiveConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="Input media: local file path or URL ffmpeg can read")
    parser.add_argument("--remote", required=True, help="rclone destination, e.g. r2:my-bucket")
    parser.add_argument("--name", required=True, help="Asset base name (slugified)")
    parser.add_argument("--loops", type=int, default=1, help="Times the video repeats (default 1 = no loop)")
    parser.add_argument("--workdir", type=Path, default=Path("manifest/media_archive"), help="Local staging dir")
    parser.add_argument("--audio-bitrate", default="128k")
    parser.add_argument("--keep-local", action="store_true", help="Never delete local copies (skips the confirmation prompt)")
    args = parser.parse_args(argv)
    return ArchiveConfig(
        source=args.source,
        remote=args.remote,
        workdir=args.workdir,
        name=args.name,
        loops=args.loops,
        audio_bitrate=args.audio_bitrate,
        keep_local=args.keep_local,
    )


def main() -> int:
    config = parse_args()
    result = archive(config)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
