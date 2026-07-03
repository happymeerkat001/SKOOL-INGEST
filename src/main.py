"""FastAPI webhook for the AI lead qualification engine.

Stateless per-request — n8n owns lead state. Sub-20s speed-to-lead SLA is
measured per request and reported in the response body and X-Latency-MS header.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

from agent_core.agents import LeadContext
from agent_core.router import (
    OpenPhoneDispatcher,
    OrchestratorRouter,
    n8n_callback_payload,
    n8n_headers,
)

load_dotenv()

SLA_MS = 20_000
DEFAULT_AUDIT_LOG = Path("logs/engine_dispatch.jsonl")
logger = logging.getLogger("agent_core")


@dataclass(frozen=True)
class EngineConfig:
    mode: str
    openphone_api_key: str
    openphone_from_number: str
    webhook_token: str
    gemini_api_key: str
    gemini_model: str
    n8n_webhook_url: str
    n8n_api_key: str
    audit_log_path: Path

    @property
    def live(self) -> bool:
        return self.mode == "live"


def load_engine_config() -> EngineConfig:
    mode = os.environ.get("ENGINE_MODE", "simulation").strip().lower()
    if mode != "live":
        mode = "simulation"
    return EngineConfig(
        mode=mode,
        openphone_api_key=os.environ.get("OPENPHONE_API_KEY", "").strip(),
        openphone_from_number=os.environ.get("OPENPHONE_FROM_NUMBER", "").strip(),
        webhook_token=os.environ.get("ENGINE_WEBHOOK_TOKEN", "").strip(),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", "").strip(),
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash").strip(),
        n8n_webhook_url=os.environ.get("N8N_WEBHOOK_URL", "").strip(),
        n8n_api_key=os.environ.get("N8N_API_KEY", "").strip(),
        audit_log_path=Path(os.environ.get("ENGINE_AUDIT_LOG", str(DEFAULT_AUDIT_LOG))),
    )


def validate_engine_config(config: EngineConfig) -> None:
    if not config.live:
        if not config.webhook_token:
            logger.warning("ENGINE_WEBHOOK_TOKEN unset; simulation webhook accepts local unauthenticated POSTs")
        return
    missing = []
    if not config.openphone_api_key:
        missing.append("OPENPHONE_API_KEY")
    if not config.openphone_from_number:
        missing.append("OPENPHONE_FROM_NUMBER")
    if not config.webhook_token:
        missing.append("ENGINE_WEBHOOK_TOKEN")
    if missing:
        raise RuntimeError(f"ENGINE_MODE=live requires: {', '.join(missing)}")


def integration_summary(config: EngineConfig) -> dict[str, str]:
    return {
        "mode": config.mode,
        "openphone": "live" if config.live and config.openphone_api_key else "simulated",
        "gemini": "live" if config.gemini_api_key else "disabled",
        "n8n": "live" if config.n8n_webhook_url else "disabled",
        "webhook_auth": "enabled" if config.webhook_token else "disabled",
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_engine_config()
    validate_engine_config(config)
    logger.info("engine integration summary: %s", integration_summary(config))
    async with httpx.AsyncClient() as client:
        app.state.config = config
        app.state.client = client
        app.state.router = OrchestratorRouter(
            api_key=config.gemini_api_key or None, client=client
        )
        app.state.dispatcher = OpenPhoneDispatcher(
            api_key=config.openphone_api_key,
            from_number=config.openphone_from_number,
            live=config.live,
            client=client,
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


def verify_webhook_token(config: EngineConfig, token: str | None) -> None:
    if config.webhook_token and token != config.webhook_token:
        raise HTTPException(status_code=401, detail="invalid engine webhook token")


def append_audit_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def audit_record(
    lead: InboundLead,
    result,
    dispatch: dict | None,
    mode: str,
    latency_ms: float,
) -> dict:
    status = "not_attempted"
    if dispatch:
        if dispatch.get("sent"):
            status = "sent"
        elif dispatch.get("simulated"):
            status = "simulated"
        elif dispatch.get("error"):
            status = "error"
    return {
        "lead_id": lead.lead_id,
        "gate": result.gate,
        "passed": result.passed,
        "deterministic": result.deterministic,
        "mode": mode,
        "sent": bool(dispatch and dispatch.get("sent")),
        "simulated": bool(dispatch and dispatch.get("simulated")),
        "error": dispatch.get("error", "") if dispatch else "",
        "status": status,
        "latency_ms": round(latency_ms, 1),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.post("/webhook/fb-inbound")
async def fb_inbound(
    lead: InboundLead,
    response: Response,
    x_engine_token: str | None = Header(default=None),
) -> dict:
    t0 = time.perf_counter()
    config = app.state.config
    verify_webhook_token(config, x_engine_token)
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

    try:
        append_audit_record(
            config.audit_log_path,
            audit_record(lead, result, dispatch, config.mode, latency_ms),
        )
    except OSError as exc:
        logger.warning("dispatch audit write failed: lead=%s err=%s", lead.lead_id, exc)

    n8n_url = config.n8n_webhook_url
    if n8n_url:
        try:
            await app.state.client.post(
                n8n_url,
                headers=n8n_headers(config.n8n_api_key),
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
