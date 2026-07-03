"""Orchestrator router: dispatch one inbound lead message to exactly one gate agent.

Deterministic-first: failed gates return the compliance template untouched and
never touch the network. Passing gates are optionally personalized with one
Gemini REST call via httpx; any error falls back to the raw template.
"""

from __future__ import annotations

import os

import httpx

from agent_core.agents import (
    GateResult,
    IncomeVerifier,
    LeadContext,
    OccupancyEvaluator,
    ShowingCoordinator,
)

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

# --- Outbound network contracts (OpenPhone/Quo + self-hosted n8n) -------------

OPENPHONE_ENDPOINT = "https://api.quo.com/v1/messages"
SIMULATION_WARNING = "WARN: ENGINE_MODE is not live. Operating in local simulation mode."


def openphone_payload(
    recipient: str,
    body: str,
    sender: str,
    media: list[str] | None = None,
) -> dict:
    """Exact Quo/OpenPhone v1 text message body: content, from, to[].

    Verified against the public OpenAPI spec linked from https://www.openphone.com/docs:
    https://openphone-public-api-prod.s3.us-west-2.amazonaws.com/public/openphone-public-api-v1-prod.json
    """
    if media:
        raise ValueError("Quo/OpenPhone v1 text messages do not accept media attachments")
    return {"content": body, "from": sender, "to": [recipient]}


def openphone_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": api_key, "Content-Type": "application/json"}


def n8n_callback_payload(lead_id: str, result: "GateResult", latency_ms: float) -> dict:
    """Body posted back to the self-hosted n8n workflow webhook (n8n owns lead state)."""
    return {
        "lead_id": lead_id,
        "reply": result.reply,
        "gate": result.gate,
        "passed": result.passed,
        "deterministic": result.deterministic,
        "latency_ms": round(latency_ms, 1),
    }


def n8n_headers(api_key: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-N8N-API-KEY"] = api_key
    return headers


class OpenPhoneDispatcher:
    """Sends qualified replies through OpenPhone/Quo when explicitly live.

    Any non-live mode degrades to local simulation even when credentials exist.
    """

    def __init__(
        self,
        api_key: str | None = None,
        from_number: str | None = None,
        live: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.from_number = (from_number or "").strip()
        self.live = live
        self.client = client

    async def send(self, recipient: str, body: str, media: list[str] | None = None) -> dict:
        payload = openphone_payload(recipient, body, self.from_number, media)
        if not self.live or not self.api_key or self.client is None:
            print(SIMULATION_WARNING)
            print(payload)
            return {"sent": False, "simulated": True, "payload": payload}
        try:
            resp = await self.client.post(
                OPENPHONE_ENDPOINT,
                headers=openphone_headers(self.api_key),
                json=payload,
                timeout=10.0,
            )
            resp.raise_for_status()
            return {
                "sent": True,
                "simulated": False,
                "payload": payload,
                "status": resp.status_code,
            }
        except httpx.HTTPError as exc:
            return {"sent": False, "simulated": False, "payload": payload, "error": str(exc)}
GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)
PERSONALIZE_INSTRUCTION = (
    "Rewrite the following leasing reply warmly. Keep every factual constraint "
    "exactly as stated. Maximum 2 sentences. Reply with the rewritten text only."
)


class OrchestratorRouter:
    def __init__(
        self,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.client = client
        self.dispatch: dict[int, object] = {
            1: OccupancyEvaluator(),
            2: ShowingCoordinator(),
            3: IncomeVerifier(),
        }

    async def route(self, ctx: LeadContext, last_message: str = "") -> GateResult:
        stage = ctx.stage if ctx.stage in self.dispatch else 1
        result = self.dispatch[stage].evaluate(ctx)
        if not result.passed:
            return result
        if self.api_key and self.client is not None and last_message:
            reply = await self._personalize(result.reply, last_message)
            if reply != result.reply:
                return GateResult(
                    passed=True, reply=reply, gate=result.gate, deterministic=False
                )
        return result

    async def _personalize(self, template: str, last_message: str) -> str:
        payload = {
            "system_instruction": {"parts": [{"text": PERSONALIZE_INSTRUCTION}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": f"Lead said: {last_message}\nReply to send: {template}"}
                    ],
                }
            ],
        }
        try:
            resp = await self.client.post(
                GEMINI_ENDPOINT,
                params={"key": self.api_key},
                json=payload,
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            return text or template
        except (httpx.HTTPError, KeyError, IndexError, ValueError):
            return template
