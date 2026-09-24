import asyncio
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agents.agent_orchestrator import (
    AgentOrchestrator, AgentResponse, AgentType, Request, ResponseComposer, RoutingDecision,
)
from core.intent_recognizer import IntentCategory, IntentRecognizer, UrgencyLevel
from core.conversation_state import merge_state
from evaluation.evaluator import DEFAULT_DIALOG_CASES, EndToEndEvaluator, QualityScores
from memory.conversation_memory import MemoryManager
from tooling.knowledge_base import KnowledgeBase


@pytest.mark.parametrize("message", ["不紧急，退款多久到账？", "无需立刻处理", "不是紧急问题",
                                     "not urgent", "this is not an emergency", "unknown issue"])
def test_negation_and_english_substrings_do_not_force_urgent_routing(message):
    rec = IntentRecognizer.__new__(IntentRecognizer)
    assert rec._urgency(message, IntentCategory.REFUND) == UrgencyLevel.LOW


def test_actual_urgent_clause_is_preserved_after_negated_clause():
    rec = IntentRecognizer.__new__(IntentRecognizer)
    assert rec._urgency("退款不紧急，但系统崩溃需要立刻处理", IntentCategory.TECHNICAL) == UrgencyLevel.CRITICAL


@pytest.mark.asyncio
async def test_few_shot_examples_reach_model():
    rec = IntentRecognizer.__new__(IntentRecognizer)
    rec.model = "test"
    create = AsyncMock(return_value=SimpleNamespace(content=[{"type": "text", "text": '{"intent":"refund","confidence":0.9}'}]))
    rec.client = SimpleNamespace(messages=SimpleNamespace(create=create))
    result = await rec._llm_recognize("退款", None)
    assert result["intent"] == IntentCategory.REFUND
    prompt = create.call_args.kwargs["messages"][0]["content"]
    assert '消息: "我要申请退款" → 意图: refund' in prompt


@pytest.mark.asyncio
async def test_intent_timeout_uses_fallback_instead_of_waiting_for_chat_deadline(monkeypatch):
    rec = IntentRecognizer.__new__(IntentRecognizer)
    rec.model = "test"
    monkeypatch.setenv("LLM_AGENT_SYSTEM_INTENT_TIMEOUT_S", "0.1")
    async def stall(**kwargs):
        await asyncio.Event().wait()
    rec.client = SimpleNamespace(messages=SimpleNamespace(create=stall))
    result = await asyncio.wait_for(rec._llm_recognize("退款", None), 1)
    assert result["failed"]


@pytest.mark.parametrize("cls,module", [(KnowledgeBase, "tooling.knowledge_base"), (MemoryManager, "memory.conversation_memory")])
def test_production_storage_connection_failure_never_switches_to_local(cls, module, monkeypatch):
    import importlib
    target = importlib.import_module(module)
    monkeypatch.setenv("LLM_AGENT_SYSTEM_ENV", "production")
    monkeypatch.setattr(target.chromadb, "Settings", Mock(return_value=None), raising=False)
    monkeypatch.setattr(target.chromadb, "HttpClient", Mock(side_effect=RuntimeError("remote unavailable")), raising=False)
    local = Mock()
    monkeypatch.setattr(target.chromadb, "PersistentClient", local, raising=False)
    if cls is MemoryManager:
        monkeypatch.setattr(target, "AsyncAnthropic", Mock())
        monkeypatch.setattr(target.redis, "from_url", Mock())
    with pytest.raises(RuntimeError, match="生产环境"):
        cls()
    local.assert_not_called()


def test_empty_production_knowledge_base_does_not_seed_demo_policies(monkeypatch):
    import tooling.knowledge_base as module
    monkeypatch.setenv("LLM_AGENT_SYSTEM_ENV", "production")
    collection = SimpleNamespace(count=lambda: 0)
    remote = SimpleNamespace(heartbeat=lambda: None, get_or_create_collection=lambda **kwargs: collection)
    monkeypatch.setattr(module.chromadb, "Settings", Mock(return_value=None), raising=False)
    monkeypatch.setattr(module.chromadb, "HttpClient", Mock(return_value=remote), raising=False)
    seed = Mock()
    monkeypatch.setattr(KnowledgeBase, "_load_default_docs", seed)
    assert KnowledgeBase()._use_server
    seed.assert_not_called()


def test_retrieval_retains_document_identity_and_version():
    kb = KnowledgeBase.__new__(KnowledgeBase)
    kb._collection = SimpleNamespace(count=lambda: 1, query=lambda **kwargs: {
        "documents": [["规则正文"]], "metadatas": [[{"title": "政策", "document_id": "doc-1", "version": "v1"}]],
        "distances": [[.2]],
    })
    result = kb.search("政策")[0]
    assert result["document_id"] == "doc-1" and result["version"] == "v1"


def test_switching_business_domain_clears_unrelated_fields():
    old = merge_state({}, {"order_id": ["12345"], "amount": ["99元"]}, "billing", "退款", "one")
    new = merge_state(old, {"error_code": ["401"]}, "technical", "登录失败", "two")
    assert not new["entities"].get("order_id") and new["entities"]["error_code"] == ["401"]


def test_neutral_order_request_keeps_order_for_next_billing_turn():
    state = merge_state({}, {"order_id": ["12345"]}, "request", "帮我取消订单 #12345", "one")
    next_state = merge_state(state, {"order_id": []}, "billing", "这笔订单怎么退款", "two")
    assert next_state["entities"]["order_id"] == ["12345"]


@pytest.mark.asyncio
async def test_route_regression_does_not_replace_baseline_even_after_repeated_runs(tmp_path):
    ev = EndToEndEvaluator.__new__(EndToEndEvaluator)
    ev._run_lock, ev._history, ev._baseline = asyncio.Lock(), [], None
    ev._baseline_path = tmp_path / "baseline.json"
    answer = SimpleNamespace(response="回复", agent_type=AgentType.BILLING,
                             intent=IntentCategory.REFUND, status="success", tools_used=[])
    ev._orchestrator = SimpleNamespace(run=AsyncMock(return_value=answer))
    ev._judge = SimpleNamespace(_model="judge", _client=SimpleNamespace(base_url="test"), _max_tokens=4096,
                               judge=AsyncMock(return_value=QualityScores(1, 1, 1, 1)))
    cases = [{"question": "退款", "expected_agent": "billing"}]
    assert (await ev.run(dialog_cases=cases)).pass_rate == 1
    baseline = ev._baseline_path.read_text()
    answer.agent_type = AgentType.TECHNICAL
    for _ in range(2):
        failed = await ev.run(dialog_cases=cases)
        assert failed.pass_rate == 0 and any("pass_rate" in item for item in failed.regressions)
        assert ev._baseline_path.read_text() == baseline
        assert not any("所有指标均达标" in item for item in failed.recommendations)


@pytest.mark.asyncio
@pytest.mark.parametrize("successful", [True, False])
async def test_parallel_partial_and_total_failure_are_distinguished(successful):
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._recent_tool_traces = deque(maxlen=10)
    orch._composer = ResponseComposer(None, "test")
    orch._execute = AsyncMock(side_effect=[
        AgentResponse(AgentType.TECHNICAL, "技术回复" if successful else "失败", successful),
        AgentResponse(AgentType.BILLING, "失败", False, status="failed"),
    ])
    req = Request("技术和账单问题", "u", "c", intent=IntentCategory.TECHNICAL)
    result = await orch.run_parallel(req, RoutingDecision(AgentType.TECHNICAL, [AgentType.BILLING]))
    assert result.status == ("partial" if successful else "failed")
    assert result.retryable is (not successful)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected_agent", [("failed", "billing"), ("partial", "billing"), ("success", "technical")])
async def test_judge_cannot_turn_execution_failure_or_wrong_route_into_a_pass(status, expected_agent):
    ev = EndToEndEvaluator.__new__(EndToEndEvaluator)
    ev._orchestrator = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(
        response="回复", agent_type=AgentType.BILLING, intent=IntentCategory.REFUND, status=status, tools_used=[])))
    judge = AsyncMock(return_value=QualityScores(1, 1, 1, 1))
    ev._judge = SimpleNamespace(judge=judge)
    results = await ev._evaluate_dialog_case({"question": "退款", "expected_agent": expected_agent}, 0)
    assert not results[0].passed
    assert results[0].metadata["agent_status"] == status
    if status == "failed":
        judge.assert_not_awaited()
        assert results[0].scores["overall"] == 0


def test_default_dialog_cases_supply_business_references():
    assert all(case.get("reference_answer") and case.get("evidence") for case in DEFAULT_DIALOG_CASES)


@pytest.mark.parametrize("value", [0, -1, 21, True])
def test_agent_rag_schema_rejects_unbounded_or_boolean_top_k(value):
    from agents.agent_orchestrator import GeneralAgent
    from agents.tools import build_shared_rag_tools
    spec = build_shared_rag_tools(None)["search_knowledge_base"]
    with pytest.raises(ValueError):
        GeneralAgent._validate_tool_input(spec, {"query": "政策", "top_k": value})


@pytest.mark.asyncio
async def test_invalid_search_budget_does_not_spend_a_model_request():
    from tooling.tool_manager import MCPToolManager
    manager = MCPToolManager.__new__(MCPToolManager)
    manager.rewrite_query = AsyncMock()
    result = await manager.search_with_rewrite("search", "政策", top_k=100000)
    assert not result.success
    manager.rewrite_query.assert_not_awaited()


@pytest.mark.asyncio
async def test_monitor_shutdown_releases_registry_and_http_server(monkeypatch):
    from prometheus_client import CollectorRegistry
    from monitor.performance_monitor import PerformanceMonitor
    import monitor.performance_monitor as module
    registry = CollectorRegistry()
    http_server = Mock()
    monkeypatch.setattr(module, "start_http_server", Mock(return_value=(http_server, None)))
    orch = SimpleNamespace(_pool={}, get_stats=lambda: {})
    tools = SimpleNamespace(get_stats=lambda: {})
    monitor = PerformanceMonitor(orch, tools, prometheus_port=9091, registry=registry)
    await monitor.start()
    await monitor.stop()
    assert not list(registry.collect())
    http_server.shutdown.assert_called_once()
    http_server.server_close.assert_called_once()
    await monitor.start()
    assert list(registry.collect())
    await monitor.stop()
