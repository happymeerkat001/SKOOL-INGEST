"""Tests for the AI lead qualification engine (src/agent_core + src/main)."""

import asyncio
import sys
import unittest
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


def ctx(**kw) -> LeadContext:
    kw.setdefault("rent", 800.0)
    return LeadContext(**kw)


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
        payload = openphone_payload("+15551234567", "Room available")
        self.assertEqual(payload, {"recipient": "+15551234567", "body": "Room available"})

    def test_openphone_payload_with_media(self):
        payload = openphone_payload("+15551234567", "Photos", media=["https://x/a.jpg"])
        self.assertEqual(payload["media"], ["https://x/a.jpg"])

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
            dispatcher = OpenPhoneDispatcher(api_key=key, client=client)
            with mock.patch("builtins.print") as print_mock:
                result = asyncio.run(dispatcher.send("+15551234567", "hi"))
            self.assertFalse(result["sent"])
            self.assertTrue(result["simulated"])
            self.assertEqual(result["payload"]["recipient"], "+15551234567")
            print_mock.assert_any_call(SIMULATION_WARNING)
            client.post.assert_not_called()

    def test_dispatcher_sends_with_key(self):
        resp = mock.Mock(status_code=202)
        resp.raise_for_status = mock.Mock()
        client = mock.AsyncMock(spec=httpx.AsyncClient)
        client.post.return_value = resp
        dispatcher = OpenPhoneDispatcher(api_key="op-key", client=client)
        result = asyncio.run(dispatcher.send("+15551234567", "hi", media=["https://x/a.jpg"]))
        self.assertTrue(result["sent"])
        self.assertFalse(result["simulated"])
        args, kwargs = client.post.call_args
        self.assertEqual(args[0], OPENPHONE_ENDPOINT)
        self.assertEqual(kwargs["headers"]["Authorization"], "op-key")
        self.assertEqual(
            kwargs["json"],
            {"recipient": "+15551234567", "body": "hi", "media": ["https://x/a.jpg"]},
        )

    def test_dispatcher_http_error_no_raise(self):
        client = mock.AsyncMock(spec=httpx.AsyncClient)
        client.post.side_effect = httpx.ConnectError("down")
        dispatcher = OpenPhoneDispatcher(api_key="op-key", client=client)
        result = asyncio.run(dispatcher.send("+15551234567", "hi"))
        self.assertFalse(result["sent"])
        self.assertFalse(result["simulated"])
        self.assertIn("error", result)


class WebhookTests(unittest.TestCase):
    def test_disqualified_lead_round_trip(self):
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
        with TestClient(app) as client:
            with mock.patch("builtins.print"):
                resp = client.post(
                    "/webhook/fb-inbound",
                    json={
                        "lead_id": "t2",
                        "messages": [{"text": "Just me, no pets"}],
                        "metadata": {"rent": 800, "stage": 1, "phone": "+15551234567"},
                    },
                )
        body = resp.json()
        self.assertTrue(body["passed"])
        self.assertTrue(body["dispatch"]["simulated"])
        self.assertFalse(body["dispatch"]["sent"])
        self.assertEqual(body["dispatch"]["payload"]["recipient"], "+15551234567")
        self.assertEqual(body["dispatch"]["payload"]["body"], body["reply"])

    def test_webhook_no_phone_no_dispatch(self):
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

    def test_healthz(self):
        with TestClient(app) as client:
            self.assertEqual(client.get("/healthz").json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
