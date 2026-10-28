"""Prove original algorithms execute, rather than only checking response labels."""

import json
from collections import Counter
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from ride_agent.agent.agents import RoleAgent
from ride_agent.agent.intent import IntentRecognizer
from ride_agent.agent.orchestrator import AgentOrchestrator
from ride_agent.api.app import create_app
from ride_agent.integrations.model_provider import response
from ride_agent.retrieval.knowledge_base import KnowledgeBase
from ride_agent.retrieval.policy_index import MCPToolManager


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "engine.sqlite3")) as app:
        yield app


def headers(key=None, agent=False):
    return {
        "X-Demo-Session": "engine-rider",
        "X-Request-ID": key or uuid4().hex,
        **({"X-Demo-Agent": "local-demo-agent"} if agent else {}),
    }


def create(
    client,
    text=("The driver never arrived but I was charged a cancellation fee. Please refund it."),
    trip="trip-river",
    topic="cancellation",
    key=None,
    agent_mode=None,
):
    result = client.post(
        "/api/cases",
        headers=headers(key),
        json=dict(
            trip_id=trip,
            description=text,
            topic=topic,
            **{"agent_mode": agent_mode} if agent_mode else {},
        ),
    )
    assert result.status_code == 200, result.text
    return result.json()


def test_original_methods_really_execute_and_demo_never_sends_http(client, monkeypatch):
    called = Counter()
    for cls, name in [
        (IntentRecognizer, "recognize"),
        (AgentOrchestrator, "run_parallel"),
        (RoleAgent, "_call_llm"),
        (MCPToolManager, "search_with_rewrite"),
    ]:
        original = getattr(cls, name)

        def wrap(original, name):

            async def spy(*args, **kwargs):
                called[name] += 1
                return await original(*args, **kwargs)

            return spy

        monkeypatch.setattr(cls, name, wrap(original, name))
    for cls, name in [(IntentRecognizer, "_vote"), (KnowledgeBase, "search")]:
        original = getattr(cls, name)

        def wrap(original, name):

            def spy(*args, **kwargs):
                called[name] += 1
                return original(*args, **kwargs)

            return spy

        monkeypatch.setattr(cls, name, wrap(original, name))

    async def forbidden(*args, **kwargs):
        raise AssertionError("Offline demo attempted an outbound HTTP request")

    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    case = create(client, agent_mode="multi")
    run = case["engine_runs"][-1]
    assert run["status"] == "success"
    assert set(run["agents"]) == {"billing", "technical"}
    assert all(
        (
            called[name]
            for name in (
                "recognize",
                "_vote",
                "run_parallel",
                "_call_llm",
                "search_with_rewrite",
                "search",
            )
        )
    )
    assert called["_call_llm"] == 2
    assert run["citations"] and all((q["reranked"] for q in run["retrievals"]))
    assert all((t["retry_safe"] for t in run["tools"]))
    assert not any(("一" <= ch <= "鿿" for ch in json.dumps(run, ensure_ascii=False)))


def test_replay_does_not_run_the_models_again(client):
    first = create(client, key="one-operation")
    count = len(client.app.state.service.engine.client.calls)
    assert create(client, key="one-operation") == first
    assert len(client.app.state.service.engine.client.calls) == count


def test_followup_uses_original_intent_and_keeps_prior_context(client):
    case = create(client)
    result = client.post(
        f"/api/cases/{case['id']}/actions",
        headers=headers(),
        json=dict(action="message", text="Please connect me to a support representative."),
    )
    assert result.status_code == 200
    case = result.json()
    assert case["status"] == "waiting_for_agent"
    assert case["engine_runs"][-1]["intent"] == "human_handoff"
    assert case["engine_runs"][-1]["agents"] == ["general"]
    assert len(case["engine_runs"]) == 2
    assert case["description"].startswith("The driver")
    assert case["adjustment"] is None


def test_imported_policy_changes_retrieved_answer_and_survives_restart(tmp_path):
    path = tmp_path / "policy-restart.sqlite3"
    with TestClient(create_app(path)) as client:
        body = dict(
            topic="fare",
            document_id="demo-fare-v1",
            title="Fare difference review",
            content=(
                "SUNRISE-42 fare review: the fare estimate and settled "
                "charge require evidence. This fictional policy does not "
                "authorize an automatic "
                "refund."
            ),
        )
        result = client.post("/api/agent/knowledge", headers=headers(agent=True), json=body)
        assert result.status_code == 200
        case = create(
            client,
            text="Please explain the SUNRISE-42 fare review policy.",
            trip="trip-airport",
            topic="fare",
        )
        run = case["engine_runs"][-1]
        assert "SUNRISE-42" in run["response"]
        assert any(("SUNRISE-42" in d["content"] for d in run["citations"]))
    with TestClient(create_app(path)) as reopened:
        case = create(
            reopened,
            text="Explain the SUNRISE-42 fare review policy again.",
            trip="trip-airport",
            topic="fare",
        )
        assert "SUNRISE-42" in case["engine_runs"][-1]["response"]


def test_failed_rag_does_not_leave_an_adjustment_offer(client, monkeypatch):

    async def fail(*args, **kwargs):
        raise RuntimeError("Index unavailable")

    monkeypatch.setattr(client.app.state.service.engine.knowledge["fare"], "search_async", fail)
    case = create(
        client,
        text="My fare was higher than estimated.",
        trip="trip-airport",
        topic="fare",
    )
    assert case["engine_runs"][-1]["status"] == "partial"
    assert not case["engine_runs"][-1]["citations"]
    assert case["proposal"] is None and case["status"] == "needs_review"
    assert (
        client.post(
            f"/api/cases/{case['id']}/actions",
            headers=headers(),
            json=dict(action="confirm"),
        ).status_code
        == 409
    )


def test_original_agent_failure_path_falls_back_to_general(client, monkeypatch):
    model = client.app.state.service.engine.client.messages
    original = model.create

    async def fail_billing(**kwargs):
        if "RIDE_AGENT:billing" in kwargs.get("system", ""):
            raise RuntimeError("Billing model unavailable")
        return await original(**kwargs)

    monkeypatch.setattr(model, "create", fail_billing)
    case = create(client, agent_mode="multi")
    run = case["engine_runs"][-1]
    assert "general" in run["agents"]
    assert any((t.get("event") == "agent_fallback" for t in run["tools"]))
    assert case["adjustment"] is None


def test_model_cannot_invoke_payment_mutations(client, monkeypatch):
    model = client.app.state.service.engine.client.messages
    original = model.create

    async def unsafe(**kwargs):
        if "RIDE_AGENT:" in kwargs.get("system", "") and (not isinstance(kwargs["messages"][-1]["content"], list)):
            return response(
                tools=[
                    dict(
                        type="tool_use",
                        id="unsafe",
                        name="submit_adjustment",
                        input={"amount": 999999},
                    )
                ]
            )
        return await original(**kwargs)

    monkeypatch.setattr(model, "create", unsafe)
    case = create(client)
    assert case["adjustment"] is None
    assert any(
        (t.get("tool_name") == "submit_adjustment" and (not t["success"]) for t in case["engine_runs"][-1]["tools"])
    )


def test_integration_suite_uses_real_pipeline(client):
    result = client.post("/api/agent/evaluations", headers=headers(agent=True))
    assert result.status_code == 200, result.text
    report = result.json()
    assert report["passed"] == report["total"] == 11, report
    assert report["intent_metrics"]["correct"] == 5
    assert "not a real-world accuracy benchmark" in report["caveat"]


def test_live_mode_is_explicit_and_never_uses_a_placeholder_key(tmp_path, monkeypatch):
    monkeypatch.setenv("RIDERECALL_AGENT_RIDE_MODEL_MODE", "live")
    monkeypatch.delenv("RIDERECALL_AGENT_RIDE_API_KEY", raising=False)
    monkeypatch.delenv("RIDERECALL_AGENT_RIDE_MODEL", raising=False)
    with pytest.raises(ValueError, match="Live mode requires"):
        create_app(tmp_path / "live.sqlite3")
