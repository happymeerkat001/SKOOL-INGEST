"""FastAPI webhook for the AI lead qualification engine.

Stateless per-request — n8n owns lead state. Sub-20s speed-to-lead SLA is
measured per request and reported in the response body and X-Latency-MS header.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx
import uvicorn
from fastapi import FastAPI, Response
from pydantic import BaseModel, Field

from agent_core.agents import LeadContext
from agent_core.router import (
    OpenPhoneDispatcher,
    OrchestratorRouter,
    n8n_callback_payload,
    n8n_headers,
)

SLA_MS = 20_000
logger = logging.getLogger("agent_core")


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient() as client:
        app.state.client = client
        app.state.router = OrchestratorRouter(
            api_key=os.environ.get("GEMINI_API_KEY"), client=client
        )
        app.state.dispatcher = OpenPhoneDispatcher(
            api_key=os.environ.get("OPENPHONE_API_KEY"), client=client
        )
        yield


app = FastAPI(title="Lead Qualification Engine", lifespan=lifespan)


class Message(BaseModel):
    text: str


class Metadata(BaseModel):
    rent: float
    phone: str | None = None
    stage: int = 1
    occupants: int = 1
    has_pets: bool = False
    has_kids: bool = False
    commute_minutes: int | None = None
    move_in_days: int | None = None
    monthly_income: float | None = None


class InboundLead(BaseModel):
    lead_id: str
    messages: list[Message] = Field(default_factory=list)
    metadata: Metadata


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.post("/webhook/fb-inbound")
async def fb_inbound(lead: InboundLead, response: Response) -> dict:
    t0 = time.perf_counter()
    meta = lead.metadata
    stage = meta.stage if meta.stage else min(len(lead.messages), 3) or 1
    ctx = LeadContext(
        rent=meta.rent,
        stage=stage,
        occupants=meta.occupants,
        has_pets=meta.has_pets,
        has_kids=meta.has_kids,
        commute_minutes=meta.commute_minutes,
        move_in_days=meta.move_in_days,
        monthly_income=meta.monthly_income,
    )
    last_message = lead.messages[-1].text if lead.messages else ""
    result = await app.state.router.route(ctx, last_message)

    dispatch = None
    if meta.phone:
        dispatch = await app.state.dispatcher.send(recipient=meta.phone, body=result.reply)

    latency_ms = (time.perf_counter() - t0) * 1000
    sla_met = latency_ms < SLA_MS
    if not sla_met:
        logger.warning("SLA breach: lead=%s latency_ms=%.0f", lead.lead_id, latency_ms)
    response.headers["X-Latency-MS"] = f"{latency_ms:.1f}"

    n8n_url = os.environ.get("N8N_WEBHOOK_URL")
    if n8n_url:
        try:
            await app.state.client.post(
                n8n_url,
                headers=n8n_headers(os.environ.get("N8N_API_KEY")),
                json=n8n_callback_payload(lead.lead_id, result, latency_ms),
                timeout=10.0,
            )
        except httpx.HTTPError as exc:
            logger.warning("n8n callback failed: lead=%s err=%s", lead.lead_id, exc)

    return {
        "lead_id": lead.lead_id,
        "reply": result.reply,
        "gate": result.gate,
        "passed": result.passed,
        "deterministic": result.deterministic,
        "latency_ms": round(latency_ms, 1),
        "sla_met": sla_met,
        "dispatch": dispatch,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
