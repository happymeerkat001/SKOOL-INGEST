"""Tests for the AI lead qualification engine (src/agent_core + src/main)."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agent_core.agents import (  # noqa: E402
    INCOME_MULTIPLIER,
    IncomeVerifier,
    LeadContext,
    OccupancyEvaluator,
    ShowingCoordinator,
)
from agent_core.router import (  # noqa: E402
    OPENPHONE_ENDPOINT,
    SIMULATION_WARNING,
    OpenPhoneDispatcher,
    OrchestratorRouter,
    n8n_callback_payload,
    n8n_headers,
    openphone_headers,
    openphone_payload,
)
from agent_core.templates import (  # noqa: E402
    COMPLIANCE_EXIT,
    RESPONSE_TEMPLATES,
    TEMPLATE_MATRIX_TARGET,
    get_template,
)
from main import app  # noqa: E402
from main import integration_summary, load_engine_config, validate_engine_config  # noqa: E402


def ctx(**kw) -> LeadContext:
    kw.setdefault("rent", 800.0)
    return LeadContext(**kw)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_healthz(base_url: str, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/healthz", timeout=0.5) as resp:
                if resp.status == 200:
                    return
        except Exception as exc:  # pragma: no cover - only reported on timeout
            last_error = exc
        time.sleep(0.1)
    raise RuntimeError(f"service did not become healthy: {last_error}")


class TemplateTests(unittest.TestCase):
    def test_all_gate_keys_present(self):
        for gate in ("gate_1_occupancy", "gate_2_location", "gate_3_income", "showing"):
            self.assertIn(gate, RESPONSE_TEMPLATES)
            self.assertTrue(RESPONSE_TEMPLATES[gate])

    def test_compliance_exit_nonempty_and_neutral(self):
        self.assertTrue(COMPLIANCE_EXIT)
        for word in ("race", "religion", "national", "disability", "sex", "familial"):
            self.assertNotIn(word, COMPLIANCE_EXIT.lower())

    def test_matrix_target_documented(self):
        self.assertEqual(TEMPLATE_MATRIX_TARGET, 333)

    def test_get_template_strict(self):
        self.assertTrue(get_template("showing", "slot_offer"))
        with self.assertRaises(KeyError):
            get_template("showing", "nope")


class AgentTests(unittest.TestCase):
    def test_occupancy_fails_on_pets_kids_multiple(self):
        agent = OccupancyEvaluator()
        for bad in (ctx(has_pets=True), ctx(has_kids=True), ctx(occupants=2)):
            result = agent.evaluate(bad)
            self.assertFalse(result.passed)
            self.assertEqual(result.reply, COMPLIANCE_EXIT)
            self.assertTrue(result.deterministic)

    def test_occupancy_passes_single_adult(self):
        result = OccupancyEvaluator().evaluate(ctx())
        self.assertTrue(result.passed)
        self.assertEqual(result.gate, "gate_1_occupancy")

    def test_income_fails_below_multiplier(self):
        result = IncomeVerifier().evaluate(ctx(monthly_income=INCOME_MULTIPLIER * 800 - 1))
        self.assertFalse(result.passed)
        self.assertEqual(result.reply, COMPLIANCE_EXIT)

    def test_income_passes_at_floor(self):
        result = IncomeVerifier().evaluate(ctx(monthly_income=INCOME_MULTIPLIER * 800))
        self.assertTrue(result.passed)

    def test_income_unknown_asks_not_fails(self):
        result = IncomeVerifier().evaluate(ctx(monthly_income=None))
        self.assertTrue(result.passed)
        self.assertIn("2.5x", result.reply)

    def test_showing_probes_then_offers_slot(self):
        agent = ShowingCoordinator()
        probe = agent.evaluate(ctx())
        self.assertTrue(probe.passed)
        self.assertIn("close to your work", probe.reply)
        offer = agent.evaluate(ctx(commute_minutes=10, move_in_days=7))
        self.assertEqual(offer.gate, "showing")
        self.assertIn("5 and 7 pm", offer.reply)


class RouterTests(unittest.TestCase):
    def test_failed_gate_makes_zero_http_calls(self):
        client = mock.AsyncMock(spec=httpx.AsyncClient)
        router = OrchestratorRouter(api_key="k", client=client)
        result = asyncio.run(router.route(ctx(has_pets=True), "me and my dog"))
        self.assertFalse(result.passed)
        self.assertEqual(result.reply, COMPLIANCE_EXIT)
        client.post.assert_not_called()

    def test_no_api_key_template_only(self):
        router = OrchestratorRouter(api_key=None, client=None)
        result = asyncio.run(router.route(ctx(), "is it available?"))
        self.assertTrue(result.passed)
        self.assertTrue(result.deterministic)

    def test_unknown_stage_falls_back_to_gate_1(self):
        router = OrchestratorRouter()
        result = asyncio.run(router.route(ctx(stage=9)))
        self.assertEqual(result.gate, "gate_1_occupancy")

    def test_personalize_falls_back_to_template_on_http_error(self):
        client = mock.AsyncMock(spec=httpx.AsyncClient)
        client.post.side_effect = httpx.ConnectError("down")
        router = OrchestratorRouter(api_key="k", client=client)
        result = asyncio.run(router.route(ctx(), "hello"))
        self.assertTrue(result.passed)
        self.assertTrue(result.deterministic)
        self.assertEqual(result.reply, get_template("gate_1_occupancy", "availability"))

    def test_personalize_used_on_success(self):
        resp = mock.Mock()
        resp.raise_for_status = mock.Mock()
        resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Warm reply!"}]}}]
        }
        client = mock.AsyncMock(spec=httpx.AsyncClient)
        client.post.return_value = resp
        router = OrchestratorRouter(api_key="k", client=client)
        result = asyncio.run(router.route(ctx(), "hello"))
        self.assertEqual(result.reply, "Warm reply!")
        self.assertFalse(result.deterministic)


class OutboundNetworkTests(unittest.TestCase):
    def test_openphone_payload_contract(self):
        payload = openphone_payload("+155****4567", "Room available", "+155****0000")
        self.assertEqual(
            payload,
            {"content": "Room available", "from": "+155****0000", "to": ["+155****4567"]},
        )

    def test_openphone_payload_rejects_media_for_text_endpoint(self):
        with self.assertRaises(ValueError):
            openphone_payload(
                "+155****4567", "Photos", "+155****0000", media=["https://x/a.jpg"]
            )

    def test_openphone_headers(self):
        headers = openphone_headers("op-key")
        self.assertEqual(headers["Authorization"], "op-key")
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_n8n_payload_and_headers(self):
        result = OccupancyEvaluator().evaluate(ctx())
        payload = n8n_callback_payload("lead-1", result, 12.34)
        self.assertEqual(payload["lead_id"], "lead-1")
        self.assertEqual(payload["gate"], "gate_1_occupancy")
        self.assertTrue(payload["passed"])
        self.assertEqual(payload["latency_ms"], 12.3)
        self.assertEqual(n8n_headers("nk")["X-N8N-API-KEY"], "nk")
        self.assertNotIn("X-N8N-API-KEY", n8n_headers(None))

    def test_dispatcher_simulation_mode_on_missing_key(self):
        for key in (None, "", "   "):
            client = mock.AsyncMock(spec=httpx.AsyncClient)
            dispatcher = OpenPhoneDispatcher(
                api_key=key, from_number="+155****0000", live=True, client=client
            )
            with mock.patch("builtins.print") as print_mock:
                result = asyncio.run(dispatcher.send("+155****4567", "hi"))
            self.assertFalse(result["sent"])
            self.assertTrue(result["simulated"])
            self.assertEqual(result["payload"]["to"], ["+155****4567"])
            print_mock.assert_any_call(SIMULATION_WARNING)
            client.post.assert_not_called()

    def test_dispatcher_simulates_when_mode_is_not_live_even_with_key(self):
        client = mock.AsyncMock(spec=httpx.AsyncClient)
        dispatcher = OpenPhoneDispatcher(
            api_key="op-key", from_number="+155****0000", live=False, client=client
        )

        with mock.patch("builtins.print"):
            result = asyncio.run(dispatcher.send("+155****4567", "hi"))

        self.assertFalse(result["sent"])
        self.assertTrue(result["simulated"])
        client.post.assert_not_called()

    def test_dispatcher_sends_with_key_and_live_mode(self):
        resp = mock.Mock(status_code=202)
        resp.raise_for_status = mock.Mock()
        client = mock.AsyncMock(spec=httpx.AsyncClient)
        client.post.return_value = resp
        dispatcher = OpenPhoneDispatcher(
            api_key="op-key", from_number="+155****0000", live=True, client=client
        )
        result = asyncio.run(dispatcher.send("+155****4567", "hi"))
        self.assertTrue(result["sent"])
        self.assertFalse(result["simulated"])
        args, kwargs = client.post.call_args
        self.assertEqual(args[0], OPENPHONE_ENDPOINT)
        self.assertEqual(kwargs["headers"]["Authorization"], "op-key")
        self.assertEqual(
            kwargs["json"],
            {"content": "hi", "from": "+155****0000", "to": ["+155****4567"]},
        )

    def test_dispatcher_http_error_no_raise(self):
        client = mock.AsyncMock(spec=httpx.AsyncClient)
        client.post.side_effect = httpx.ConnectError("down")
        dispatcher = OpenPhoneDispatcher(
            api_key="op-key", from_number="+155****0000", live=True, client=client
        )
        result = asyncio.run(dispatcher.send("+155****4567", "hi"))
        self.assertFalse(result["sent"])
        self.assertFalse(result["simulated"])
        self.assertIn("error", result)


class EngineConfigTests(unittest.TestCase):
    def test_engine_mode_defaults_to_simulation_even_with_openphone_key(self):
        with mock.patch.dict(os.environ, {"OPENPHONE_API_KEY": "op-key"}, clear=True):
            config = load_engine_config()

        self.assertEqual(config.mode, "simulation")
        self.assertFalse(config.live)

    def test_live_mode_requires_key_from_number_and_webhook_token(self):
        with mock.patch.dict(os.environ, {"ENGINE_MODE": "live"}, clear=True):
            config = load_engine_config()

        with self.assertRaisesRegex(RuntimeError, "OPENPHONE_API_KEY"):
            validate_engine_config(config)

    def test_live_mode_accepts_required_openphone_and_token_config(self):
        with mock.patch.dict(
            os.environ,
            {
                "ENGINE_MODE": "live",
                "OPENPHONE_API_KEY": "op-key",
                "OPENPHONE_FROM_NUMBER": "+155****0000",
                "ENGINE_WEBHOOK_TOKEN": "secret",
            },
            clear=True,
        ):
            config = load_engine_config()

        validate_engine_config(config)
        self.assertEqual(integration_summary(config)["openphone"], "live")

    def test_integration_summary_reports_disabled_services(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            summary = integration_summary(load_engine_config())

        self.assertEqual(summary["mode"], "simulation")
        self.assertEqual(summary["openphone"], "simulated")
        self.assertEqual(summary["gemini"], "disabled")
        self.assertEqual(summary["n8n"], "disabled")

    def test_startup_logs_integration_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ,
                {
                    "ENGINE_WEBHOOK_TOKEN": "secret",
                    "ENGINE_AUDIT_LOG": str(Path(tmp) / "audit.jsonl"),
                },
                clear=True,
            ):
                with self.assertLogs("agent_core", level="INFO") as logs:
                    with TestClient(app):
                        pass

        self.assertTrue(any("engine integration summary" in line for line in logs.output))


class WebhookTests(unittest.TestCase):
    def test_disqualified_lead_round_trip(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with TestClient(app) as client:
                resp = client.post(
                    "/webhook/fb-inbound",
                    json={
                        "lead_id": "t1",
                        "messages": [{"text": "Me and my dog need a room"}],
                        "metadata": {"rent": 800, "stage": 1, "has_pets": True},
                    },
                )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["reply"], COMPLIANCE_EXIT)
        self.assertFalse(body["passed"])
        self.assertTrue(body["deterministic"])
        self.assertTrue(body["sla_met"])
        self.assertIn("latency_ms", body)
        self.assertIn("X-Latency-MS", resp.headers)

    def test_webhook_dispatch_simulated_without_openphone_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with TestClient(app) as client:
                with mock.patch("builtins.print"):
                    resp = client.post(
                        "/webhook/fb-inbound",
                        json={
                            "lead_id": "t2",
                            "messages": [{"text": "Just me, no pets"}],
                            "metadata": {"rent": 800, "stage": 1, "phone": "+155****4567"},
                        },
                    )
        body = resp.json()
        self.assertTrue(body["passed"])
        self.assertTrue(body["dispatch"]["simulated"])
        self.assertFalse(body["dispatch"]["sent"])
        self.assertEqual(len(body["dispatch"]["payload"]["to"]), 1)
        self.assertTrue(body["dispatch"]["payload"]["to"][0].startswith("+155"))
        self.assertEqual(body["dispatch"]["payload"]["content"], body["reply"])

    def test_webhook_no_phone_no_dispatch(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with TestClient(app) as client:
                resp = client.post(
                    "/webhook/fb-inbound",
                    json={
                        "lead_id": "t3",
                        "messages": [{"text": "hi"}],
                        "metadata": {"rent": 800, "stage": 1},
                    },
                )
        self.assertIsNone(resp.json()["dispatch"])

    def test_webhook_requires_token_when_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = str(Path(tmp) / "audit.jsonl")
            with mock.patch.dict(
                os.environ,
                {"ENGINE_WEBHOOK_TOKEN": "secret", "ENGINE_AUDIT_LOG": audit_path},
                clear=True,
            ):
                with TestClient(app) as client:
                    missing = client.post(
                        "/webhook/fb-inbound",
                        json={
                            "lead_id": "auth-missing",
                            "messages": [{"text": "hi"}],
                            "metadata": {"rent": 800, "stage": 1},
                        },
                    )
                    ok = client.post(
                        "/webhook/fb-inbound",
                        headers={"X-Engine-Token": "secret"},
                        json={
                            "lead_id": "auth-ok",
                            "messages": [{"text": "hi"}],
                            "metadata": {"rent": 800, "stage": 1},
                        },
                    )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(ok.status_code, 200)

    def test_live_mode_without_webhook_token_fails_startup(self):
        with mock.patch.dict(
            os.environ,
            {
                "ENGINE_MODE": "live",
                "OPENPHONE_API_KEY": "op-key",
                "OPENPHONE_FROM_NUMBER": "+155****0000",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "ENGINE_WEBHOOK_TOKEN"):
                with TestClient(app):
                    pass

    def test_webhook_writes_dispatch_audit_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "engine_dispatch.jsonl"
            with mock.patch.dict(
                os.environ,
                {"ENGINE_WEBHOOK_TOKEN": "secret", "ENGINE_AUDIT_LOG": str(audit_path)},
                clear=True,
            ):
                with TestClient(app) as client:
                    with mock.patch("builtins.print"):
                        resp = client.post(
                            "/webhook/fb-inbound",
                            headers={"X-Engine-Token": "secret"},
                            json={
                                "lead_id": "audit-1",
                                "messages": [{"text": "Just me"}],
                                "metadata": {
                                    "rent": 800,
                                    "stage": 1,
                                    "phone": "+155****4567",
                                },
                            },
                        )

            records = [json.loads(line) for line in audit_path.read_text().splitlines()]

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["lead_id"], "audit-1")
        self.assertEqual(records[0]["mode"], "simulation")
        self.assertTrue(records[0]["simulated"])
        self.assertFalse(records[0]["sent"])

    def test_audit_write_failure_does_not_block_response(self):
        with mock.patch.dict(os.environ, {"ENGINE_WEBHOOK_TOKEN": "secret"}, clear=True):
            with TestClient(app) as client:
                with mock.patch("main.append_audit_record", side_effect=OSError("disk full")):
                    resp = client.post(
                        "/webhook/fb-inbound",
                        headers={"X-Engine-Token": "secret"},
                        json={
                            "lead_id": "audit-fail",
                            "messages": [{"text": "hi"}],
                            "metadata": {"rent": 800, "stage": 1},
                        },
                    )

        self.assertEqual(resp.status_code, 200)

    def test_healthz(self):
        with mock.patch.dict(os.environ, {"ENGINE_WEBHOOK_TOKEN": "secret"}, clear=True):
            with TestClient(app) as client:
                self.assertEqual(client.get("/healthz").json(), {"status": "ok"})


class SmokeScriptTests(unittest.TestCase):
    def test_engine_smoke_script_passes_against_simulated_service_and_rejects_bad_token(self):
        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env.update(
                {
                    "ENGINE_MODE": "simulation",
                    "ENGINE_WEBHOOK_TOKEN": "secret",
                    "ENGINE_AUDIT_LOG": str(Path(tmp) / "audit.jsonl"),
                    "PYTHONPATH": str(REPO / "src"),
                }
            )
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "src.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=REPO,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                _wait_for_healthz(base_url)
                ok = subprocess.run(
                    [
                        sys.executable,
                        "scripts/engine_smoke.py",
                        "--base-url",
                        base_url,
                        "--token",
                        "secret",
                    ],
                    cwd=REPO,
                    env=env,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=15,
                )
                bad = subprocess.run(
                    [
                        sys.executable,
                        "scripts/engine_smoke.py",
                        "--base-url",
                        base_url,
                        "--token",
                        "wrong",
                    ],
                    cwd=REPO,
                    env=env,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=15,
                )
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)

        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn("engine smoke passed", ok.stdout)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("webhook failed", bad.stderr)


if __name__ == "__main__":
    unittest.main()
