"""
LlmAgentSystem 智能客服系统 — FastAPI 入口

启动时打印小熊饼干图案。
所有核心组件在 lifespan 中初始化，通过环境变量配置。
"""
import asyncio
import logging
import os
import pathlib
import sys
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Literal, Optional


_ROOT = str(pathlib.Path(__file__).parent.parent.resolve())
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response, UploadFile, File, Query, Request as HTTPRequest
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

load_dotenv()

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BANNER = r"""
    ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ
   ╔══════════════════════╗
   ║   LlmAgentSystem  v2.0     ║
   ║   智能客服 AI 系统    ║
   ╚══════════════════════╝
    ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ
"""

# ── 全局组件（lifespan 中初始化）─────────────────────────────────────────────
_orchestrator = None
_memory       = None
_tool_manager = None
_monitor      = None
_evaluator    = None
_eval_orchestrator = None
_eval_tool_manager = None
_skill_manager = None
_profile_updater = None
_mcp_client = None
from evaluation.jobs import EvaluationJobs
_eval_jobs = EvaluationJobs()

def _anthropic_cfg() -> Dict[str, Any]:
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("未设置 ANTHROPIC_API_KEY")
    cfg: Dict[str, Any] = {
        "api_key":  key,
        "model":    os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022").strip(),
    }
    base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip()
    if base_url:
        cfg["base_url"] = base_url
    return cfg


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _eval_orchestrator, _eval_tool_manager, _orchestrator, _memory, _tool_manager, _monitor, _evaluator, _skill_manager, _profile_updater, _mcp_client

    from core.access import identity
    identity("")  # 启动时验证生产鉴权配置
    print(BANNER, flush=True)

    from agents.agent_orchestrator import AgentOrchestrator, Request, build_shared_rag_tools
    from core.intent_recognizer import IntentRecognizer
    from evaluation.evaluator import EndToEndEvaluator
    from tooling.knowledge_base import KnowledgeBase
    from tooling.tool_manager import MCPToolManager, Tool
    from memory.conversation_memory import MemoryManager
    from monitor.performance_monitor import PerformanceMonitor
    from core.skill_loader import SkillManager

    cfg = _anthropic_cfg()
    logger.info(f"模型: {cfg['model']}  base_url: {cfg.get('base_url', '(官方)')}")

    # 意图识别器（Orchestrator 内部也会创建，这里单独暴露给 Evaluator）
    recognizer = IntentRecognizer(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )

    # Skills：启动时从目录加载业务能力说明，并在 Agent 调用 LLM 时动态注入。
    skills_dir = os.getenv("LLM_AGENT_SYSTEM_SKILLS_DIR", str(pathlib.Path(_ROOT) / "skills"))
    _skill_manager = SkillManager(
        root_dir=skills_dir,
        max_prompt_chars=int(os.getenv("LLM_AGENT_SYSTEM_SKILLS_MAX_PROMPT_CHARS", "5000")),
    )
    _skill_manager.load()

    # Agent 编排器
    _orchestrator = AgentOrchestrator(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        skill_manager=_skill_manager,
    )

    # 记忆管理器（Redis 工作记忆 + ChromaDB 情景记忆/用户画像）
    _memory = MemoryManager(
        redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
        chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )

    # MCP 工具管理器 + RAG 知识库（基于 ChromaDB 的真实检索）
    _tool_manager = MCPToolManager(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )
    kb = KnowledgeBase(
        chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),
    )
    logger.info(f"知识库已加载: {await kb.doc_count_async()} 个文档片段")

    def knowledge_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        query = params.get("query", "")
        return [{
            "title": "知识库降级结果",
            "content": f"知识库暂时不可用，未能完成对“{query}”的语义检索。请稍后重试，或转人工客服确认。",
            "score": 0.0,
            "fallback": True,
            "error": error,
        }]

    _tool_manager.register(Tool(
        name="knowledge_search",
        description="搜索知识库（基于 ChromaDB 向量检索）",
        handler=kb.search_handler,
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 2000},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["query"],
        },
        cache_ttl=300.0,
        supports_rerank=True,
        fallback=knowledge_fallback,
    ))
    from tooling.mcp_client import StandardMCPClient, load_servers
    from tooling.mcp_server import configure
    configure(kb, _tool_manager)
    mcp_config = pathlib.Path(os.getenv("MCP_SERVERS_CONFIG", str(pathlib.Path(_ROOT) / "config/mcp_servers.json")))
    enabled = os.getenv("MCP_EXTERNAL_ENABLED", "true").lower() in {"1", "true", "yes"}
    _mcp_client = StandardMCPClient(_tool_manager, load_servers(mcp_config) if enabled else [], _refresh_agent_tools)
    _refresh_agent_tools()
    await _mcp_client.start()

    # 性能监控（可选启动 Prometheus）
    prom_port = int(os.getenv("PROMETHEUS_PORT", "0")) or None
    _monitor = PerformanceMonitor(
        orchestrator=_orchestrator,
        tool_manager=_tool_manager,
        interval_s=float(os.getenv("MONITOR_INTERVAL", "10")),
        webhook_url=os.getenv("ALERT_WEBHOOK_URL") or None,
        prometheus_port=prom_port,
    )
    await _monitor.start()

    # 评测与线上使用独立路由状态、工具缓存和熔断器。
    _eval_tool_manager = _tool_manager.fork()
    _eval_orchestrator = AgentOrchestrator(api_key=cfg["api_key"], base_url=cfg.get("base_url"),
        model=cfg["model"], skill_manager=_skill_manager)
    _refresh_agent_tools()
    _evaluator = EndToEndEvaluator(
        orchestrator=_eval_orchestrator,
        recognizer=recognizer,
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        baseline_path=os.getenv("EVAL_BASELINE_PATH", "/app/data/eval/baseline.json"),
    )

    from core.concurrency import ProfileUpdater
    _profile_updater = ProfileUpdater(_memory, workers=int(os.getenv("PROFILE_UPDATE_WORKERS", "4")))
    _profile_updater.start()
    logger.info("LlmAgentSystem 已就绪")
    async with standard_mcp_server.session_manager.run():
        try:
            yield
        finally:
            await _eval_jobs.close()
            await _profile_updater.close()
            await _mcp_client.close()
            await _monitor.stop()
            if _memory is not None:
                await _memory.close()
            configure(None)
            logger.info("LlmAgentSystem 已关闭")


def _refresh_agent_tools():
    from agents.tools import build_shared_rag_tools
    if _orchestrator is not None and _tool_manager is not None:
        tools = build_shared_rag_tools(_tool_manager)
        if _mcp_client is not None:
            tools.update(_mcp_client.agent_tools())
        _orchestrator.set_shared_tools(tools)
    if _eval_orchestrator is not None:
        from dataclasses import replace
        for name in list(_eval_tool_manager._tools):
            if name not in _tool_manager._tools:
                _eval_tool_manager.unregister(name)
        for name, tool in _tool_manager._tools.items():
            if name not in _eval_tool_manager._tools or _eval_tool_manager._tools[name].handler is not tool.handler or _eval_tool_manager._tools[name].schema != tool.schema:
                _eval_tool_manager.register(replace(tool))
        eval_tools = build_shared_rag_tools(_eval_tool_manager)
        if _mcp_client is not None:
            eval_tools.update(_mcp_client.agent_tools(_eval_tool_manager))
        _eval_orchestrator.set_shared_tools({name: spec for name, spec in eval_tools.items() if spec.retry_safe})


from tooling.mcp_server import http_app as standard_mcp_app, server as standard_mcp_server


# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI(
    title="LlmAgentSystem 智能客服",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def access_control(request: HTTPRequest, call_next):
    from core.access import identity
    from fastapi.responses import JSONResponse
    try:
        account = identity(request.headers.get("authorization", ""))
    except (ValueError, TypeError):
        return JSONResponse({"detail": "服务鉴权配置无效"}, status_code=503)
    request.state.account = account
    if account is not None and request.url.path != "/health" and request.method != "OPTIONS":
        if not account:
            return JSONResponse({"detail": "需要有效 Bearer Token"}, status_code=401)
        reader = request.url.path in {"/chat", "/search", "/knowledge/stats", "/skills", "/mcp", "/mcp/"}
        if not reader and account["role"] != "admin":
            return JSONResponse({"detail": "该接口需要管理员权限"}, status_code=403)
    is_chat = request.url.path == "/chat" and request.method == "POST"
    started = time.monotonic()
    if is_chat and _monitor is not None and "requests_total" in _monitor._prom:
        _monitor._prom["requests_total"].inc()
    response = await call_next(request)
    if is_chat:
        elapsed = (time.monotonic() - started) * 1000
        response.headers["Server-Timing"] = f"chat;dur={elapsed:.1f}"
        if _monitor is not None and "chat_latency_ms" in _monitor._prom:
            _monitor._prom["chat_latency_ms"].labels(status=str(response.status_code)).observe(elapsed)
    return response


# ── 请求/响应模型 ─────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message:     str = Field(min_length=1, max_length=12000)
    client_request_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    user_id:     str = "anonymous"
    conv_id:     Optional[str] = None


class ChatResponse(BaseModel):
    conv_id:     str
    request_id:  str = ""
    response:    str
    intent:      str
    intent_group: str = "other"
    agent_type:  str
    agent_types: List[str] = Field(default_factory=list)
    primary_agent: str = ""
    supporting_agents: List[str] = Field(default_factory=list)
    tools_used: List[str] = Field(default_factory=list)
    routing_reason: str = ""
    routing_confidence: float = 0.0
    escalated:   bool
    latency_ms:  float
    knowledge_used: bool = False
    entities: Dict[str, List[str]] = Field(default_factory=dict)
    intent_confidence: float = 0.0
    intent_source_scores: Dict[str, float] = Field(default_factory=dict)
    intent_embedding_source: str = "local-ngram"
    status: Literal["success", "partial", "failed"] = "success"
    retryable: bool = False
    error_code: Optional[str] = None


class ToolTraceResponse(BaseModel):
    request_id: str
    found: bool
    trace: Dict[str, Any] = Field(default_factory=dict)


class RecentToolTracesResponse(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)


# ── 路由 ──────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    if _orchestrator is None:
        raise HTTPException(503, "服务未就绪")
    return {"status": "ok", "agents": {} if os.getenv("LLM_AGENT_SYSTEM_API_TOKENS") else _orchestrator.get_stats()}


@app.get("/skills", tags=["Skills"])
async def skills_summary():
    """查看当前已加载的 Skills，便于确认热加载结果和排查解析错误。"""
    if _skill_manager is None:
        raise HTTPException(503, "Skills 未初始化")
    return _skill_manager.summary()


@app.post("/skills/reload", tags=["Skills"])
async def reload_skills():
    """运行时重新扫描 Skill 目录，不需要重启服务。"""
    if _skill_manager is None:
        raise HTTPException(503, "Skills 未初始化")
    _skill_manager.reload()
    if _orchestrator is not None:
        _orchestrator.set_skill_manager(_skill_manager)
    return _skill_manager.summary()


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, http_request: HTTPRequest):
    """
    主对话接口。完整流程：
      记忆读取 → 意图识别 → Agent 路由 → 执行 → 记忆写入
    """
    if _orchestrator is None or _memory is None:
        raise HTTPException(503, "服务未就绪")

    account = getattr(http_request.state, "account", None)
    if account:
        req.user_id = account["user_id"]
    from agents.agent_orchestrator import Request as OrcReq

    client_id = req.client_request_id or str(uuid.uuid4())
    conv_id = req.conv_id or str(uuid.uuid5(uuid.NAMESPACE_URL, req.user_id + ":" + client_id))
    started = time.monotonic()

    def render(reply):
        if reply.status == "failed":
            from fastapi.responses import JSONResponse
            return JSONResponse(reply.model_dump(), status_code=503)
        return reply

    from core.concurrency import ConversationBusyError, LeaseLostError
    try:
        async with asyncio.timeout(50), _memory.conversation(req.user_id, conv_id):
            receipt = await _memory.get_turn_receipt(req.user_id, conv_id, client_id)
            if receipt:
                if receipt["message"] != req.message:
                    raise HTTPException(409, "同一 client_request_id 不能用于不同消息")
                previous = ChatResponse(**receipt["response"])
                if previous.status != "failed" or not previous.retryable:
                    return render(previous)
            # 1. 读取记忆上下文
            mem_ctx = await _memory.get_context(req.user_id, conv_id, query=req.message)

            # 2. 构建编排请求（含对话历史，用于意图识别上下文）
            history = [
                {"role": m.role.value, "content": m.content}
                for m in mem_ctx.recent_messages[-5:]
            ] if mem_ctx.recent_messages else None

            intent_result = await _orchestrator.recognize_intent(req.message, history=history)
            full_context = mem_ctx.to_prompt_text()

            orch_req = OrcReq(
                message=req.message,
                user_id=req.user_id,
                conv_id=conv_id,
                context=full_context,
                history=history,
                entities=intent_result.entities,
                intent=intent_result.intent,
                intent_group=intent_result.intent_group,
                urgency=intent_result.urgency,
                intent_confidence=intent_result.confidence,
                conversation_state=getattr(mem_ctx, "state", {}),
            )

            async def before_unsafe_tool(name):
                # 在产生副作用之前持久化；超时/崩溃后同一 ID 不会重放未知结果。
                marker = ChatResponse(conv_id=conv_id, request_id=orch_req.request_id,
                    response="该请求涉及可能修改数据的工具，请先核验执行结果再发起新的请求。",
                    intent=intent_result.intent.value, agent_type="unknown", escalated=False,
                    latency_ms=(time.monotonic() - started) * 1000,
                    status="failed", retryable=False, error_code="tool_outcome_unknown")
                await _memory.commit_turn(req.user_id, conv_id, client_id, req.message, marker.model_dump())
            orch_req.before_unsafe_tool = before_unsafe_tool

            # 3. 执行
            result = await _orchestrator.run(orch_req)

            response = ChatResponse(
                conv_id=conv_id,
                request_id=result.request_id,
                response=result.response,
                intent=result.intent.value if result.intent else "other",
                intent_group=intent_result.intent_group,
                agent_type=result.agent_type.value,
                agent_types=[agent_type.value for agent_type in result.agent_types],
                primary_agent=result.primary_agent.value if result.primary_agent else result.agent_type.value,
                supporting_agents=[agent_type.value for agent_type in result.supporting_agents],
                tools_used=result.tools_used,
                routing_reason=result.routing_reason,
                routing_confidence=result.routing_confidence,
                escalated=result.escalated,
                latency_ms=round((time.monotonic() - started) * 1000, 1),
                knowledge_used=any(t.get("tool_name") == "search_knowledge_base" and t.get("result_success") is True for t in result.tool_traces),
                entities=orch_req.entities,
                intent_confidence=round(intent_result.confidence, 4),
                intent_source_scores=intent_result.source_scores,
                intent_embedding_source=getattr(intent_result, "embedding_source", "local-ngram"),
                status=result.status,
                retryable=result.retryable,
                error_code=result.error_code,
            )
            await _memory.commit_turn(req.user_id, conv_id, client_id, req.message, response.model_dump(),
                                      _orchestrator.get_tool_trace(result.request_id), state=orch_req.conversation_state)
            if response.status != "failed" and _profile_updater is not None:
                _profile_updater.submit(req.user_id, conv_id)
            return render(response)
    except TimeoutError as ex:
        raise HTTPException(504, "本轮处理超过 50 秒，请使用相同 client_request_id 重试") from ex
    except (ConversationBusyError, LeaseLostError) as ex:
        raise HTTPException(503, str(ex)) from ex


@app.get("/monitor")
async def monitor_summary():
    """实时监控摘要：Agent 成功率、工具统计、告警、优化建议。"""
    if _monitor is None:
        raise HTTPException(503, "服务未就绪")
    return _monitor.summary()


@app.get("/trace/tool/{request_id}", response_model=ToolTraceResponse)
async def get_tool_trace(request_id: str):
    """查看某次请求的工具调用明细。"""
    if _orchestrator is None:
        raise HTTPException(503, "服务未就绪")
    trace = await _memory.get_trace(request_id) if _memory else None
    trace = trace or _orchestrator.get_tool_trace(request_id)
    return ToolTraceResponse(
        request_id=request_id,
        found=trace is not None,
        trace=trace or {},
    )


@app.get("/trace/tools", response_model=RecentToolTracesResponse)
async def list_recent_tool_traces(limit: int = 20):
    """查看最近 N 次请求的工具调用明细。"""
    if _orchestrator is None:
        raise HTTPException(503, "服务未就绪")
    return RecentToolTracesResponse(items=await _memory.recent_traces(limit) if _memory else _orchestrator.get_recent_tool_traces(limit=limit))


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus 指标入口。"""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/search")
async def search(query: str = Query(min_length=1, max_length=2000), top_k: int = Query(default=5, ge=1, le=20)):
    """
    演示检索优化链路：查询改写 → 并行召回 → 重排 → Top-K。
    展示 MCP 工具调用的核心亮点。
    """
    if _tool_manager is None:
        raise HTTPException(503, "服务未就绪")
    result = await _tool_manager.search_with_rewrite("knowledge_search", query, top_k=top_k)
    return {"query": query, "results": result.data, "reranked": result.reranked}


class DocInput(BaseModel):
    """单篇文档输入。"""
    title:   str
    content: str
    document_id: Optional[str] = None


class BatchDocInput(BaseModel):
    """批量文档导入请求体。"""
    documents: List[DocInput]


class EvalIntentInput(BaseModel):
    """意图识别评测用例。"""
    message: str
    expected_intent: str
    context: Optional[Dict[str, Any]] = None


class EvalDialogInput(BaseModel):
    """对话质量评测用例。question 单轮，turns 多轮。"""
    question: Optional[str] = None
    reference_answer: Optional[str] = Field(default=None, max_length=12000)
    evidence: Optional[str] = Field(default=None, max_length=20000)
    turns: Optional[List[str]] = Field(default=None, max_length=50)
    user_id: Optional[str] = None
    conv_id: Optional[str] = None
    expected_agent: Optional[Literal["general", "technical", "billing", "escalation"]] = None
    expected_intent: Optional[str] = None
    required_tools: List[str] = Field(default_factory=list, max_length=20)
    expected_entities: Dict[str, List[str]] = Field(default_factory=dict)


class EvalRunInput(BaseModel):
    """评测请求。为空时使用内置默认用例。"""
    intent_cases: Optional[List[EvalIntentInput]] = Field(default=None, max_length=200)
    dialog_cases: Optional[List[EvalDialogInput]] = Field(default=None, max_length=100)


@app.post("/knowledge/add", tags=["知识库"])
async def add_knowledge(body: BatchDocInput):
    """
    批量导入文档到知识库。

    文档会自动切片（每片 500 字）并存入 ChromaDB，ChromaDB 内置 Embedding 模型自动向量化。

    示例请求体：
    ```json
    {
      "documents": [
        {"title": "退款政策", "content": "用户在购买后 7 天内可以申请无理由退款..."},
        {"title": "配送说明", "content": "标准配送 3-5 个工作日..."}
      ]
    }
    ```
    """
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__
    try:
        count = await kb.add_documents_async([d.model_dump(exclude_none=True) for d in body.documents])
    except ValueError as ex:
        raise HTTPException(400, str(ex)) from ex
    finally:
        _tool_manager.invalidate_cache("knowledge_search")
        if _eval_tool_manager is not None:
            _eval_tool_manager.invalidate_cache("knowledge_search")
    total = await kb.doc_count_async()
    return {"message": f"成功导入 {count} 个文档片段", "added_chunks": count, "total_chunks": total}


@app.post("/knowledge/upload", tags=["知识库"])
async def upload_knowledge(file: UploadFile = File(...)):
    """
    上传文件导入知识库。

    支持格式：
    - `.txt` / `.md`：整个文件作为一篇文档，文件名作为标题
    - `.json`：JSON 数组格式 `[{"title": "...", "content": "..."}, ...]`

    文件大小限制：10MB
    """
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__

    filename = file.filename or "unknown"
    if pathlib.Path(filename).suffix.lower() not in {".txt", ".md", ".json"}:
        raise HTTPException(400, "仅支持 .txt、.md、.json 文件")
    content = await file.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "文件大小超过 10MB 限制")

    text = content.decode("utf-8", errors="ignore")
    filename = file.filename or "unknown"

    if filename.lower().endswith(".json"):
        import json as _json
        try:
            docs = _json.loads(text)
            if not isinstance(docs, list):
                raise HTTPException(400, "JSON 文件应为数组格式: [{title, content}, ...]")
        except _json.JSONDecodeError as e:
            raise HTTPException(400, f"JSON 解析失败: {e}")
    else:
        # txt / md：整个文件作为一篇文档
        title = filename.rsplit(".", 1)[0] if "." in filename else filename
        docs = [{"title": title, "content": text}]

    try:
        validated = [DocInput.model_validate(doc).model_dump(exclude_none=True) for doc in docs]
        count = await kb.add_documents_async(validated)
    except ValueError as ex:
        raise HTTPException(400, "文档格式不正确：需要非空 title、content 和可选 document_id") from ex
    finally:
        _tool_manager.invalidate_cache("knowledge_search")
        if _eval_tool_manager is not None:
            _eval_tool_manager.invalidate_cache("knowledge_search")
    total = await kb.doc_count_async()
    return {
        "message": f"文件 {filename} 导入成功",
        "added_chunks": count,
        "total_chunks": total,
    }


@app.get("/knowledge/stats", tags=["知识库"])
async def knowledge_stats():
    """查看知识库统计信息（文档片段总数）。"""
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__
    return {"total_chunks": await kb.doc_count_async()}


@app.post("/eval/run", status_code=202)
async def run_eval(body: Optional[EvalRunInput] = None):
    if _evaluator is None:
        raise HTTPException(503, "服务未就绪")
    snapshot = body.model_copy(deep=True) if body else None
    try:
        return _eval_jobs.submit(lambda progress: _execute_eval(snapshot, progress))
    except RuntimeError as ex:
        raise HTTPException(429, str(ex)) from ex


@app.get("/eval/jobs/{job_id}")
async def get_eval_job(job_id: str):
    job = _eval_jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "评测任务不存在或已过期；服务重启后需要重新提交")
    return job


async def _execute_eval(body: Optional[EvalRunInput] = None, progress=None):
    """运行内置评测用例，返回评测报告。"""
    if _evaluator is None:
        raise HTTPException(503, "服务未就绪")
    from evaluation.evaluator import DEFAULT_DIALOG_CASES, DEFAULT_INTENT_CASES, IntentTestCase

    if body and body.intent_cases is not None:
        intent_cases = [
            IntentTestCase(
                message=c.message,
                expected_intent=c.expected_intent,
                context=c.context,
            )
            for c in body.intent_cases
        ]
    else:
        intent_cases = DEFAULT_INTENT_CASES

    if body and body.dialog_cases is not None:
        dialog_cases = [
            c.model_dump(exclude_none=True)
            for c in body.dialog_cases
        ]
    else:
        dialog_cases = DEFAULT_DIALOG_CASES

    report = await _evaluator.run(
        intent_cases=intent_cases,
        dialog_cases=dialog_cases,
        progress=progress,
    )
    return {
        "pass_rate":       report.pass_rate,
        "scope":           "orchestrator",
        "status":          report.status,
        "suite_id":        report.suite_id,
        "regression_note": report.regression_note,
        "valid_total":     report.valid_total,
        "evaluation_errors": report.evaluation_errors,
        "total":           report.total,
        "passed":          report.passed,
        "avg_scores":      report.avg_scores,
        "regressions":     report.regressions,
        "recommendations": report.recommendations,
        "results": [
            {
                "test_id": r.test_id,
                "passed": r.passed,
                "scores": r.scores,
                "detail": r.detail,
                "metadata": r.metadata,
            }
            for r in report.results
        ],
    }


class MCPCallInput(BaseModel):
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


@app.get("/integrations/embedding", tags=["Embedding"])
async def embedding_status():
    if _orchestrator is None:
        raise HTTPException(503, "服务未就绪")
    return _orchestrator._intent_recognizer._embedding.status()


@app.get("/integrations/mcp", tags=["MCP"])
async def mcp_status():
    """查看外部标准 MCP 连接状态和已发现的白名单工具。"""
    if _mcp_client is None:
        raise HTTPException(503, "MCP 尚未初始化")
    return _mcp_client.summary()


@app.post("/integrations/mcp/call", tags=["MCP"])
async def mcp_call(body: MCPCallInput):
    """调试已配置的外部 MCP 工具，无需先调用 LLM。"""
    if _mcp_client is None or _tool_manager is None:
        raise HTTPException(503, "MCP 尚未初始化")
    if not _mcp_client.owns_tool(body.name):
        raise HTTPException(404, "MCP 工具未连接或未在配置白名单中")
    result = await _tool_manager.call(body.name, body.arguments)
    return {"success": result.success, "data": result.data, "error": result.error,
            "cached": result.cached, "latency_ms": round(result.latency_ms, 1)}


app.mount("/mcp", standard_mcp_app)


# ── 交互式 CLI ────────────────────────────────────────────────────────────────
async def _cli():
    print(BANNER)
    print("LlmAgentSystem CLI — 输入 quit 退出\n")

    from agents.agent_orchestrator import AgentOrchestrator, Request
    from memory.conversation_memory import MemoryManager, MsgRole
    from core.skill_loader import SkillManager

    cfg = _anthropic_cfg()
    skill_manager = SkillManager(
        root_dir=os.getenv("LLM_AGENT_SYSTEM_SKILLS_DIR", str(pathlib.Path(_ROOT) / "skills")),
        max_prompt_chars=int(os.getenv("LLM_AGENT_SYSTEM_SKILLS_MAX_PROMPT_CHARS", "5000")),
    )
    skill_manager.load()
    orch = AgentOrchestrator(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        skill_manager=skill_manager,
    )
    mem  = MemoryManager(
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        chroma_host=os.getenv("CHROMA_HOST", "localhost"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/tmp/chroma"),
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )

    from tooling.tool_manager import MCPToolManager, Tool
    from tooling.knowledge_base import KnowledgeBase
    from tooling.mcp_client import StandardMCPClient, load_servers
    from agents.tools import build_shared_rag_tools
    manager = MCPToolManager(**cfg)
    kb = KnowledgeBase(chroma_host=os.getenv("CHROMA_HOST", "localhost"),
                       chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
                       chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/tmp/chroma"))
    manager.register(Tool(name="knowledge_search", description="本地知识库检索",
                          handler=kb.search_handler, schema={"type":"object", "properties":{
                              "query":{"type":"string"}, "top_k":{"type":"integer", "minimum":1}},
                              "required":["query"]}, cache_ttl=300, supports_rerank=True))
    bridge = None
    def refresh():
        tools = build_shared_rag_tools(manager)
        if bridge is not None:
            tools.update(bridge.agent_tools())
        orch.set_shared_tools(tools)
    path = pathlib.Path(os.getenv("MCP_SERVERS_CONFIG", str(pathlib.Path(_ROOT) / "config/mcp_servers.json")))
    enabled = os.getenv("MCP_EXTERNAL_ENABLED", "true").lower() in {"1", "true", "yes"}
    bridge = StandardMCPClient(manager, load_servers(path) if enabled else [], refresh)
    refresh()
    await bridge.start()

    user_id, conv_id = "cli_user", str(uuid.uuid4())

    try:
        while True:
            try:
                msg = (await asyncio.to_thread(input, "你: ")).strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见 ʕ•ᴥ•ʔ")
                break
            if not msg or msg.lower() in ("quit", "exit", "退出"):
                print("再见 ʕ•ᴥ•ʔ")
                break

            ctx = await mem.get_context(user_id, conv_id, query=msg)
            history = [
                {"role": m.role.value, "content": m.content}
                for m in ctx.recent_messages[-5:]
            ] if ctx.recent_messages else None
            req = Request(message=msg, user_id=user_id, conv_id=conv_id, context=ctx.to_prompt_text(), history=history)
            result = await orch.run(req)

            await mem.add_message(user_id, conv_id, MsgRole.USER, msg)
            await mem.add_message(user_id, conv_id, MsgRole.ASSISTANT, result.response)

            print(f"\nLlmAgentSystem [{result.agent_type.value}]: {result.response}\n")
    finally:
        await bridge.close()
        await manager._client.close()
        await mem.close()


if __name__ == "__main__":
    if "--cli" in sys.argv:
        asyncio.run(_cli())
    else:
        uvicorn.run(
            "api.main:app",
            host=os.getenv("API_HOST", "0.0.0.0"),
            port=int(os.getenv("API_PORT", "8000")),
            reload=os.getenv("APP_ENV") == "development",
        )
