"""REST client for transcript.lol v1 API.

The real contract (as published at https://transcript.lol/docs):

  Base URL:  https://transcript.lol/api/v1
  Auth:      Authorization: Bearer <api_key>
  Resources: Account / Workspace (space) / Folder / Recording / Transcript / Translation

The flow we use:

  1. GET  /account                                         → confirm auth, get user info
  2. GET  /spaces                                          → list existing workspaces
  3. POST /spaces                                          → create a "skool-ingest" workspace
  4. POST /spaces/{spaceId}/recordings                     → submit a URL
  5. GET  /spaces/{spaceId}/recordings/{recordingId}       → poll status
  6. GET  /spaces/{spaceId}/recordings/{recordingId}/transcript → fetch final text

This client does NOT assume any of the field names from the docs are
guaranteed (the docs are prerendered HTML; the actual JSON shapes are
not publicly exposed as an OpenAPI spec). The code is defensive: it
accepts a few common variants and falls back to raw ``data`` in the
Job so callers can inspect whatever the API actually returns.

Notes:

    * The v1 API requires a ``spaceId`` (workspace) for everything,
      so we cache the chosen one on first use.
    * Transcript text is returned in a separate endpoint and may
      require polling. The ``wait()`` helper does that with exponential
      backoff.
    * All write methods raise ``TranscriptLolError`` on non-2xx. The
      runner decides how to record failures in the manifest.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

DEFAULT_BASE_URL = "https://transcript.lol/api/v1"


class TranscriptLolError(RuntimeError):
    """Raised for any non-2xx response from transcript.lol."""


@dataclass
class Job:
    id: str
    space_id: str
    status: str          # e.g. "queued" | "processing" | "done" | "failed"
    transcript_url: str | None = None
    text: str | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class TranscriptLol:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("TRANSCRIPT_LOL_API_KEY", "")
        if not self.api_key:
            raise TranscriptLolError(
                "TRANSCRIPT_LOL_API_KEY is empty. Paste your key into .env first."
            )
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )
        self._cached_space_id: str | None = None

    # ----- public API ----------------------------------------------------

    def get_account(self) -> dict[str, Any]:
        """Verify auth + retrieve account info (calls ``/me`` per the real v1 spec)."""
        return self._get("/me")

    def list_spaces(self) -> list[dict[str, Any]]:
        """List existing workspaces."""
        data = self._get("/spaces")
        return self._extract_list(data)

    def create_space(self, name: str) -> dict[str, Any]:
        """Create a new workspace. Returns the new space object."""
        return self._post("/spaces", json={"name": name})

    def ensure_space(self, name: str = "skool-ingest") -> str:
        """Return the first existing space with this name, or create one. Caches."""
        if self._cached_space_id:
            return self._cached_space_id
        for sp in self.list_spaces():
            if sp.get("name") == name:
                sid = str(sp.get("id") or sp.get("spaceId") or "")
                if sid:
                    self._cached_space_id = sid
                    return sid
        created = self.create_space(name)
        sid = str(created.get("id") or created.get("spaceId") or "")
        if not sid:
            raise TranscriptLolError(
                f"created space but no id in response: {created!r}"
            )
        self._cached_space_id = sid
        return sid

    def submit(self, url: str, *, language: str | None = None,
               space_id: str | None = None) -> Job:
        """Submit a remote URL for transcription. Returns the queued Job."""
        sid = space_id or self.ensure_space()
        payload: dict[str, Any] = {"url": url}
        if language:
            payload["language"] = language
        data: Any = self._post(f"/spaces/{sid}/recordings", json=payload)
        return self._parse_job(data, sid)

    def fetch(self, job_id: str, *, space_id: str | None = None) -> Job:
        """Fetch current state of a previously submitted recording."""
        sid = space_id or self.ensure_space()
        data = self._get(f"/spaces/{sid}/recordings/{job_id}")
        return self._parse_job(data, sid)

    def get_transcript(self, job_id: str, *, space_id: str | None = None) -> str:
        """Fetch the finalized transcript text."""
        sid = space_id or self.ensure_space()
        data = self._get(f"/spaces/{sid}/recordings/{job_id}/transcript")
        if isinstance(data, str):
            return data
        if isinstance(data, list):
            # Some APIs return a list of segments; join them.
            return " ".join(str(x) for x in data)
        if isinstance(data, dict):
            for k in ("text", "transcript", "content", "body"):
                if isinstance(data.get(k), str):
                    return data[k]
            # Some APIs return { data: { text: "..." } }
            inner = data.get("data")
            if isinstance(inner, dict):
                for k in ("text", "transcript", "content", "body"):
                    if isinstance(inner.get(k), str):
                        return inner[k]
        return str(data)

    def wait(
        self,
        job_id: str,
        *,
        space_id: str | None = None,
        poll_every: float = 5.0,
        max_wait: float = 900.0,
        on_poll: Callable[[Job], None] | None = None,
    ) -> Job:
        """Poll ``fetch`` until the job is terminal or ``max_wait`` elapses."""
        sid = space_id or self.ensure_space()
        deadline = time.monotonic() + max_wait
        attempt = 0
        while True:
            job = self.fetch(job_id, space_id=sid)
            if on_poll is not None:
                on_poll(job)
            if job.status in ("done", "completed", "finished", "succeeded"):
                # Try to fetch the transcript text
                try:
                    job.text = self.get_transcript(job_id, space_id=sid)
                except TranscriptLolError as exc:
                    job.error = f"transcript fetch failed: {exc}"
                return job
            if job.status in ("failed", "error", "canceled", "cancelled"):
                return job
            if time.monotonic() >= deadline:
                raise TranscriptLolError(
                    f"transcript.lol job {job_id} did not finish within {max_wait}s "
                    f"(last status={job.status})"
                )
            attempt += 1
            # Exponential backoff up to 30s, cap
            sleep = min(poll_every * (2 ** min(attempt, 4)), 30.0)
            time.sleep(sleep)

    # ----- internals -----------------------------------------------------

    def _post(self, path: str, *, json: dict[str, Any]) -> dict[str, Any]:
        resp = self._session.post(f"{self.base_url}{path}", json=json, timeout=self.timeout)
        return self._check(resp)

    def _get(self, path: str) -> dict[str, Any] | list[Any] | str:
        resp = self._session.get(f"{self.base_url}{path}", timeout=self.timeout)
        return self._check(resp)

    @staticmethod
    def _check(resp: requests.Response) -> dict[str, Any] | list[Any] | str:
        if not resp.ok:
            raise TranscriptLolError(
                f"transcript.lol {resp.request.method} {resp.url} → "
                f"{resp.status_code}: {resp.text[:500]}"
            )
        if not resp.content:
            return {}
        try:
            parsed: Any = resp.json()
            return parsed
        except ValueError:
            # Some endpoints (e.g. /transcript) might return raw text
            return resp.text

    @staticmethod
    def _extract_list(data: dict[str, Any] | list[Any] | str) -> list[dict[str, Any]]:
        """Extract a list from a possibly-wrapped API response."""
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            for k in ("data", "items", "spaces", "results"):
                v = data.get(k)
                if isinstance(v, list):
                    return [x for x in v if isinstance(x, dict)]
        return []

    @staticmethod
    def _parse_job(data: Any, space_id: str) -> Job:
        if not isinstance(data, dict):
            return Job(id="", space_id=space_id, status="unknown", raw={"_data": data})
        # Status is sometimes nested under "data" or "status"
        status = (
            data.get("status")
            or (data.get("data") or {}).get("status")
            or "unknown"
        )
        rid = str(
            data.get("id")
            or data.get("recordingId")
            or data.get("recording_id")
            or (data.get("data") or {}).get("id")
            or ""
        )
        return Job(
            id=rid,
            space_id=space_id,
            status=str(status).lower(),
            transcript_url=(
                data.get("transcriptUrl")
                or data.get("transcript_url")
                or (data.get("data") or {}).get("transcriptUrl")
            ),
            error=data.get("error") or (data.get("data") or {}).get("error"),
            raw=data,
        )
