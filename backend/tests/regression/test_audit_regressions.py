import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import Tool as RemoteTool
from mcp.types import ToolAnnotations
from ride_agent.agent.access import identity
from ride_agent.agent.agents import TechnicalAgent
from ride_agent.agent.intent import IntentCategory, IntentRecognizer
from ride_agent.agent.orchestrator import AgentOrchestrator
from ride_agent.agent.types import AgentCallResult, AgentResponse, AgentStats, AgentType
from ride_agent.integrations.mcp_client import MCPServerConfig, StandardMCPClient
from ride_agent.retrieval.policy_index import (
    CircuitBreaker,
    CircuitState,
    MCPToolManager,
    Tool,
)

from tests.unit.test_agent_orchestrator import make_request
from tests.unit.test_knowledge_base import make_kb


def test_old_inflight_completion_cannot_release_probe():

    async def run():
        entered = {k: asyncio.Event() for k in ("old", "probe")}
        release = {k: asyncio.Event() for k in entered}
        agent = TechnicalAgent(None, "test")

        async def llm(req):
            entered[req.message].set()
            await release[req.message].wait()
            return AgentCallResult("ok")

        agent._call_llm = llm
        old = asyncio.create_task(agent.handle(make_request(message="old")))
        await entered["old"].wait()
        agent.stats.set_penalty(0.8)
        agent.stats.retry_at = time.monotonic() - 1
        probe = asyncio.create_task(agent.handle(make_request(message="probe")))
        await entered["probe"].wait()
        release["old"].set()
        await old
        assert not agent.stats.available() and agent.stats.probe_running
        release["probe"].set()
        await probe
        assert agent.stats.available() and (not agent.stats.probe_running)

    asyncio.run(run())


def test_old_tool_success_cannot_close_new_circuit():
    breaker = CircuitBreaker(failure_threshold=1, recovery_s=0)
    assert breaker.allow()
    old = breaker.generation
    breaker.record_failure(old)
    assert breaker.state == CircuitState.OPEN
    breaker.record_success(old)
    assert breaker.state == CircuitState.OPEN
    assert breaker.allow()
    probe = breaker.generation
    breaker.record_failure(old)
    assert breaker.state == CircuitState.HALF_OPEN
    breaker.record_success(probe)
    assert breaker.state == CircuitState.CLOSED


def test_fallback_retains_failed_agent_trace():

    async def run():
        orch = AgentOrchestrator.__new__(AgentOrchestrator)
        failed = AgentResponse(
            AgentType.TECHNICAL,
            "failed",
            False,
            tools_used=["search"],
            tool_traces=[{"tool_name": "search"}],
        )
        recovered = AgentResponse(AgentType.GENERAL, "ok", True)
        orch._pool = {
            AgentType.TECHNICAL: [SimpleNamespace(stats=AgentStats(), handle=AsyncMock(return_value=failed))],
            AgentType.GENERAL: [SimpleNamespace(stats=AgentStats(), handle=AsyncMock(return_value=recovered))],
        }
        result = await orch._execute(make_request(), AgentType.TECHNICAL)
        assert result.success and result.tools_used == ["search"]
        assert result.tool_traces[0]["tool_name"] == "search"
        assert result.tool_traces[1]["event"] == "agent_fallback"

    asyncio.run(run())


@pytest.mark.parametrize("value", ["0.9", None, 2, float("nan"), True])
def test_malformed_intent_confidence_falls_back(value):
    rec = IntentRecognizer.__new__(IntentRecognizer)
    rec._embedding_enabled, rec.threshold = (True, 0.5)
    local = {"intent": IntentCategory.TECHNICAL_LOGIN, "confidence": 0.8}
    intent, confidence, _ = rec._vote({"intent": IntentCategory.TECHNICAL_LOGIN, "confidence": value}, local, local)
    assert intent == IntentCategory.TECHNICAL_LOGIN and 0 <= confidence <= 1


def test_document_id_namespaces_do_not_collide():
    kb = make_kb()
    kb.add_documents(
        [
            {"title": "政策", "content": "甲"},
            {"document_id": "title:政策", "title": "另一文档", "content": "乙"},
        ]
    )
    assert len(kb._collection.rows) == 2


def test_failed_rag_is_not_successful_evidence_and_fork_is_isolated():

    async def run():
        manager = MCPToolManager("test")
        manager.rewrite_query = AsyncMock(return_value=["q"])

        async def fail(params, context):
            raise RuntimeError("storage down")

        manager.register(
            Tool(
                "search",
                "search",
                fail,
                {"type": "object"},
                fallback=lambda p, c, e: [{"fallback": True, "content": "unavailable"}],
            )
        )
        clone = manager.fork()
        result = await manager.search_with_rewrite("search", "q")
        assert not result.success and "storage down" in result.error
        assert clone._tools["search"].stats.total == 0
        assert clone._tools["search"].breaker.fail_count == 0
        await manager._client.close()

    asyncio.run(run())


def test_read_only_rejects_write_and_unspecified_tools():

    async def run():
        manager = MCPToolManager("test")
        config = MCPServerConfig(
            name="test",
            transport="stdio",
            command="unused",
            agents=["general"],
            allowed_tools=["write", "unknown", "read"],
        )
        client = StandardMCPClient(manager, [config])
        tools = [
            RemoteTool(
                name="write",
                inputSchema={"type": "object"},
                annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
            ),
            RemoteTool(name="unknown", inputSchema={"type": "object"}),
            RemoteTool(
                name="read",
                inputSchema={"type": "object"},
                annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
            ),
        ]
        client._register_catalog(client.connections["test"], tools)
        assert list(client.agent_tools()) == ["mcp_test_read"]
        await manager._client.close()

    asyncio.run(run())


def test_access_tokens_bind_identity_and_production_fails_closed(monkeypatch):
    monkeypatch.delenv("LLM_AGENT_SYSTEM_API_TOKENS", raising=False)
    monkeypatch.setenv("LLM_AGENT_SYSTEM_ENV", "production")
    with pytest.raises(ValueError):
        identity("")
    token = "test-token-with-at-least-24-characters"
    monkeypatch.setenv(
        "LLM_AGENT_SYSTEM_API_TOKENS",
        json.dumps({token: {"user_id": "u1", "role": "user"}}),
    )
    assert identity("Bearer " + token)["user_id"] == "u1"
    assert identity("Bearer wrong") == {}


def test_internal_intent_recognition_passes_entities():
    from ride_agent.agent.intent import UrgencyLevel

    async def run():
        orch = AgentOrchestrator.__new__(AgentOrchestrator)
        orch._intent_recognizer = SimpleNamespace(
            recognize=AsyncMock(
                return_value=SimpleNamespace(
                    intent=IntentCategory.TECHNICAL_LOGIN,
                    intent_group="technical",
                    urgency=UrgencyLevel.MEDIUM,
                    confidence=0.9,
                    entities={"error_code": ["401"]},
                )
            )
        )
        orch._route_decision = lambda req: SimpleNamespace(
            multi_agent=False,
            primary_agent=AgentType.TECHNICAL,
            reason="",
            confidence=0.9,
        )
        seen = []

        async def execute(req, kind):
            seen.append(req.entities)
            return AgentResponse(kind, "ok", True)

        orch._execute = execute
        orch._record_tool_trace = lambda r: None
        await orch.run(make_request(intent=None, entities={}))
        assert seen == [{"error_code": ["401"]}]

    asyncio.run(run())


def test_intent_evaluation_passes_history():
    from ride_agent.evaluation.evaluator import IntentEvaluator, IntentTestCase

    async def run():
        recognizer = SimpleNamespace(
            recognize=AsyncMock(
                return_value=SimpleNamespace(intent=IntentCategory.REFUND, confidence=0.9, reasoning="test")
            )
        )
        history = [{"role": "user", "content": "想退款"}]
        await IntentEvaluator(recognizer).evaluate([IntentTestCase("多久到账", "refund", {"history": history})])
        assert recognizer.recognize.call_args.kwargs["history"] == history

    asyncio.run(run())


def test_trace_redaction_keeps_diagnostic_fields():
    from ride_agent.agent.redaction import redact

    trace = {
        "input": {
            "api_key": "secret",
            "password": "secret",
            "query": "Bearer a-test-secret",
        },
        "latency_ms": 12,
    }
    clean = redact(trace)
    assert clean["latency_ms"] == 12
    assert clean["input"]["api_key"] == "[REDACTED]"
    assert clean["input"]["query"] == "Bearer [REDACTED]"
    assert trace["input"]["password"] == "secret"
