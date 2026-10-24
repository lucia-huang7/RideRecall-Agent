"""Correlation and diagnostics must not expose payloads or grow labels per case."""

import json
import re
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from ride_agent.api.app import create_app
from ride_agent.api.telemetry import LOGGER, HttpObservation
from ride_agent.retrieval.policy_index import MCPToolManager, Tool


def headers():
    return {
        "X-Demo-Session": "observation-rider",
        "X-Request-ID": uuid4().hex,
        "X-Correlation-ID": "caller-controlled-value",
    }


def test_server_correlation_and_fixed_route_metrics_exclude_private_identifiers(
    tmp_path,
):
    with TestClient(create_app(tmp_path / "db.sqlite3")) as client:
        identifiers = []
        for i in range(12):
            result = client.get(
                "/api/cases/PRIVATE-CASE-" + str(i) + "?private=DO-NOT-LOG-THIS",
                headers=headers(),
            )
            assert result.status_code == 404
            correlation = result.headers["x-correlation-id"]
            assert re.fullmatch("[a-f0-9]{32}", correlation)
            identifiers.append(correlation)
        assert len(set(identifiers)) == 12
        metrics = client.get("/api/agent/metrics", headers={"X-Demo-Agent": "local-demo-agent"}).text
        assert 'route="/api/cases/{case_id}"' in metrics
        assert all(
            (
                value not in metrics
                for value in (
                    "PRIVATE-CASE",
                    "DO-NOT-LOG-THIS",
                    "caller-controlled-value",
                    *identifiers,
                )
            )
        )
        assert client.app.state.observation.active._value.get() == 0


def test_unexpected_exception_reports_frames_without_exception_text_or_mutating_case(tmp_path, monkeypatch, caplog):
    LOGGER.addHandler(caplog.handler)
    try:
        with TestClient(create_app(tmp_path / "db.sqlite3")) as client:
            case = client.post(
                "/api/cases",
                headers=headers(),
                json=dict(
                    trip_id="trip-river",
                    description="Please review the canceled pickup.",
                ),
            ).json()

            def fail(*args, **kwargs):
                raise RuntimeError("PRIVATE-EXCEPTION-DATA-719")

            monkeypatch.setattr(client.app.state.service, "act", fail)
            result = client.post(
                "/api/cases/" + case["id"] + "/actions",
                headers=headers(),
                json=dict(action="evidence", text="PRIVATE-REQUEST-DATA-513"),
            )
            assert result.status_code == 500 and result.headers["cache-control"] == "no-store"
            assert "PRIVATE-" not in result.text
            assert client.app.state.store.get(case["id"]) == case
            events = [json.loads(record.getMessage()) for record in caplog.records if record.name == LOGGER.name]
            event = next((event for event in events if event["correlation_id"] == result.headers["x-correlation-id"]))
            assert event["error_type"] == "RuntimeError" and event["frames"]
            assert all((set(frame) == {"module", "function", "line"} for frame in event["frames"]))
            assert "PRIVATE-" not in json.dumps(events)
            assert client.app.state.observation.active._value.get() == 0
    finally:
        LOGGER.removeHandler(caplog.handler)


@pytest.mark.asyncio
async def test_tool_exception_diagnostics_do_not_echo_arbitrary_payloads(caplog):
    manager = MCPToolManager(api_key="unused")

    async def fail(params, context):
        raise ValueError("PRIVATE-TOOL-DATA-316")

    manager.register(
        Tool(
            "test_privacy",
            "",
            fail,
            {"type": "object", "properties": {}, "additionalProperties": False},
            cache_ttl=0,
        )
    )
    try:
        result = await manager.call("test_privacy", {})
        assert not result.success
        assert "PRIVATE-TOOL-DATA-316" not in caplog.text
        assert "ValueError" in caplog.text
    finally:
        await manager._client.close()


def test_unknown_http_methods_share_one_metric_label():
    observation = HttpObservation()
    for i in range(20):
        observation.active.inc()
        observation.finish(uuid4().hex, "CUSTOM-" + str(i), "/fixed-route", 405, 0.01)
    samples = observation.render().decode()
    assert 'method="OTHER"' in samples and "CUSTOM-" not in samples
    assert observation.active._value.get() == 0
