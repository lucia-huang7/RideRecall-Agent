"""官方 MCP SDK 客户端：工具发现、会话生命周期、白名单和 Agent 适配。"""
import asyncio
import hashlib
import json
import logging
import os
import pathlib
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Implementation
from mcp.client.streamable_http import streamable_http_client

from agents.tools import AgentToolSpec
from tooling.tool_manager import MCPToolManager, Tool

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    name: str
    transport: str
    allowed_tools: List[str]
    agents: List[str]
    url: str = ""
    command: str = ""
    args: List[str] = field(default_factory=list)
    headers_env: Dict[str, str] = field(default_factory=dict)
    env_vars: List[str] = field(default_factory=list)
    read_only: bool = True
    timeout_s: float = 25
    cache_ttl: float = 300
    max_result_chars: int = 12000

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,24}", self.name):
            raise ValueError("MCP 服务名必须是 1–25 位字母、数字、下划线或连字符")
        if self.transport not in {"streamable_http", "stdio"}:
            raise ValueError("MCP 仅支持 streamable_http 或 stdio")
        if not self.allowed_tools or not all(isinstance(n, str) and n for n in self.allowed_tools):
            raise ValueError("必须显式配置 MCP allowed_tools 白名单")
        if not self.agents or not set(self.agents) <= {"general", "technical", "billing", "escalation"}:
            raise ValueError("MCP agents 必须是有效 Agent 类型")
        if self.transport == "streamable_http":
            url = urlsplit(self.url)
            if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
                raise ValueError("MCP URL 必须是无内嵌凭据的 HTTP(S) 地址")
        elif not self.command:
            raise ValueError("stdio MCP 必须配置 command")
        if not 1 <= self.timeout_s <= 120 or not 0 <= self.cache_ttl <= 3600 or not 256 <= self.max_result_chars <= 50000:
            raise ValueError("MCP 超时、缓存或输出上限超出允许范围")


def load_servers(path: pathlib.Path) -> List[MCPServerConfig]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("servers"), list):
        raise ValueError("MCP 配置必须包含 servers 数组")
    configs = []
    for item in data["servers"]:
        if not isinstance(item, dict):
            raise ValueError("MCP server 配置必须是对象")
        values = dict(item)
        if not values.pop("enabled", True):
            continue
        configs.append(MCPServerConfig(**values))
    if len(configs) > 16 or len({c.name for c in configs}) != len(configs):
        raise ValueError("MCP 服务最多 16 个且名称不能重复")
    return configs


def tool_alias(server: str, remote_name: str) -> str:
    raw = f"mcp_{server}_{remote_name}"
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", raw)
    if cleaned != raw or len(cleaned) > 64:
        cleaned = cleaned[:55] + "_" + hashlib.sha256(raw.encode()).hexdigest()[:8]
    return cleaned


class MCPRemoteError(RuntimeError):
    pass


class _Connection:
    def __init__(self, config):
        self.config = config
        self.session = None
        self.task = None
        self.stop = asyncio.Event()
        self.reconnect = asyncio.Event()
        self.refresh = asyncio.Event()
        self.ready = asyncio.Event()
        self.protocol_version = None
        self.state = "connecting"
        self.error = None


class StandardMCPClient:
    def __init__(self, manager: MCPToolManager, configs: List[MCPServerConfig], on_change=None):
        self.manager = manager
        self.connections = {c.name: _Connection(c) for c in configs}
        self._agent_tools = {}
        self._origins = {}
        self._on_change = on_change

    async def start(self):
        for connection in self.connections.values():
            connection.task = asyncio.create_task(self._serve(connection))
        if self.connections:
            async def wait_ready(c):
                try:
                    await asyncio.wait_for(c.ready.wait(), c.config.timeout_s + 5)
                except asyncio.TimeoutError:
                    c.error = "startup_timeout"
            await asyncio.gather(*(wait_ready(c) for c in self.connections.values()))

    def agent_tools(self, manager=None):
        if manager is None:
            return dict(self._agent_tools)
        from dataclasses import replace
        result = {}
        for alias, spec in self._agent_tools.items():
            server, name = self._origins[alias]
            async def handler(req, args, alias=alias, server=server, name=name):
                response = await manager.call(alias, args, context={"user_id": req.user_id})
                return {"success": response.success, "data": response.data, "error": response.error,
                        "cached": response.cached, "source": "external_mcp", "mcp_server": server, "mcp_tool": name}
            result[alias] = replace(spec, handler=handler)
        return result

    def owns_tool(self, alias):
        return alias in self._origins

    def summary(self):
        return {"servers": [{
            "name": c.config.name, "transport": c.config.transport, "state": c.state,
            "protocol_version": c.protocol_version, "error": c.error,
            "agents": c.config.agents,
            "tools": [{"name": alias, "remote_name": origin[1]} for alias, origin in self._origins.items() if origin[0] == c.config.name],
        } for c in self.connections.values()]}

    async def _serve(self, connection):
        config = connection.config
        delay = 1
        while not connection.stop.is_set():
            try:
                # AnyIO 传输上下文始终由同一个任务进入和退出。
                async with AsyncExitStack() as stack:
                    if config.transport == "streamable_http":
                        headers = {}
                        for header, variable in config.headers_env.items():
                            value = os.getenv(variable)
                            if not value:
                                raise ValueError(f"MCP 认证环境变量未配置: {variable}")
                            headers[header] = value
                        http = await stack.enter_async_context(httpx.AsyncClient(
                            headers=headers, timeout=httpx.Timeout(config.timeout_s, connect=min(10, config.timeout_s)),
                            follow_redirects=False))
                        read, write, _ = await stack.enter_async_context(streamable_http_client(config.url, http_client=http))
                    else:
                        # 默认不把应用模型 Key 等环境变量传入外部子进程。
                        env = {k: os.environ[k] for k in {"PATH", "SYSTEMROOT", "HOME", *config.env_vars} if k in os.environ}
                        read, write = await stack.enter_async_context(stdio_client(
                            StdioServerParameters(command=config.command, args=config.args, env=env)))
                    async def on_message(message):
                        notification = getattr(message, "root", message)
                        if getattr(notification, "method", None) == "notifications/tools/list_changed":
                            connection.refresh.set()
                    session = await stack.enter_async_context(ClientSession(read, write,
                        read_timeout_seconds=timedelta(seconds=config.timeout_s), message_handler=on_message,
                        client_info=Implementation(name="backend", version="2.0")))
                    initialized = await asyncio.wait_for(session.initialize(), config.timeout_s)
                    connection.refresh.clear()
                    catalog = await self._discover(session, config.timeout_s)
                    connection.session = session
                    connection.protocol_version = initialized.protocolVersion
                    connection.state, connection.error = "connected", None
                    connection.reconnect.clear()
                    self._register_catalog(connection, catalog)
                    connection.ready.set()
                    delay = 1
                    while not connection.stop.is_set():
                        stop = asyncio.create_task(connection.stop.wait())
                        broken = asyncio.create_task(connection.reconnect.wait())
                        changed = asyncio.create_task(connection.refresh.wait())
                        try:
                            await asyncio.wait([stop, broken, changed], return_when=asyncio.FIRST_COMPLETED)
                        finally:
                            for task in (stop, broken, changed):
                                task.cancel()
                            await asyncio.gather(stop, broken, changed, return_exceptions=True)
                        if connection.stop.is_set() or connection.reconnect.is_set():
                            break
                        if connection.refresh.is_set():
                            connection.refresh.clear()
                            catalog = await self._discover(session, config.timeout_s)
                            self._remove_server(config.name)
                            self._register_catalog(connection, catalog)

            except asyncio.CancelledError:
                raise
            except Exception as ex:
                connection.state = "disconnected"
                connection.error = type(ex).__name__
                logger.warning("MCP 服务 %s 连接失败: %s", config.name, connection.error)
            finally:
                connection.session = None
                if not connection.stop.is_set():
                    connection.state = "disconnected"
                self._remove_server(config.name)
                connection.ready.set()
            if not connection.stop.is_set():
                try:
                    await asyncio.wait_for(connection.stop.wait(), delay)
                except asyncio.TimeoutError:
                    pass
                delay = min(delay * 2, 30)
        connection.state = "closed"

    @staticmethod
    async def _discover(session, timeout):
        catalog, cursor = [], None
        for _ in range(8):
            page = await asyncio.wait_for(session.list_tools(cursor=cursor), timeout)
            catalog.extend(page.tools)
            cursor = page.nextCursor
            if not cursor:
                return catalog
        raise ValueError("MCP tools/list 分页超过上限")

    def _register_catalog(self, connection, catalog):
        config = connection.config
        for remote in catalog:
            if remote.name not in config.allowed_tools:
                continue
            annotations = getattr(remote, "annotations", None)
            if config.read_only and (annotations is None or annotations.readOnlyHint is not True or annotations.destructiveHint is True):
                continue
            from jsonschema import validators
            validators.validator_for(remote.inputSchema).check_schema(remote.inputSchema)
            alias = tool_alias(config.name, remote.name)
            self._origins[alias] = (config.name, remote.name)

            async def handler(params, context, server=config.name, name=remote.name):
                return await self._call_remote(server, name, params)

            self.manager.register(Tool(name=alias, description=remote.description or remote.name,
                                       handler=handler, schema=remote.inputSchema,
                                       cache_ttl=config.cache_ttl if config.read_only else 0,
                                       timeout_s=config.timeout_s))

            async def agent_handler(req, args, tool_name=alias, server=config.name, name=remote.name):
                result = await self.manager.call(tool_name, args, context={"user_id": req.user_id})
                return {"success": result.success, "data": result.data, "error": result.error,
                        "cached": result.cached, "source": "external_mcp", "mcp_server": server, "mcp_tool": name}

            self._agent_tools[alias] = AgentToolSpec(
                name=alias, description="外部 MCP 资料工具：" + (remote.description or remote.name)[:1500],
                input_schema=remote.inputSchema, handler=agent_handler, allowed_agents=tuple(config.agents),
                retry_safe=config.read_only or bool(annotations and annotations.readOnlyHint is True and annotations.destructiveHint is not True))
        if self._on_change:
            self._on_change()

    def _remove_server(self, name):
        for alias, origin in list(self._origins.items()):
            if origin[0] == name:
                self.manager.unregister(alias)
                self._origins.pop(alias, None)
                self._agent_tools.pop(alias, None)
        if self._on_change:
            self._on_change()

    async def _call_remote(self, server, name, arguments):
        connection = self.connections[server]
        if connection.session is None:
            raise MCPRemoteError(f"MCP 服务 {server} 未连接")
        try:
            result = await connection.session.call_tool(name, arguments)
        except Exception:
            connection.reconnect.set()
            raise
        if result.isError:
            raise MCPRemoteError(f"MCP 服务 {server} 工具 {name} 返回执行错误")
        content = []
        for block in result.content:
            if block.type == "text":
                content.append({"type": "text", "text": block.text})
            elif block.type == "resource":
                content.append({"type": "resource", "uri": str(block.resource.uri), "text": getattr(block.resource, "text", "")})
            else:
                content.append({"type": block.type})
        structured = getattr(result, "structuredContent", None)
        if structured is None and len(content) == 1 and content[0]["type"] == "text":
            try:
                decoded = json.loads(content[0]["text"], parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
                if isinstance(decoded, (dict, list)):
                    structured, content = decoded, []
            except ValueError:
                pass
        elif structured is not None:
            # SDK 常把结构化结果同时复制到文本块；只移除完全相同的副本。
            remaining = []
            for block in content:
                try:
                    duplicate = block["type"] == "text" and json.loads(block["text"]) == structured
                except ValueError:
                    duplicate = False
                if not duplicate:
                    remaining.append(block)
            content = remaining
        return bounded_result({"structured": structured, "content": content}, server, name,
                              connection.config.max_result_chars)

    async def close(self):
        tasks = []
        for c in self.connections.values():
            c.stop.set()
            if c.task:
                tasks.append(c.task)
        if tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)
            except asyncio.TimeoutError:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        for connection in self.connections.values():
            connection.state = "closed"



def bounded_result(payload, server, tool, max_chars):
    """按字段裁剪，保留有效 JSON 和优先保留来源；不截断 JSON 字符串。"""
    encode = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    metadata = {"server": server, "tool": tool, "truncated": False}
    remaining = max_chars - len(encode(metadata)) + 1
    omitted = object()

    def trim(value):
        nonlocal remaining
        if isinstance(value, (dict, list)):
            if remaining < 2:
                metadata["truncated"] = True
                return omitted
            remaining -= 2
            result = {} if isinstance(value, dict) else []
            items = value.items() if isinstance(value, dict) else enumerate(value)
            if isinstance(value, dict):
                priority = lambda item: 0 if item[0] in {"url", "uri", "title", "source", "id", "type"} else 2 if item[0] in {"text", "content"} else 1
                items = sorted(items, key=priority)
            for key, child in items:
                overhead = (1 if result else 0) + (len(encode(key)) + 1 if isinstance(value, dict) else 0)
                if remaining < overhead + 2:
                    metadata["truncated"] = True
                    break
                remaining -= overhead
                kept = trim(child)
                if kept is omitted:
                    remaining += overhead
                    break
                if isinstance(result, dict):
                    result[key] = kept
                else:
                    result.append(kept)
            return result
        encoded = encode(value)
        if len(encoded) <= remaining:
            remaining -= len(encoded)
            return value
        metadata["truncated"] = True
        if isinstance(value, str) and remaining >= 3:
            low, high = 0, len(value)
            while low < high:
                middle = (low + high + 1) // 2
                if len(encode(value[:middle] + "…")) <= remaining:
                    low = middle
                else:
                    high = middle - 1
            result = value[:low] + "…"
            remaining -= len(encode(result))
            return result
        return omitted

    kept = trim(payload)
    if kept is not omitted:
        metadata.update(kept)
    return metadata
