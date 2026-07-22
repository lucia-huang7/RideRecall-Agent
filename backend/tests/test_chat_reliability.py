"""HTTP 链路回归：使用 Redis 协议模拟器验证锁、事务、记忆和幂等行为。"""
import asyncio
import json
import sys
import types
from collections import deque
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fakeredis.aioredis
import httpx
import pytest
import pytest_asyncio
from prometheus_client import CollectorRegistry

try:
    import chromadb
except ModuleNotFoundError:
    sys.modules["chromadb"] = types.ModuleType("chromadb")

import api.main as api
from agents.agent_orchestrator import (
    AgentOrchestrator, AgentType, BillingAgent, GeneralAgent, TechnicalAgent,
    ResponseComposer,
)
from agents.tools import AgentToolSpec
from evaluation.evaluator import EndToEndEvaluator, QualityScores
from core.concurrency import KeyedLocks
from core.intent_recognizer import IntentCategory, IntentRecognizer, IntentResult, UrgencyLevel
from memory.conversation_memory import MemoryManager
from monitor.performance_monitor import PerformanceMonitor


def text_response(text="回复完成", stop="end_turn"):
    return SimpleNamespace(content=[{"type": "text", "text": text}], stop_reason=stop)


@pytest_asyncio.fixture
async def services(monkeypatch):
    monkeypatch.setenv("KINDER_UBER_ENV", "development")
    monkeypatch.delenv("KINDER_UBER_API_TOKENS", raising=False)
    memory = MemoryManager.__new__(MemoryManager)
    memory._redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    memory._request_locks = KeyedLocks()
    memory._query_episodic = AsyncMock(return_value={})
    memory._get_profile = AsyncMock(return_value={})
    client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(return_value=text_response())))
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._pool = {
        kind: [cls(client, "test-model")]
        for kind, cls in [(AgentType.GENERAL, GeneralAgent), (AgentType.BILLING, BillingAgent),
                          (AgentType.TECHNICAL, TechnicalAgent)]
    }
    orch._composer = ResponseComposer(client, "test-model")
    orch._recent_tool_traces = deque(maxlen=200)
    recognizer = IntentRecognizer.__new__(IntentRecognizer)

    async def recognize(message, history=None):
        return IntentResult(IntentCategory.REFUND, .95, UrgencyLevel.LOW, "billing",
                            recognizer._extract_entities(message), "test", 0)

    orch.recognize_intent = AsyncMock(side_effect=recognize)
    monkeypatch.setattr(api, "_memory", memory)
    monkeypatch.setattr(api, "_orchestrator", orch)
    monkeypatch.setattr(api, "_profile_updater", None)
    monkeypatch.setattr(api, "_monitor", None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test") as http:
        yield memory, orch, client, http
    await memory._redis.aclose()


def body(message="退款规则", request_id="one", conv_id="c"):
    return {"message": message, "user_id": "u", "conv_id": conv_id, "client_request_id": request_id}


@pytest.mark.asyncio
async def test_failed_turn_can_retry_after_recovery_without_duplicate_history(services):
    memory, orch, model, http = services
    model.messages.create.side_effect = RuntimeError("provider down")
    first = await http.post("/chat", json=body())
    assert first.status_code == 503
    assert first.json()["status"] == "failed" and first.json()["retryable"]
    assert await memory._redis.llen(memory._wm_key("u", "c")) == 0
    assert not await memory._redis.exists(memory._state_key("u", "c"))
    assert (await http.post("/chat", json=body("另一条消息"))).status_code == 409
    model.messages.create.side_effect = None
    recovered = await http.post("/chat", json=body())
    repeat = await http.post("/chat", json=body())
    assert recovered.status_code == repeat.status_code == 200
    assert recovered.json() == repeat.json()
    assert recovered.json()["status"] == "success"
    assert model.messages.create.await_count == 3  # 两次失败尝试 + 一次成功
    assert await memory._redis.llen(memory._wm_key("u", "c")) == 2
    assert await memory.get_trace(recovered.json()["request_id"])


@pytest.mark.asyncio
@pytest.mark.parametrize("text,stop,status,http_status", [
    ("", "end_turn", "failed", 503),
    ("退款需要先", "max_tokens", "partial", 200),
])
async def test_empty_and_truncated_answers_are_not_recorded_as_complete(services, text, stop, status, http_status):
    memory, orch, model, http = services
    model.messages.create.return_value = text_response(text, stop)
    response = await http.post("/chat", json=body())
    assert response.status_code == http_status and response.json()["status"] == status
    assert orch._pool[AgentType.BILLING][0].stats.success == 0
    if status == "partial":
        repeat = await http.post("/chat", json=body())
        assert repeat.json() == response.json()
        assert model.messages.create.await_count == 1


@pytest.mark.asyncio
async def test_unsafe_tool_is_recorded_before_execution_and_not_replayed(services):
    memory, orch, model, http = services
    writes = []

    async def write(req, args):
        marker = await memory.get_turn_receipt("u", "c", "one")
        assert marker["response"]["error_code"] == "tool_outcome_unknown"
        assert not marker["response"]["retryable"]
        writes.append(args)
        return {"success": True}

    orch.set_shared_tools({"write": AgentToolSpec("write", "test", {"type": "object"}, write, retry_safe=False)})
    model.messages.create.side_effect = [
        SimpleNamespace(content=[{"type": "tool_use", "id": "w", "name": "write", "input": {}}]),
        RuntimeError("lost model response"), RuntimeError("fallback down"),
    ]
    first = await http.post("/chat", json=body())
    assert first.status_code == 503 and not first.json()["retryable"]
    model.messages.create.side_effect = None
    repeat = await http.post("/chat", json=body())
    assert repeat.json() == first.json()
    assert len(writes) == 1 and model.messages.create.await_count == 2


@pytest.mark.asyncio
async def test_repeated_write_within_one_model_tool_loop_executes_once(services):
    memory, orch, model, http = services
    write = AsyncMock(return_value={"success": True})
    orch.set_shared_tools({"write": AgentToolSpec("write", "test", {"type": "object"}, write, retry_safe=False)})
    model.messages.create.side_effect = [
        SimpleNamespace(content=[{"type": "tool_use", "id": "w1", "name": "write", "input": {}}]),
        SimpleNamespace(content=[{"type": "tool_use", "id": "w2", "name": "write", "input": {}}]),
        text_response("已处理"),
    ]
    response = await http.post("/chat", json=body())
    assert response.status_code == 200 and write.await_count == 1
    trace = await memory.get_trace(response.json()["request_id"])
    assert trace["tool_calls"][1]["success"] is False


@pytest.mark.asyncio
async def test_unsafe_tool_timeout_preserves_non_replayable_receipt(services, monkeypatch):
    memory, orch, model, http = services
    calls = 0

    async def generate(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(content=[{"type": "tool_use", "id": "w", "name": "write", "input": {}}])
        await asyncio.Event().wait()

    write = AsyncMock(return_value={"success": True})
    orch.set_shared_tools({"write": AgentToolSpec("write", "test", {"type": "object"}, write, retry_safe=False)})
    model.messages.create.side_effect = generate
    original_timeout = asyncio.timeout
    monkeypatch.setattr(api.asyncio, "timeout", lambda seconds: original_timeout(.2))
    first = await http.post("/chat", json=body())
    assert first.status_code == 504
    repeat = await http.post("/chat", json=body())
    assert repeat.status_code == 503 and not repeat.json()["retryable"]
    assert write.await_count == 1 and calls == 2
    assert await memory._redis.llen(memory._wm_key("u", "c")) == 0


@pytest.mark.asyncio
async def test_concurrent_retries_execute_once_and_bind_authenticated_identity(services, monkeypatch):
    memory, orch, model, http = services
    token = "test-token-with-at-least-24-characters"
    monkeypatch.setenv("KINDER_UBER_API_TOKENS", json.dumps({token: {"user_id": "real", "role": "user"}}))
    headers = {"Authorization": "Bearer " + token}
    first, second = await asyncio.gather(*(http.post("/chat", json=body(), headers=headers) for _ in range(2)))
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() and model.messages.create.await_count == 1
    assert await memory._redis.llen(memory._wm_key("real", "c")) == 2
    assert not await memory._redis.exists(memory._wm_key("u", "c"))
    assert (await http.get("/monitor", headers=headers)).status_code == 403
    assert (await http.post("/chat", json=body())).status_code == 401


@pytest.mark.asyncio
async def test_slots_survive_turns_but_reset_for_another_order_or_conversation(services):
    memory, orch, model, http = services
    await http.post("/chat", json=body("订单号 #12345，昨天支付99元", "first"))
    second = await http.post("/chat", json=body("退款多久到？", "second"))
    assert second.json()["entities"]["order_id"] == ["12345"]
    assert second.json()["entities"]["amount"] == ["99元"]
    assert second.json()["entities"]["date"] == ["昨天"]
    third = await http.post("/chat", json=body("另一笔订单 #67890", "third"))
    assert third.json()["entities"]["order_id"] == ["67890"]
    assert not third.json()["entities"]["amount"] and not third.json()["entities"]["date"]
    other = await http.post("/chat", json=body("退款多久到？", "fourth", "other"))
    assert not other.json()["entities"]["order_id"]
    state = json.loads(await memory._redis.get(memory._state_key("u", "c")))
    assert state["sources"]["order_id"] == third.json()["request_id"]


@pytest.mark.asyncio
async def test_dialog_evaluation_uses_same_slot_merge_as_chat(services):
    memory, orch, model, http = services
    orch._intent_recognizer = SimpleNamespace(recognize=orch.recognize_intent)
    evaluator = EndToEndEvaluator.__new__(EndToEndEvaluator)
    evaluator._orchestrator = orch
    evaluator._judge = SimpleNamespace(judge=AsyncMock(return_value=QualityScores(1, 1, 1, 1)))
    results = await evaluator._evaluate_dialog_case({
        "turns": ["订单号 #12345，昨天支付99元", "退款多久到？"],
        "expected_entities": {"order_id": ["12345"], "amount": ["99元"]},
    }, 0)
    assert results[-1].passed and results[-1].metadata["deterministic_checks"]["entities"]
    assert results[-1].metadata["entities"]["date"] == ["昨天"]


def raw_message(index):
    return json.dumps({"role": "user", "content": f"old-{index}",
                       "ts": datetime.now().isoformat(), "metadata": {}})


@pytest.mark.asyncio
async def test_background_compression_does_not_block_chat_or_drop_new_messages(services):
    memory, orch, model, http = services
    started, finish = asyncio.Event(), asyncio.Event()

    async def summarize(**kwargs):
        started.set()
        await finish.wait()
        return text_response("有效摘要")

    memory._client = SimpleNamespace(messages=SimpleNamespace(create=summarize))
    memory._model = "fake"
    memory._merge_summary = AsyncMock(return_value="有效摘要")
    memory._store_episodic = AsyncMock()
    memory.update_profile = AsyncMock()
    key = memory._wm_key("u", "c")
    await memory._redis.rpush(key, *(raw_message(i) for i in reversed(range(25))))
    maintain = asyncio.create_task(memory.maintain("u", "c"))
    try:
        await asyncio.wait_for(started.wait(), 1)
        response = await asyncio.wait_for(http.post("/chat", json=body()), 1)
        assert response.status_code == 200 and not maintain.done()
        finish.set()
        await maintain
        rows = [json.loads(row) for row in await memory._redis.lrange(key, 0, -1)]
        assert len(rows) == 7 and rows[0]["role"] == "assistant" and rows[1]["content"] == "退款规则"
        assert await memory._redis.get(memory._summary_key("u", "c")) == "有效摘要"
        memory._store_episodic.assert_awaited_once()
    finally:
        finish.set()
        await asyncio.gather(maintain, return_exceptions=True)


@pytest.mark.asyncio
async def test_concurrent_compression_commits_only_one_snapshot(services):
    memory, *_ = services
    count = 0
    started, finish = asyncio.Event(), asyncio.Event()

    async def summarize(**kwargs):
        nonlocal count
        count += 1
        if count == 2:
            started.set()
        await finish.wait()
        return text_response("有效摘要")

    memory._client = SimpleNamespace(messages=SimpleNamespace(create=summarize))
    memory._model = "fake"
    memory._merge_summary = AsyncMock(return_value="有效摘要")
    memory._store_episodic = AsyncMock()
    key = memory._wm_key("u", "c")
    await memory._redis.rpush(key, *(raw_message(i) for i in reversed(range(25))))
    tasks = [asyncio.create_task(memory._compress("u", "c")) for _ in range(2)]
    try:
        await asyncio.wait_for(started.wait(), 1)
        finish.set()
        await asyncio.gather(*tasks)
        assert await memory._redis.llen(key) == 5
        memory._store_episodic.assert_awaited_once()
    finally:
        finish.set()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_http_timing_includes_memory_commit_and_metrics_work_without_extra_port(services, monkeypatch):
    memory, orch, model, http = services
    registry = CollectorRegistry()
    monitor = PerformanceMonitor(orch, SimpleNamespace(), registry=registry)
    monkeypatch.setattr(api, "_monitor", monitor)
    original_commit = memory.commit_turn

    async def delayed_commit(*args, **kwargs):
        await asyncio.sleep(.025)
        return await original_commit(*args, **kwargs)

    monkeypatch.setattr(memory, "commit_turn", delayed_commit)
    response = await http.post("/chat", json=body())
    total = float(response.headers["Server-Timing"].split("=")[1])
    assert total >= response.json()["latency_ms"] + 20
    samples = [s for metric in registry.collect() for s in metric.samples]
    assert next(s.value for s in samples if s.name == "requests_total") == 1
    assert next(s.value for s in samples if s.name == "chat_latency_ms_count") == 1
    histogram = monitor._prom["agent_latency_ms"].labels(agent="bucket-check")
    histogram.observe(3000)
    assert next(s.value for s in histogram.collect()[0].samples
                if s.name.endswith("_bucket") and s.labels["le"] == "3000.0") == 1
