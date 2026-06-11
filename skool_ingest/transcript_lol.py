"""Thin REST client for transcript.lol.

The public docs are linked from https://transcript.lol → "API Docs" in the
footer. Two endpoints we use:

    POST /api/transcript            submit a URL / upload
    GET  /api/transcript/{id}       poll job status / fetch result

This client intentionally has *no* retry magic: callers decide how to handle
failures so they can mark the manifest row appropriately.

Notes for the future self reading this:

- ``submit`` returns a job id. ``fetch`` polls that id. Both raise on non-2xx.
- We never read the API key from the keychain or write it to disk; the caller
  passes it in (or it comes from ``.env`` at process start).
- transcript.lol's tier caps live in the user's account, not the API. We
  surface ``get_account()`` so the runner can warn the user *before* they
  burn through their free tier.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

DEFAULT_BASE_URL = "https://transcript.lol"


class TranscriptLolError(RuntimeError):
    """Raised for any non-2xx response from transcript.lol."""


@dataclass
class Job:
    id: str
    status: str          # e.g. "queued" | "processing" | "done" | "failed"
    transcript_url: str | None = None
    text: str | None = None
    error: str | None = None
    raw: dict[str, Any] | None = None


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
            }
        )

    # ----- public API ----------------------------------------------------

    def submit(self, url: str, *, language: str | None = None) -> Job:
        """Submit a remote URL for transcription. Returns the queued Job."""
        payload: dict[str, Any] = {"url": url}
        if language:
            payload["language"] = language
        data = self._post("/api/transcript", json=payload)
        return self._parse_job(data)

    def fetch(self, job_id: str) -> Job:
        """Fetch current state of a previously submitted job."""
        data = self._get(f"/api/transcript/{job_id}")
        return self._parse_job(data)

    def wait(
        self,
        job_id: str,
        *,
        poll_every: float = 5.0,
        max_wait: float = 600.0,
        on_poll: "Callable[[Job], None] | None" = None,
    ) -> Job:
        """Poll ``fetch`` until the job is terminal or ``max_wait`` elapses."""
        deadline = time.monotonic() + max_wait
        while True:
            job = self.fetch(job_id)
            if on_poll is not None:
                on_poll(job)
            if job.status in ("done", "failed", "error"):
                return job
            if time.monotonic() >= deadline:
                raise TranscriptLolError(
                    f"transcript.lol job {job_id} did not finish within {max_wait}s "
                    f"(last status={job.status})"
                )
            time.sleep(poll_every)

    def get_account(self) -> dict[str, Any]:
        """Best-effort account lookup. Endpoint may not exist on every tier."""
        return self._get("/api/account")

    # ----- internals -----------------------------------------------------

    def _post(self, path: str, *, json: dict[str, Any]) -> dict[str, Any]:
        resp = self._session.post(f"{self.base_url}{path}", json=json, timeout=self.timeout)
        return self._check(resp)

    def _get(self, path: str) -> dict[str, Any]:
        resp = self._session.get(f"{self.base_url}{path}", timeout=self.timeout)
        return self._check(resp)

    @staticmethod
    def _check(resp: requests.Response) -> dict[str, Any]:
        if not resp.ok:
            raise TranscriptLolError(
                f"transcript.lol {resp.request.method} {resp.url} → "
                f"{resp.status_code}: {resp.text[:500]}"
            )
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as exc:
            raise TranscriptLolError(
                f"transcript.lol returned non-JSON body: {resp.text[:200]}"
            ) from exc

    @staticmethod
    def _parse_job(data: dict[str, Any]) -> Job:
        return Job(
            id=str(data.get("id") or data.get("job_id") or ""),
            status=str(data.get("status") or "unknown"),
            transcript_url=data.get("transcript_url") or data.get("url"),
            text=data.get("text") or data.get("transcript"),
            error=data.get("error"),
            raw=data,
        )
