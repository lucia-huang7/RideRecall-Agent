"""
亮点：MCP 工具调用框架

核心问题：工具调用出错（检索不全、召回不好）怎么优化？

本模块的答案：
  1. 查询改写（Query Rewriting）—— 用 LLM 把用户原始问题扩写成多个角度的子查询，
     再合并去重，解决"召回不全"问题。
  2. 结果重排（Reranking）—— 对召回结果用 LLM 打分，按相关性重新排序，
     解决"召回不好/排序差"问题。
  3. 熔断器（Circuit Breaker）—— 连续失败超阈值时自动断开，防止雪崩。
  4. 结果缓存（TTL Cache）—— 相同参数直接返回缓存，减少重复调用。
  5. 降级策略（Fallback）—— 工具不可用时返回有意义的降级结果。
"""
import asyncio
import copy
import hashlib
import inspect
import json
import logging
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from anthropic import AsyncAnthropic

from core.llm_utils import extract_text_content

logger = logging.getLogger(__name__)


# ── 数据结构 ──────────────────────────────────────────────────────────────────

class CircuitState(Enum):
    CLOSED    = "closed"     # 正常
    OPEN      = "open"       # 熔断，拒绝请求
    HALF_OPEN = "half_open"  # 探测恢复


@dataclass
class ToolResult:
    success:        bool
    data:           Any
    tool_name:      str
    error:          Optional[str] = None
    cached:         bool = False
    latency_ms:     float = 0.0
    reranked:       bool = False   # 是否经过重排


@dataclass
class ToolStats:
    """工具运行时统计，供 Monitor 读取。"""
    total:              int = 0
    success:            int = 0
    failed:             int = 0
    total_latency_ms:   float = 0.0
    consecutive_fails:  int = 0

    @property
    def success_rate(self) -> float:
        return self.success / self.total if self.total else 1.0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.total if self.total else 0.0


# ── 熔断器 ────────────────────────────────────────────────────────────────────

class CircuitBreaker:
    """
    三态熔断器：CLOSED → OPEN → HALF_OPEN → CLOSED

    连续失败 failure_threshold 次后打开；
    打开 recovery_s 秒后进入 HALF_OPEN 探测；
    探测成功则关闭，失败则重新打开。
    """

    def __init__(self, failure_threshold: int = 5, recovery_s: float = 60.0):
        self.threshold   = failure_threshold
        self.recovery_s  = recovery_s
        self.state       = CircuitState.CLOSED
        self.generation = 0
        self.fail_count  = 0
        self.opened_at:  Optional[float] = None

    def allow(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.monotonic() - self.opened_at >= self.recovery_s:  # type: ignore
                self.generation += 1
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        return False  # 唯一探测尚未结束，其他请求走降级

    def record_success(self, generation=None) -> None:
        if generation is not None and generation != self.generation:
            return
        self.fail_count = 0
        self.state = CircuitState.CLOSED

    def record_failure(self, generation=None) -> None:
        if generation is not None and generation != self.generation:
            return
        self.fail_count += 1
        if self.state == CircuitState.HALF_OPEN or self.fail_count >= self.threshold:
            self.generation += 1
            self.state     = CircuitState.OPEN
            self.opened_at = time.monotonic()
            logger.warning(f"熔断器打开（连续失败 {self.fail_count} 次）")


# ── 工具定义 ──────────────────────────────────────────────────────────────────

@dataclass
class Tool:
    name:        str
    description: str
    handler:     Callable                    # async (params, context) -> Any
    schema:      Dict[str, Any]              # JSON Schema
    cache_ttl:   float = 0.0                 # 0 = 不缓存
    timeout_s:   float = 30.0
    supports_rerank: bool = False            # 是否支持结果重排
    fallback:    Optional[Callable] = None    # sync/async (params, context, error) -> Any

    # 运行时状态（不参与构造）
    stats:   ToolStats    = field(default_factory=ToolStats, init=False)
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker, init=False)


# ── MCP 工具管理器 ────────────────────────────────────────────────────────────

class MCPToolManager:
    """
    MCP 工具调用框架。

    核心优化链路（针对检索类工具）：
      用户查询 → 查询改写（多角度子查询）→ 并行召回 → 结果重排 → 返回 Top-K
    """

    def __init__(self, api_key: str, base_url: Optional[str] = None, model: str = "claude-3-5-sonnet-20241022", client: Any = None):
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = client if client is not None else AsyncAnthropic(**kwargs)
        self._model  = model
        self._tools: Dict[str, Tool] = {}
        self._cache_versions: Dict[str, int] = {}
        self._cache: Dict[str, tuple] = {}   # key → (result, expire_at, reranked)

    def fork(self):
        """共享只读依赖和 HTTP 客户端，隔离工具统计、熔断和缓存。"""
        other = object.__new__(type(self))
        other._client, other._model = self._client, self._model
        other._cache, other._cache_versions = {}, {}
        other._tools = {name: replace(tool) for name, tool in self._tools.items()}
        return other

    # ── 注册 / 注销 ───────────────────────────────────────────────────────────

    def register(self, tool: Tool) -> None:
        self.invalidate_cache(tool.name)
        self._tools[tool.name] = tool
        logger.info(f"注册工具: {tool.name}")

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)
        self.invalidate_cache(name)

    # ── 核心调用 ──────────────────────────────────────────────────────────────

    async def call(
        self,
        name: str,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
        *,
        use_cache: bool = True,
        rerank_top_k: int = 0,          # >0 时对结果重排，取 Top-K
    ) -> ToolResult:
        """
        调用工具，完整执行链：
          缓存检查 → 熔断检查 → 参数校验 → 执行（含超时）→ 可选重排 → 缓存写入
        """
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(success=False, data=None, tool_name=name, error=f"工具不存在: {name}")

        try:
            self._validate_params(tool, params)
        except ValueError as ex:
            # 用户参数错误不应使健康的外部服务被熔断。
            return ToolResult(success=False, data=None, tool_name=name, error=str(ex))
        cache_version = self._cache_versions.get(name, 0)
        cache_rerank_top_k = rerank_top_k if rerank_top_k > 0 and tool.supports_rerank else 0

        # 缓存命中
        if use_cache and tool.cache_ttl > 0:
            cached = self._get_cache(name, params, cache_rerank_top_k, context)
            if cached is not None:
                cached_data, cached_reranked = cached
                tool.stats.total += 1
                tool.stats.success += 1
                return ToolResult(
                    success=True,
                    data=cached_data,
                    tool_name=name,
                    cached=True,
                    reranked=cached_reranked,
                )

        # 熔断检查
        if not tool.breaker.allow():
            error = f"工具熔断中: {name}，请稍后重试"
            return await self._fallback_result(tool, params, context, error)

        generation = tool.breaker.generation
        t0 = time.monotonic()
        tool.stats.total += 1
        try:
            data = await asyncio.wait_for(self._run_handler(tool, params, context), timeout=tool.timeout_s)
            latency = (time.monotonic() - t0) * 1000

            # 重排（针对返回列表的检索工具）
            reranked = False
            if rerank_top_k > 0 and tool.supports_rerank and isinstance(data, list):
                query = params.get("query", "")
                data, reranked = await self._rerank_with_status(query, data, rerank_top_k)

            latency = (time.monotonic() - t0) * 1000
            tool.stats.success += 1
            tool.stats.consecutive_fails = 0
            tool.stats.total_latency_ms += latency
            tool.breaker.record_success(generation)

            # 写缓存：缓存最终返回结果，避免下次命中未重排的原始结果。
            if use_cache and tool.cache_ttl > 0 and cache_version == self._cache_versions.get(name, 0):
                self._set_cache(name, params, data, tool.cache_ttl, cache_rerank_top_k, reranked, context)

            return ToolResult(success=True, data=data, tool_name=name,
                              latency_ms=latency, reranked=reranked)

        except asyncio.CancelledError:
            if tool.breaker.state == CircuitState.HALF_OPEN:
                tool.breaker.record_failure(generation)
            raise
        except asyncio.TimeoutError:
            tool.stats.failed += 1
            tool.stats.consecutive_fails += 1
            tool.breaker.record_failure(generation)
            logger.error(f"工具超时: {name} ({tool.timeout_s}s)")
            return await self._fallback_result(tool, params, context, "执行超时")

        except Exception as ex:
            tool.stats.failed += 1
            tool.stats.consecutive_fails += 1
            tool.breaker.record_failure(generation)
            logger.error(f"工具异常: {name} — {ex}")
            return await self._fallback_result(tool, params, context, str(ex))

    async def _fallback_result(
        self,
        tool: Tool,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]],
        error: str,
    ) -> ToolResult:
        """工具不可用时返回降级结果，而不是把空错误直接暴露给调用方。"""
        if tool.fallback is None:
            return ToolResult(success=False, data=None, tool_name=tool.name, error=error)
        try:
            data = tool.fallback(params, context, error)
            if asyncio.iscoroutine(data):
                data = await data
            return ToolResult(
                success=False,
                data=data,
                tool_name=tool.name,
                error=error,
            )
        except Exception as ex:
            logger.error(f"工具降级失败: {tool.name} — {ex}")
            return ToolResult(success=False, data=None, tool_name=tool.name, error=f"{error}; fallback失败: {ex}")

    async def _run_handler(
        self,
        tool: Tool,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]],
    ) -> Any:
        """
        执行工具 handler。

        优先支持 async handler；如果历史工具仍是同步函数，则放入线程池执行，
        避免阻塞事件循环。
        """
        if inspect.iscoroutinefunction(tool.handler):
            return await tool.handler(params, context)
        result = await asyncio.to_thread(tool.handler, params, context)
        if inspect.isawaitable(result):
            return await result
        return result

    # ── 查询改写（解决召回不全）────────────────────────────────────────────────

    async def rewrite_query(self, query: str, n: int = 3) -> List[str]:
        """
        用 LLM 将原始查询改写为 n 个不同角度的子查询。

        目的：单一查询往往只能召回某一角度的文档，
        多角度子查询并行检索后合并，显著提升召回率。

        示例：
          原始: "退款流程"
          改写: ["如何申请退款", "退款需要多少天", "退款政策是什么"]
        """
        prompt = f"""将以下用户查询改写为 {n} 个不同角度的搜索子查询，用于检索知识库。
要求：每个子查询角度不同，覆盖原始问题的不同方面。
原始查询: "{query}"
返回 JSON 数组，例如: ["子查询1", "子查询2", "子查询3"]"""
        prompt = self._clean_text(prompt)
        try:
            resp = await asyncio.wait_for(self._client.messages.create(
                model=self._model, max_tokens=512, temperature=0.3,
                messages=[{"role": "user", "content": prompt}],
            ), timeout=10)
            raw = extract_text_content(resp.content)
            s, e = raw.find("["), raw.rfind("]") + 1
            queries = json.loads(raw[s:e])
            # 原始查询也保留，去重
            return list(dict.fromkeys([query] + [q[:2000] for q in queries if isinstance(q, str) and q.strip()][:n]))
        except Exception as ex:
            logger.warning(f"查询改写失败，使用原始查询: {ex}")
            return [query]

    async def search_with_rewrite(
        self,
        tool_name: str,
        query: str,
        top_k: int = 5,
        context: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        """
        完整的检索优化链路：查询改写 → 并行召回 → 去重 → 重排 → Top-K

        这是解决"检索不全、召回不好"的完整方案。
        """
        if type(top_k) is not int or not 1 <= top_k <= 20:
            return ToolResult(False, [], tool_name, error="top_k 必须是 1–20 的整数")
        if not isinstance(query, str) or not query.strip() or len(query) > 2000:
            return ToolResult(False, [], tool_name, error="query 必须是 1–2000 字符的非空文本")
        # 1. 查询改写：生成多角度子查询
        sub_queries = await self.rewrite_query(query, n=3)
        logger.info(f"查询改写: {query!r} → {sub_queries}")

        # 2. 并行召回：所有子查询同时检索
        recall_k = max(top_k, 5)
        tasks = [
            self.call(tool_name, {"query": q, "top_k": recall_k}, context, use_cache=True)
            for q in sub_queries
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 3. 合并去重（按内容哈希去重）
        seen, merged = set(), []
        for r in results:
            if isinstance(r, ToolResult) and r.success and isinstance(r.data, list):
                for item in r.data:
                    identity = ({k: item.get(k) for k in ("document_id", "title", "content", "chunk")}
                                if isinstance(item, dict) and "content" in item else item)
                    key = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                    if key not in seen:
                        seen.add(key)
                        merged.append(item)

        if not merged:
            return ToolResult(success=False, data=[], tool_name=tool_name, error="检索无可用结果：" + "; ".join(dict.fromkeys(r.error for r in results if isinstance(r, ToolResult) and r.error)))

        # 4. 重排：用 LLM 对合并结果按相关性打分，取 Top-K
        data, reranked = await self._rerank_with_status(query, merged, top_k)
        return ToolResult(success=True, data=data, tool_name=tool_name, reranked=reranked)

    # ── 结果重排（解决召回不好）──────────────────────────────────────────────

    async def _rerank(self, query: str, items: List[Any], top_k: int) -> List[Any]:
        data, _ = await self._rerank_with_status(query, items, top_k)
        return data

    async def _rerank_with_status(self, query: str, items: List[Any], top_k: int) -> Tuple[List[Any], bool]:
        """
        用 LLM 对召回结果重新打分排序。

        解决问题：向量检索的相似度分数不等于"对用户有用"，
        LLM 能理解语义相关性，重排后 Top-K 质量显著提升。
        """
        if len(items) <= top_k:
            return items, False

        # 将结果序列化为文本供 LLM 评分
        items_text = "\n".join(f"{i}. {json.dumps(item, ensure_ascii=False)[:200]}"
                               for i, item in enumerate(items))
        prompt = f"""根据用户查询，对以下检索结果按相关性打分（0-10），返回 JSON 数组。
用户查询: "{query}"
检索结果:
{items_text}

返回格式（按相关性降序排列的索引列表）: [最相关的索引, ..., 最不相关的索引]
只返回 JSON 数组，不要其他文字。"""
        prompt = self._clean_text(prompt)

        try:
            resp = await asyncio.wait_for(self._client.messages.create(
                model=self._model, max_tokens=2048, temperature=0.0,
                messages=[{"role": "user", "content": prompt}],
            ), timeout=30)
            if getattr(resp, "stop_reason", None) == "max_tokens":
                raise ValueError("重排输出被截断")
            raw = extract_text_content(resp.content, separator="")
            decoder = json.JSONDecoder()
            order = None
            for index, char in enumerate(raw):
                if char == "[":
                    try:
                        candidate, _ = decoder.raw_decode(raw[index:])
                    except ValueError:
                        continue
                    if isinstance(candidate, list):
                        order = candidate
                        break
            if not order or any(type(i) is not int or not 0 <= i < len(items) for i in order):
                raise ValueError("重排索引必须是非空、有效的整数列表")
            # 去重后补齐模型遗漏的索引，避免召回结果无故消失。
            order = list(dict.fromkeys(order))
            order.extend(i for i in range(len(items)) if i not in order)
            return [items[i] for i in order[:top_k]], True
        except Exception as ex:
            logger.warning("重排失败，返回原始顺序: %s", type(ex).__name__)
            return items[:top_k], False

    # ── 缓存 ──────────────────────────────────────────────────────────────────

    def _cache_key(self, name: str, params: Dict, rerank_top_k: int = 0, context: Optional[Dict] = None) -> str:
        payload = {"params": params, "rerank_top_k": rerank_top_k, "context": context or {},
                   "version": self._cache_versions.get(name, 0)}
        return f"{name}:{hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()}"

    def _get_cache(self, name: str, params: Dict, rerank_top_k: int = 0, context: Optional[Dict] = None) -> Optional[Tuple[Any, bool]]:
        key = self._cache_key(name, params, rerank_top_k, context)
        if key in self._cache:
            data, expire_at, reranked = self._cache[key]
            if time.monotonic() < expire_at:
                return copy.deepcopy(data), reranked
            del self._cache[key]
        return None

    def _set_cache(
        self,
        name: str,
        params: Dict,
        data: Any,
        ttl: float,
        rerank_top_k: int = 0,
        reranked: bool = False,
        context: Optional[Dict] = None,
    ) -> None:
        if len(self._cache) >= 5000:
            # 清掉最旧的 1/4
            for k in list(self._cache)[:1250]:
                del self._cache[k]
        self._cache[self._cache_key(name, params, rerank_top_k, context)] = (copy.deepcopy(data), time.monotonic() + ttl, reranked)

    def invalidate_cache(self, name: str) -> None:
        """失效工具缓存；版本号阻止更新前的在途请求重新写入旧结果。"""
        self._cache_versions[name] = self._cache_versions.get(name, 0) + 1
        for key in list(self._cache):
            if key.startswith(f"{name}:"):
                del self._cache[key]

    # ── 参数校验 ──────────────────────────────────────────────────────────────

    _TYPE_MAP = {"string": str, "number": (int, float), "integer": int, "boolean": bool, "array": list, "object": dict}

    def _validate_params(self, tool: Tool, params: Dict[str, Any]) -> None:
        """按完整 JSON Schema 校验参数，不合法时抛出 ValueError。"""
        from jsonschema import validators, ValidationError
        validator = validators.validator_for(tool.schema)(tool.schema)
        try:
            validator.validate(params)
        except ValidationError as ex:
            path = ".".join(str(p) for p in ex.absolute_path) or "参数"
            raise ValueError(f"工具 {tool.name} 的 {path} 未通过 JSON Schema 校验（{ex.validator}）") from None

    @staticmethod
    def _clean_text(value: Any) -> str:
        """移除 Unicode 代理字符，避免 LLM 请求编码失败。"""
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        return value.encode("utf-8", errors="ignore").decode("utf-8")

    # ── 统计 ──────────────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        return {
            name: {
                "total": t.stats.total,
                "success_rate": round(t.stats.success_rate, 3),
                "avg_latency_ms": round(t.stats.avg_latency_ms, 1),
                "consecutive_fails": t.stats.consecutive_fails,
                "circuit_state": t.breaker.state.value,
            }
            for name, t in self._tools.items()
        }
