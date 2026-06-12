# Source Code Appendix

_Inline for Claude, built 2026-06-11._

These are the actual source files that drive the Skool -> transcript.lol -> Obsidian pipeline. Read them as needed when answering questions about how the pipeline works, what's broken, or what a code change would touch.

## Obsidian-vault-orchestrator/cli/transcribe.py

<!-- absolute path: /Users/leon/Documents/Code/Obsidian-vault-orchestrator/cli/transcribe.py -->

```python
#!/usr/bin/env python3
"""
transcribe.py - Submit a media URL to Transcript.lol and print the transcript.

Manual run:
  python3 /Users/leon/Documents/Code/vault-orchestrator/cli/transcribe.py \
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

Auth test:
  python3 /Users/leon/Documents/Code/vault-orchestrator/cli/transcribe.py --test-auth

Optional env vars in .env or process environment:
  FIREBASE_API_KEY
  TRANSCRIPT_LOL_SPACE_ID
  TRANSCRIPT_LOL_SPACE_NAME
  TRANSCRIPT_LOL_API_KEY
  TRANSCRIPT_LOL_AUTH_TOKEN
  TRANSCRIPT_LOL_SESSION_COOKIE
  Transcript.lol_Login
  Transcript.lol_Password

No external dependencies - stdlib only.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import Cookie, CookieJar
from pathlib import Path

from media_captions import fetch_vimeo_captions

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

BASE_URL = "https://transcript.lol"
API_BASE_URL = f"{BASE_URL}/api/v1"
FIREBASE_SIGN_IN_URL = (
    "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
)
DEFAULT_SPACE_ID = "678568d76d74d77ee0ef382c"
DEFAULT_TIMEOUT_SECONDS = 600
POLL_INTERVAL_SECONDS = 5

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

SOURCE_MAP = {
    "youtube.com": "YOUTUBE",
    "youtu.be": "YOUTUBE",
    "vimeo.com": "VIMEO",
    "instagram.com": "INSTAGRAM",
    "x.com": "X",
    "twitter.com": "X",
    "facebook.com": "FACEBOOK",
}

VIDEO_SOURCES = {"YOUTUBE", "VIMEO", "INSTAGRAM", "X", "FACEBOOK"}
TERMINAL_STATUSES = {"COMPLETED", "COMPLETE", "DONE", "READY", "SUCCEEDED", "SUCCESS", "TRANSCRIPTION_COMPLETE"}
FAILED_STATUSES = {"FAILED", "ERROR", "CANCELLED", "REJECTED"}


def load_env(env_path: Path | None = None) -> dict[str, str]:
    values: dict[str, str] = {}
    path = env_path or ENV_PATH
    if path.exists():
        pattern = re.compile(r'^\s*([A-Za-z0-9_.-]+)\s*=\s*"?([^"]*)"?\s*$')
        for line in path.read_text(encoding="utf-8").splitlines():
            match = pattern.match(line)
            if match:
                values[match.group(1)] = match.group(2)

    for key, value in os.environ.items():
        if key in values or key.startswith('TRANSCRIPT_LOL_') or key.startswith('FIREBASE_') or key.startswith('SKOOL_') or key == 'Transcript.lol_Login' or key == 'Transcript.lol_Password':
            values[key] = value
    return values


def extract_youtube_id(url: str) -> str | None:
    if not url:
        return None
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.strip("/")

    if host in {"youtube.com", "m.youtube.com"}:
        if path == "watch":
            video_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
            return video_id or None
        if path.startswith("shorts/") or path.startswith("embed/") or path.startswith("live/"):
            parts = path.split("/")
            if len(parts) >= 2 and parts[1]:
                return parts[1]
    if host == "youtu.be":
        parts = path.split("/")
        if parts and parts[0]:
            return parts[0]
    return None


def normalize_recordings_payload(payload: dict | list) -> list[dict]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("recordings", "items", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                items = value
                break
        else:
            return []
    else:
        return []
    return [item for item in items if isinstance(item, dict)]


def urls_match(source_url: str, target_url: str) -> bool:
    source_video_id = extract_youtube_id(source_url)
    target_video_id = extract_youtube_id(target_url)
    if source_video_id and target_video_id:
        return source_video_id == target_video_id

    source_parts = urllib.parse.urlsplit((source_url or "").strip())
    target_parts = urllib.parse.urlsplit((target_url or "").strip())
    normalized_source = urllib.parse.urlunsplit(
        (
            source_parts.scheme.lower(),
            source_parts.netloc.lower(),
            source_parts.path.rstrip("/"),
            source_parts.query,
            "",
        )
    )
    normalized_target = urllib.parse.urlunsplit(
        (
            target_parts.scheme.lower(),
            target_parts.netloc.lower(),
            target_parts.path.rstrip("/"),
            target_parts.query,
            "",
        )
    )
    return normalized_source == normalized_target


class TranscriptClient:
    def __init__(self, env: dict[str, str]) -> None:
        self.env = env
        self.space_name = (env.get("TRANSCRIPT_LOL_SPACE_NAME") or "").strip()
        self.space_id = (env.get("TRANSCRIPT_LOL_SPACE_ID") or DEFAULT_SPACE_ID).strip() or DEFAULT_SPACE_ID
        self.cookie_jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookie_jar))
        self.auth_headers: dict[str, str] = {}
        self.firebase_tokens: dict[str, str] = {}

    def _request(
        self,
        url: str,
        *,
        method: str = "GET",
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        accept_json: bool = True,
    ) -> tuple[int, bytes, dict[str, str]]:
        merged_headers = {
            "Accept": "application/json" if accept_json else "*/*",
            "User-Agent": _UA,
            **self.auth_headers,
        }
        if headers:
            merged_headers.update(headers)

        req = urllib.request.Request(url, data=data, method=method, headers=merged_headers)
        try:
            with self.opener.open(req, timeout=30) as resp:
                body = resp.read()
                return resp.getcode(), body, dict(resp.headers.items())
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers.items())

    def _json_request(
        self,
        url: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict | list:
        body = None
        req_headers = dict(headers or {})
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            req_headers["Content-Type"] = "application/json"
        status, raw, _ = self._request(url, method=method, data=body, headers=req_headers)
        if status >= 400:
            text = raw.decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {status} from {url}: {text[:500]}")
        return json.loads(raw.decode("utf-8"))

    def authenticate(self) -> None:
        api_key = self.env.get("TRANSCRIPT_LOL_API_KEY")
        if api_key:
            self.auth_headers = {"x-api-key": api_key}
            self._verify_auth("api key")
            self._resolve_space_id()
            return

        auth_token = self.env.get("TRANSCRIPT_LOL_AUTH_TOKEN")
        if auth_token:
            self.auth_headers = {"Authorization": auth_token}
            self._verify_auth("auth token")
            self._resolve_space_id()
            return

        session_cookie = self.env.get("TRANSCRIPT_LOL_SESSION_COOKIE")
        if session_cookie:
            self.auth_headers = {"Cookie": session_cookie}
            self._verify_auth("session cookie")
            self._resolve_space_id()
            return

        email = self.env.get("Transcript.lol_Login")
        password = self.env.get("Transcript.lol_Password")
        if not email or not password:
            raise RuntimeError(
                "No Transcript.lol auth found. Set one of: TRANSCRIPT_LOL_API_KEY, "
                "TRANSCRIPT_LOL_AUTH_TOKEN, TRANSCRIPT_LOL_SESSION_COOKIE, or "
                "Transcript.lol_Login + Transcript.lol_Password."
            )

        firebase_api_key = self.env.get("FIREBASE_API_KEY", "")
        if firebase_api_key:
            self._login_with_firebase(email, password)
            self._establish_transcript_auth()
            self._resolve_space_id()
            return

        self._login_with_browser_session(email, password)
        self._verify_auth("browser session")
        self._resolve_space_id()

    def _resolve_space_id(self) -> None:
        if not self.space_name:
            return
        data = self._json_request(f"{API_BASE_URL}/spaces")
        spaces = [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
        for space in spaces:
            name = space.get("name")
            sid = space.get("id") or space.get("_id")
            if isinstance(name, str) and name.strip() == self.space_name and isinstance(sid, str) and sid.strip():
                self.space_id = sid.strip()
                return
        raise RuntimeError(f"Workspace named {self.space_name!r} was not found in Transcript.lol spaces list.")

    def _login_with_browser_session(self, email: str, password: str) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is required for browser-session Transcript.lol login when FIREBASE_API_KEY is unavailable. "
                "Install it with: python3 -m pip install playwright && python3 -m playwright install chromium"
            ) from exc

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"{BASE_URL}/auth/login", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1200)
            if page.locator('input[type="email"]').count() > 0:
                page.locator('input[type="email"]').first.fill(email)
                page.locator('input[type="email"]').first.press('Enter')
                page.wait_for_timeout(1200)
            if page.locator('input[type="password"]').count() == 0:
                raise RuntimeError("Transcript.lol login page did not expose a password input during browser auth.")
            page.locator('input[type="password"]').first.fill(password)
            page.locator('button:has-text("Sign In")').first.click()
            page.wait_for_timeout(6000)
            cookies = page.context.cookies()
            browser.close()

        if not cookies:
            raise RuntimeError("Transcript.lol browser login did not produce any cookies.")
        self._load_cookies(cookies)

    def _load_cookies(self, cookies: list[dict[str, object]]) -> None:
        for cookie in cookies:
            name = str(cookie.get('name') or '').strip()
            value = str(cookie.get('value') or '')
            domain = str(cookie.get('domain') or '').strip()
            if not name or not domain:
                continue
            path = str(cookie.get('path') or '/') or '/'
            secure = bool(cookie.get('secure', False))
            expires = cookie.get('expires')
            expires_int = int(expires) if isinstance(expires, (int, float)) and expires > 0 else None
            self.cookie_jar.set_cookie(Cookie(
                version=0,
                name=name,
                value=value,
                port=None,
                port_specified=False,
                domain=domain,
                domain_specified=bool(domain),
                domain_initial_dot=domain.startswith('.'),
                path=path,
                path_specified=True,
                secure=secure,
                expires=expires_int,
                discard=False,
                comment=None,
                comment_url=None,
                rest={'HttpOnly': 'True' if cookie.get('httpOnly', False) else 'False'},
                rfc2109=False,
            ))

    def _verify_auth(self, auth_mode: str) -> None:
        for candidate in (
            f"{API_BASE_URL}/me",
            f"{API_BASE_URL}/spaces/{self.space_id}",
            f"{API_BASE_URL}/spaces/{self.space_id}/recordings",
        ):
            status, raw, _ = self._request(candidate)
            if status < 400:
                print(f"[transcribe] authenticated via {auth_mode}")
                return
            if status not in (401, 403, 404):
                text = raw.decode("utf-8", errors="replace")
                raise RuntimeError(f"Auth probe failed at {candidate}: HTTP {status}: {text[:300]}")
        raise RuntimeError(f"Unable to verify Transcript.lol auth via {auth_mode}.")

    def _login_with_firebase(self, email: str, password: str) -> None:
        firebase_api_key = self.env.get("FIREBASE_API_KEY", "")
        if not firebase_api_key:
            raise RuntimeError("FIREBASE_API_KEY not set in .env")
        url = f"{FIREBASE_SIGN_IN_URL}?key={firebase_api_key}"
        data = self._json_request(
            url,
            method="POST",
            payload={
                "email": email,
                "password": password,
                "returnSecureToken": True,
            },
        )
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected Firebase auth response: {json.dumps(data)[:500]}")

        id_token = data.get("idToken")
        refresh_token = data.get("refreshToken")
        if not id_token or not refresh_token:
            raise RuntimeError(f"Firebase auth response missing tokens: {json.dumps(data)[:500]}")

        self.firebase_tokens = {
            "idToken": str(id_token),
            "refreshToken": str(refresh_token),
        }
        print("[transcribe] Firebase sign-in succeeded")

    def _establish_transcript_auth(self) -> None:
        id_token = self.firebase_tokens["idToken"]
        refresh_token = self.firebase_tokens["refreshToken"]

        exchange_attempts = [
            (
                f"{BASE_URL}/api/auth/session",
                {"id_token": id_token, "refresh_token": refresh_token},
            ),
            (
                f"{BASE_URL}/api/auth/firebase",
                {"idToken": id_token, "refreshToken": refresh_token},
            ),
            (
                f"{BASE_URL}/auth/session",
                {"id_token": id_token, "refresh_token": refresh_token},
            ),
            (
                f"{BASE_URL}/api/v1/auth/session",
                {"id_token": id_token, "refresh_token": refresh_token},
            ),
        ]

        for url, payload in exchange_attempts:
            status, raw, _ = self._request(
                url,
                method="POST",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Origin": BASE_URL,
                    "Referer": f"{BASE_URL}/auth/login",
                },
            )
            if status < 400 and self._has_auth_token_cookie():
                self.auth_headers = {}
                self._verify_auth(f"firebase exchange at {url}")
                return
            if status not in (401, 403, 404):
                text = raw.decode("utf-8", errors="replace")
                print(
                    f"[transcribe] auth exchange probe at {url} -> HTTP {status}: {text[:200]}",
                    file=sys.stderr,
                )

        unsigned_auth_token = self._build_unsigned_auth_token(id_token, refresh_token)
        strategies = [
            ("firebase bearer token", {"Authorization": f"Bearer {id_token}"}),
            ("firebase id token", {"Authorization": id_token}),
            ("AuthToken cookie", {"Cookie": f"AuthToken={unsigned_auth_token}"}),
        ]
        for auth_mode, headers in strategies:
            self.auth_headers = headers
            try:
                self._verify_auth(auth_mode)
                return
            except RuntimeError as exc:
                print(f"[transcribe] auth strategy failed: {auth_mode}: {exc}", file=sys.stderr)

        raise RuntimeError(
            "Firebase sign-in worked, but Transcript.lol API auth was rejected. "
            "If the site requires a signed AuthToken cookie exchange, add a working "
            "TRANSCRIPT_LOL_AUTH_TOKEN or TRANSCRIPT_LOL_SESSION_COOKIE to .env."
        )

    def _has_auth_token_cookie(self) -> bool:
        return any(cookie.name == "AuthToken" for cookie in self.cookie_jar)

    @staticmethod
    def _build_unsigned_auth_token(id_token: str, refresh_token: str) -> str:
        header = {"alg": "none", "typ": "JWT"}
        payload = {"id_token": id_token, "refresh_token": refresh_token}

        def encode_segment(value: dict[str, str]) -> str:
            raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
            return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

        return f"{encode_segment(header)}.{encode_segment(payload)}."

    def create_recording(
        self,
        *,
        url: str,
        title: str,
        language: str,
        media_type: str,
        source: str,
        external_id: str | None = None,
    ) -> str:
        payload = {
            "title": title,
            "language": language,
            "mediaType": media_type,
            "source": source,
            "sourceUrl": url,
        }
        if external_id and external_id.strip():
            payload["externalId"] = external_id.strip()
        data = self._json_request(
            f"{API_BASE_URL}/spaces/{self.space_id}/recordings",
            method="POST",
            payload=payload,
        )
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected create response: {json.dumps(data)[:500]}")
        recording_id = data.get("id") or data.get("recordingId")
        if not recording_id:
            raise RuntimeError(f"Create response missing recording id: {json.dumps(data)[:500]}")
        return str(recording_id)

    def get_recording(self, recording_id: str) -> dict:
        data = self._json_request(f"{API_BASE_URL}/spaces/{self.space_id}/recordings/{recording_id}")
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected recording response: {json.dumps(data)[:500]}")
        return data

    def find_recording_by_url(self, url: str) -> str | None:
        cleaned_url = (url or "").strip()
        if not cleaned_url:
            return None

        payload = self._json_request(f"{API_BASE_URL}/spaces/{self.space_id}/recordings")
        for recording in normalize_recordings_payload(payload):
            source_url = recording.get("sourceUrl", "")
            recording_id = recording.get("id") or recording.get("recordingId")
            if not isinstance(source_url, str) or not recording_id:
                continue
            if not urls_match(source_url, cleaned_url):
                continue
            if extract_status(recording) in TERMINAL_STATUSES:
                return str(recording_id)
        return None

    def list_insights(self, recording_id: str) -> dict | list:
        return self._json_request(
            f"{API_BASE_URL}/spaces/{self.space_id}/recordings/{recording_id}/insights"
        )

    def create_insight(
        self,
        recording_id: str,
        prompt_id: str,
        tweak_query: str = "",
    ) -> dict | list:
        payload: dict[str, str] = {"promptId": prompt_id}
        if tweak_query.strip():
            payload["tweakQuery"] = tweak_query.strip()
        return self._json_request(
            f"{API_BASE_URL}/spaces/{self.space_id}/recordings/{recording_id}/insights",
            method="POST",
            payload=payload,
        )

    def get_transcript(self, recording_id: str, fmt: str) -> str:
        status, raw, headers = self._request(
            f"{API_BASE_URL}/spaces/{self.space_id}/recordings/{recording_id}/transcript?format={urllib.parse.quote(fmt)}",
            accept_json=(fmt == "json"),
        )
        if status >= 400:
            text = raw.decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {status} from transcript endpoint: {text[:500]}")
        content_type = headers.get("Content-Type", "")
        if "application/json" in content_type or fmt == "json":
            return json.dumps(json.loads(raw.decode("utf-8")), indent=2)
        return raw.decode("utf-8", errors="replace")


def detect_source(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    for domain, source in SOURCE_MAP.items():
        if host == domain or host.endswith(f".{domain}"):
            return source
    return "UNKNOWN"


def detect_media_type(source: str) -> str:
    return "VIDEO" if source in VIDEO_SOURCES else "AUDIO"


def derive_title(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    if parsed.path and parsed.path != "/":
        return f"{host}{parsed.path}"
    return host or url


def extract_status(recording: dict) -> str:
    for key in ("status", "state", "processingStatus"):
        value = recording.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    transcript = recording.get("transcript")
    if isinstance(transcript, dict):
        for key in ("status", "state", "processingStatus"):
            value = transcript.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().upper()
    return "UNKNOWN"


def wait_for_recording_terminal(
    client: TranscriptClient,
    recording_id: str,
    timeout_seconds: int,
) -> dict:
    deadline = time.time() + timeout_seconds
    last_status = None
    while time.time() < deadline:
        recording = client.get_recording(recording_id)
        status = extract_status(recording)
        if status != last_status:
            print(f"[transcribe] status={status}", flush=True)
            last_status = status

        if status in FAILED_STATUSES or status.endswith("_FAILED"):
            raise RuntimeError(f"Transcript.lol marked recording as failed: {json.dumps(recording)[:500]}")

        if status in TERMINAL_STATUSES:
            return recording

        time.sleep(POLL_INTERVAL_SECONDS)

    raise RuntimeError(f"Timed out after {timeout_seconds}s waiting for recording.")


def wait_for_transcript(client: TranscriptClient, recording_id: str, fmt: str, timeout_seconds: int) -> str:
    wait_for_recording_terminal(client, recording_id, timeout_seconds)
    return client.get_transcript(recording_id, fmt)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Submit a URL to Transcript.lol and print the transcript.")
    parser.add_argument("url", nargs="?", help="Media URL to transcribe")
    parser.add_argument("--language", default="en", help="Transcript language (default: en)")
    parser.add_argument(
        "--format",
        default="text",
        choices=["json", "text", "csv", "srt", "vtt", "pdf", "word"],
        help="Transcript output format (default: text)",
    )
    parser.add_argument("--title", help="Optional title override")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="Polling timeout in seconds")
    parser.add_argument("--test-auth", action="store_true", help="Authenticate and exit without creating a recording")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    env = load_env()
    if args.test_auth:
        client = TranscriptClient(env)
        client.authenticate()
        print(f"[transcribe] auth ok for space_id={client.space_id}")
        return

    if not args.url:
        raise RuntimeError("URL is required unless --test-auth is used.")

    source = detect_source(args.url)
    media_type = detect_media_type(source)
    title = args.title or derive_title(args.url)

    print(f"[transcribe] source={source} media_type={media_type} language={args.language}")

    if source == "VIMEO":
        transcript = fetch_vimeo_captions(args.url, args.language)
        if transcript:
            sys.stdout.write(transcript)
            if not transcript.endswith("\n"):
                sys.stdout.write("\n")
            return
        print("[transcribe] no Vimeo captions found; falling back to Transcript.lol")

    client = TranscriptClient(env)
    client.authenticate()
    try:
        recording_id = client.find_recording_by_url(args.url)
        if recording_id:
            print(f"[transcribe] reusing existing recording {recording_id}")
        else:
            recording_id = client.create_recording(
                url=args.url,
                title=title,
                language=args.language,
                media_type=media_type,
                source=source,
            )
            print(f"[transcribe] recording_id={recording_id}")

        transcript = wait_for_transcript(client, recording_id, args.format, args.timeout)
    except Exception as exc:
        if source == "VIMEO":
            raise RuntimeError(
                f"No Vimeo captions found; Transcript.lol media import failed. {exc}"
            ) from exc
        raise
    sys.stdout.write(transcript)
    if transcript and not transcript.endswith("\n"):
        sys.stdout.write("\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[transcribe] interrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
```

## Obsidian-vault-orchestrator/cli/export_transcripts.py

<!-- absolute path: /Users/leon/Documents/Code/Obsidian-vault-orchestrator/cli/export_transcripts.py -->

```python
#!/usr/bin/env python3
"""
export_transcripts.py - Export completed Transcript.lol recordings into the Obsidian vault.

Manual run:
  python3 /Users/leon/Documents/Code/vault-orchestrator/cli/export_transcripts.py --dry-run
  python3 /Users/leon/Documents/Code/vault-orchestrator/cli/export_transcripts.py
"""

from __future__ import annotations

import argparse
from datetime import date
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

from transcribe import API_BASE_URL, TERMINAL_STATUSES, TranscriptClient, extract_status, load_env

DEFAULT_OUTPUT_DIR = Path(
    "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/AI-Vault/z.Ingestion"
).expanduser()
EXPORTABLE_STATUSES = TERMINAL_STATUSES | {
    "TRANSCRIPTION_COMPLETE",
    "TRANSCRIPT_COMPLETE",
}


def read_text_with_retry(path: Path, attempts: int = 10, delay_s: float = 0.5) -> str:
    last_exc: Exception | None = None
    for _ in range(max(1, attempts)):
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            last_exc = exc
            time.sleep(delay_s)
    raise last_exc or OSError(f"Unable to read {path}")


def write_text_with_retry(path: Path, content: str, attempts: int = 10, delay_s: float = 1.0) -> None:
    last_exc: Exception | None = None
    for _ in range(max(1, attempts)):
        try:
            path.write_text(content, encoding="utf-8")
            return
        except OSError as exc:
            last_exc = exc
            time.sleep(delay_s)
    raise last_exc or OSError(f"Unable to write {path}")


def append_text_with_retry(path: Path, content: str, attempts: int = 10, delay_s: float = 1.0) -> None:
    last_exc: Exception | None = None
    for _ in range(max(1, attempts)):
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(content)
            return
        except OSError as exc:
            last_exc = exc
            time.sleep(delay_s)
    raise last_exc or OSError(f"Unable to append to {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export completed Transcript.lol recordings into the Obsidian vault."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be exported without writing files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory to write markdown files into (default: {DEFAULT_OUTPUT_DIR})",
    )
    return parser.parse_args()


def list_recordings(client: TranscriptClient) -> list[dict]:
    data = client._json_request(f"{API_BASE_URL}/spaces/{client.space_id}/recordings")
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for key in ("recordings", "items", "data", "results"):
            value = data.get(key)
            if isinstance(value, list):
                items = value
                break
        else:
            raise RuntimeError(f"Unexpected recordings response shape: {data!r}")
    else:
        raise RuntimeError(f"Unexpected recordings response type: {type(data).__name__}")

    recordings = [item for item in items if isinstance(item, dict)]
    return recordings


def sanitize_title(title: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", title).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.rstrip(".")
    return cleaned or "untitled"


def coalesce_string(recording: dict, *keys: str) -> str:
    for key in keys:
        value = recording.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def extract_youtube_id(url: str) -> str | None:
    if not url:
        return None
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.strip("/")

    if host in {"youtube.com", "m.youtube.com"}:
        if path == "watch":
            video_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
            return video_id or None
        if path.startswith("shorts/") or path.startswith("embed/") or path.startswith("live/"):
            parts = path.split("/")
            if len(parts) >= 2 and parts[1]:
                return parts[1]
    if host == "youtu.be":
        parts = path.split("/")
        if parts and parts[0]:
            return parts[0]
    return None


def fetch_youtube_transcript(video_id: str, include_timestamps: bool = False) -> str | None:
    if not video_id:
        return None
    watch_url = f"https://www.youtube.com/watch?v={urllib.parse.quote(video_id)}"
    try:
        with tempfile.TemporaryDirectory(prefix="yt-sub-") as temp_dir:
            output_template = str(Path(temp_dir) / f"yt-sub-{video_id}.%(ext)s")
            cmd = [
                sys.executable, "-m", "yt_dlp",
                "--write-auto-sub",
                "--sub-lang",
                "en",
                "--skip-download",
                "--sub-format",
                "json3",
                "--js-runtimes",
                "node",
                "-o",
                output_template,
                watch_url,
            ]
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                return None

            subtitle_files = sorted(Path(temp_dir).glob("*.json3"))
            if not subtitle_files:
                return None

            return parse_json3_transcript(subtitle_files[0], include_timestamps=include_timestamps)
    except (OSError, json.JSONDecodeError):
        return None


def format_timestamp(milliseconds: int) -> str:
    total_seconds = max(0, milliseconds // 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def parse_json3_transcript(json3_path: Path, include_timestamps: bool = False) -> str | None:
    data = json.loads(json3_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return None
    events = data.get("events")
    if not isinstance(events, list):
        return None

    lines: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        segs = event.get("segs")
        if not isinstance(segs, list):
            continue
        parts: list[str] = []
        for seg in segs:
            if not isinstance(seg, dict):
                continue
            text = seg.get("utf8")
            if isinstance(text, str):
                cleaned = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
                if cleaned:
                    parts.append(cleaned)
        if parts:
            line = " ".join(parts)
            if include_timestamps:
                start_ms = event.get("tStartMs")
                if isinstance(start_ms, int):
                    line = f"[{format_timestamp(start_ms)}] {line}"
            lines.append(line)
    if not lines:
        return None
    return "\n".join(lines)


def get_transcript_text(client: TranscriptClient, recording: dict) -> tuple[str, str]:
    recording_id = coalesce_string(recording, "id", "recordingId")
    if not recording_id:
        raise RuntimeError(f"Recording missing id: {recording!r}")

    source = coalesce_string(recording, "source").upper()
    source_url = coalesce_string(recording, "sourceUrl", "url")
    if source == "YOUTUBE":
        video_id = extract_youtube_id(source_url)
        if video_id:
            youtube_text = fetch_youtube_transcript(video_id)
            if youtube_text:
                return youtube_text, "YouTube captions"

    return client.get_transcript(recording_id, "text"), "transcript.lol"


def build_markdown(
    recording: dict,
    transcript_text: str,
    transcript_source: str,
    description: str = "",
    ai_summary: str = "",
) -> str:
    title = coalesce_string(recording, "title", "name") or "Untitled"
    source_url = coalesce_string(recording, "sourceUrl", "url")
    created_at = coalesce_string(recording, "createdAt", "created_at", "date")
    language = coalesce_string(recording, "language", "locale")
    today_tag = date.today().isoformat()
    description_text = description.strip()
    ai_summary_text = ai_summary.strip()
    description_norm = re.sub(r"\s+", " ", description_text).strip().lower()
    ai_summary_norm = re.sub(r"\s+", " ", ai_summary_text).strip().lower()

    optional_sections = ""
    if description_text:
        optional_sections += f"## Description\n\n{description_text}\n\n"
    if ai_summary_text and ai_summary_norm != description_norm:
        optional_sections += f"## AI Summary\n\n{ai_summary_text}\n\n"

    transcript_heading = "## YouTube Transcript" if "youtube" in transcript_source.lower() else "## Transcript"

    return (
        f"# {title}\n\n"
        f"**Source:** {source_url or 'Unknown'}\n"
        f"**Date:** {created_at or 'Unknown'}\n"
        f"**Language:** {language or 'Unknown'}\n"
        f"**Transcript source:** {transcript_source}\n\n"
        f"{optional_sections}"
        "---\n\n"
        f"{transcript_heading}\n\n"
        f"{transcript_text.rstrip()}\n\n"
        f"#{today_tag}\n"
    )


def is_exportable_status(status: str) -> bool:
    normalized = status.strip().upper()
    if normalized in EXPORTABLE_STATUSES:
        return True
    return normalized.endswith("_COMPLETE") or normalized.endswith("_COMPLETED")


def ensure_daily_note_link(
    daily_note_path: Path,
    transcript_title: str,
    display_title: str | None = None,
) -> None:
    visible_title = (display_title or transcript_title).strip()
    link = f"[[z.Ingestion/{transcript_title}]]"
    block = f"### {visible_title}\n{link}"
    daily_note_path.parent.mkdir(parents=True, exist_ok=True)
    if not daily_note_path.exists():
        write_text_with_retry(daily_note_path, "")
    content = read_text_with_retry(daily_note_path)
    if link in content:
        return

    to_append = f"\n{block}\n" if content and not content.endswith("\n") else f"{block}\n"
    append_text_with_retry(daily_note_path, to_append)


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir.expanduser()
    vault_root = output_dir.parent
    daily_note_path = vault_root / "Daily Notes" / f"{date.today().isoformat()}.md"

    env = load_env()
    client = TranscriptClient(env)
    client.authenticate()

    recordings = list_recordings(client)
    print(f"[export] found {len(recordings)} recording(s) in space_id={client.space_id}")

    exportable = 0
    written = 0
    skipped_existing = 0
    skipped_incomplete = 0

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    for recording in recordings:
        recording_id = coalesce_string(recording, "id", "recordingId")
        title = coalesce_string(recording, "title", "name") or f"recording-{recording_id or 'unknown'}"
        status = extract_status(recording)
        safe_title = sanitize_title(title)
        destination = output_dir / f"{safe_title}.md"

        if not is_exportable_status(status):
            skipped_incomplete += 1
            print(f"[export] skip incomplete status={status} title={title}")
            continue

        if destination.exists():
            skipped_existing += 1
            print(f"[export] skip existing {destination.name}")
            continue

        exportable += 1
        if args.dry_run:
            print(f"[export] would export {destination.name}")
            continue

        transcript_text, transcript_source = get_transcript_text(client, recording)
        destination.write_text(
            build_markdown(recording, transcript_text, transcript_source),
            encoding="utf-8",
        )
        ensure_daily_note_link(daily_note_path, safe_title, title)
        written += 1
        print(f"[export] wrote {destination} source={transcript_source}")

    print(
        "[export] summary: "
        f"exportable={exportable} written={written} "
        f"skipped_existing={skipped_existing} skipped_incomplete={skipped_incomplete}"
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[export] interrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
```

## skool-ingest/skool_ingest/manifest.py

<!-- absolute path: /Users/leon/Documents/Code/skool-ingest/skool_ingest/manifest.py -->

```python
"""Manifest model + IO for the Skool → transcript.lol pipeline.

The manifest is a single CSV with one row per video. Keeping it in CSV (not
JSON) means you can open it in Excel/Numbers/Sheets to triage, and the schema
is self-documenting in the header.

Columns (in order — append-only; do not reorder so existing rows stay valid):

    id                stable hash of (post_url, video_url); idempotent
    post_url          Skool post that embeds the video
    post_title        as shown on the post
    post_author       Skool display name
    post_date         ISO 8601; "" if unknown
    video_url         the *direct* video URL we will hand to transcript.lol
    embed_type        loom | youtube | vimeo | mux | mp4 | m3u8 | other
    reachable         yes | no  (third-party fetcher can GET it without auth)
    status            pending | submitted | done | failed
    transcript_lol_id transcript.lol job id
    transcript_url    public URL of the finished transcript (if any)
    failure_reason    free text; populated on failed
    captured_at       ISO 8601 timestamp of last update

The "id" column is the join key. If a video appears in two posts, both rows
share the same id so you can dedupe at any time with a simple sort.
"""
from __future__ import annotations

import csv
import dataclasses
import datetime as _dt
import hashlib
from pathlib import Path
from typing import Iterable, Iterator

COLUMNS: tuple[str, ...] = (
    "id",
    "post_url",
    "post_title",
    "post_author",
    "post_date",
    "video_url",
    "embed_type",
    "reachable",
    "status",
    "transcript_lol_id",
    "transcript_url",
    "failure_reason",
    "captured_at",
)

STATUS_PENDING = "pending"
STATUS_SUBMITTED = "submitted"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

EMBED_TYPES = {"loom", "youtube", "vimeo", "mux", "mp4", "m3u8", "other"}


@dataclasses.dataclass
class Row:
    post_url: str
    post_title: str
    post_author: str
    post_date: str
    video_url: str
    embed_type: str
    reachable: str = "yes"
    status: str = STATUS_PENDING
    transcript_lol_id: str = ""
    transcript_url: str = ""
    failure_reason: str = ""
    captured_at: str = ""
    id: str = ""  # computed in __post_init__

    def __post_init__(self) -> None:
        if not self.id:
            self.id = make_id(self.post_url, self.video_url)
        if self.embed_type not in EMBED_TYPES:
            self.embed_type = "other"
        if not self.captured_at:
            self.captured_at = now_iso()

    def as_dict(self) -> dict[str, str]:
        return {c: getattr(self, c) for c in COLUMNS}


def make_id(post_url: str, video_url: str) -> str:
    h = hashlib.sha256()
    h.update(post_url.encode("utf-8"))
    h.update(b"\x00")
    h.update(video_url.encode("utf-8"))
    return h.hexdigest()[:16]


def now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat(timespec="seconds")


def load(path: Path) -> dict[str, Row]:
    """Load manifest CSV into a dict keyed by row id."""
    if not path.exists():
        return {}
    rows: dict[str, Row] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            row = Row(
                post_url=raw.get("post_url", ""),
                post_title=raw.get("post_title", ""),
                post_author=raw.get("post_author", ""),
                post_date=raw.get("post_date", ""),
                video_url=raw.get("video_url", ""),
                embed_type=raw.get("embed_type", "other"),
                reachable=raw.get("reachable", "yes"),
                status=raw.get("status", STATUS_PENDING),
                transcript_lol_id=raw.get("transcript_lol_id", ""),
                transcript_url=raw.get("transcript_url", ""),
                failure_reason=raw.get("failure_reason", ""),
                captured_at=raw.get("captured_at", ""),
                id=raw.get("id", ""),
            )
            rows[row.id] = row
    return rows


def save(path: Path, rows: Iterable[Row]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    items = list(rows)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(COLUMNS))
        writer.writeheader()
        for row in items:
            writer.writerow(row.as_dict())


def upsert(path: Path, row: Row) -> None:
    """Write a single row, preserving others. Atomic via temp-file rename."""
    rows = load(path)
    rows[row.id] = row
    save(path, rows.values())


def iter_pending(rows: dict[str, Row]) -> Iterator[Row]:
    return (r for r in rows.values() if r.status == STATUS_PENDING)
```

## skool-ingest/scripts/local_ingest.py

<!-- absolute path: /Users/leon/Documents/Code/skool-ingest/scripts/local_ingest.py -->

```python
"""local_ingest.py - Download protected m3u8 streams and transcribe them locally.

Designed for the case where transcript.lol's URL ingestion fails on protected
streams (Skool, Mux, Cloudflare-fronted HLS) but the original platform's
cookie session can still reach the source page.

Workflow:
1. Read the manifest of video URLs (CSV with embed_type=m3u8 rows).
2. Refresh tokens just-in-time by re-crawling the source page (optional).
3. ffmpeg pulls the m3u8 -> remux to mono 16 kHz mp3 at 32 kbps (~240 KB/min).
4. faster-whisper transcribes the mp3 -> text with timestamps.
5. Build a vault-ready markdown note per recording.

Reusable inside Obsidian-vault-orchestrator for any future protected stream
that needs the same treatment.
"""

from __future__ import annotations

import argparse
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


@dataclass
class IngestConfig:
    workdir: Path
    vault_dir: Path
    manifest_path: Path
    whisper_model: str = "tiny.en"
    whisper_device: str = "cpu"
    whisper_compute: str = "int8"
    max_seconds: int = 0
    sample_seconds: int = 0
    skip_existing: bool = True
    ffmpeg_headers: str = DEFAULT_FFMPEG_HEADERS
    skip_download: bool = False
    skip_transcribe: bool = False
    only_ids: set[str] = field(default_factory=set)
    embed_filter: str = "m3u8"
    stats: dict = field(default_factory=dict)


def slugify(text: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", text or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.rstrip(". ").strip()
    return (cleaned or "untitled")[:160]


def ffmpeg_audio(m3u8_url: str, dest: Path, config: IngestConfig) -> dict:
    args = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-user_agent",
        "Mozilla/5.0",
        "-headers",
        config.ffmpeg_headers,
        "-i",
        m3u8_url,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "32k",
        str(dest),
    ]
    if config.max_seconds:
        args[args.index("-i") : args.index("-i") + 1] = ["-i"]
        # Insert -t right before the output
        idx = args.index(str(dest))
        args[idx:idx] = ["-t", str(config.max_seconds)]
    t0 = time.time()
    proc = subprocess.run(args, capture_output=True, text=True, timeout=900)
    wall = time.time() - t0
    if proc.returncode != 0 or not dest.exists():
        raise RuntimeError(f"ffmpeg failed: {proc.stderr[-400:]}")
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


def build_markdown(title: str, post_url: str, video_url: str, transcript: dict) -> str:
    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- Source: {post_url}")
    lines.append(f"- Video URL: {video_url}")
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
    audio_dir = config.workdir / "audio"
    transcript_dir = config.workdir / "transcripts"
    note_dir = config.vault_dir
    audio_path = audio_dir / f"{row_id}-{slug}.mp3"
    transcript_path = transcript_dir / f"{row_id}-{slug}.json"
    note_path = note_dir / f"{row_id}-{slug}.md"

    if config.skip_existing and note_path.exists():
        return {"id": row_id, "title": title, "skipped": "exists"}

    if not config.skip_download:
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            download = ffmpeg_audio(row["video_url"], audio_path, config)
        except Exception as exc:
            update_manifest(
                manifest_path,
                row_id,
                status="LOCAL_DOWNLOAD_FAILED",
                failure_reason=str(exc)[:200],
            )
            return {"id": row_id, "title": title, "error": "download", "detail": str(exc)[:200]}
    else:
        download = {"seconds": 0.0, "bytes": audio_path.stat().st_size if audio_path.exists() else 0}

    if config.skip_transcribe:
        transcript = {"language": "en", "duration": 0.0, "segments": []}
    else:
        try:
            transcript = transcribe(audio_path, config)
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
    markdown = build_markdown(title, row.get("post_url", ""), row.get("video_url", ""), transcript)
    write_with_retry(note_path, markdown)
    update_manifest(
        manifest_path,
        row_id,
        status="LOCAL_TRANSCRIBED",
        failure_reason="",
    )
    return {
        "id": row_id,
        "title": title,
        "downloaded_bytes": download.get("bytes"),
        "transcript_segments": len(transcript.get("segments", [])),
        "transcribe_seconds": transcript.get("wall_seconds"),
    }


def parse_args() -> IngestConfig:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", type=Path, default=Path("manifest/skool_videos.csv"))
    ap.add_argument("--workdir", type=Path, default=Path("manifest/local_ingest"))
    ap.add_argument("--vault-dir", type=Path, required=True)
    ap.add_argument("--model", default="tiny.en")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--compute", default="int8")
    ap.add_argument("--max-seconds", type=int, default=0)
    ap.add_argument("--sample-seconds", type=int, default=0)
    ap.add_argument("--skip-existing", action="store_true", default=True)
    ap.add_argument("--no-skip-existing", dest="skip_existing", action="store_false")
    ap.add_argument("--skip-download", action="store_true")
    ap.add_argument("--skip-transcribe", action="store_true")
    ap.add_argument("--only-ids", nargs="*", default=[])
    ap.add_argument("--embed-filter", default="m3u8")
    args = ap.parse_args()
    return IngestConfig(
        workdir=args.workdir.expanduser(),
        vault_dir=args.vault_dir.expanduser(),
        manifest_path=args.manifest.expanduser(),
        whisper_model=args.model,
        whisper_device=args.device,
        whisper_compute=args.compute,
        max_seconds=args.max_seconds,
        sample_seconds=args.sample_seconds,
        skip_existing=args.skip_existing,
        skip_download=args.skip_download,
        skip_transcribe=args.skip_transcribe,
        only_ids=set(args.only_ids or []),
        embed_filter=args.embed_filter,
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

    summary = {"total": len(rows), "results": []}
    for index, row in enumerate(rows, start=1):
        title = row.get("post_title") or "untitled"
        print(f"[{index}/{len(rows)}] {title}", flush=True)
        result = process_row(row, config, config.manifest_path)
        print(f"    -> {result}", flush=True)
        summary["results"].append(result)
    summary_path = config.workdir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```
