import asyncio
import json
import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from ride_agent.agent.agents import BillingAgent, GeneralAgent
from ride_agent.agent.types import Request
from ride_agent.integrations.mcp_client import (
    MCPServerConfig,
    StandardMCPClient,
    tool_alias,
)
from ride_agent.retrieval.policy_index import MCPToolManager


def test_stdio_discovery_whitelist_and_agent_tool_roundtrip():

    async def run():
        manager = MCPToolManager(api_key="test")
        config = MCPServerConfig(
            name="fixture",
            transport="stdio",
            allowed_tools=["read_document", "dangerous_write"],
            agents=["general", "technical"],
            command=sys.executable,
            args=[str(pathlib.Path(__file__).with_name("mcp_fixture_server.py"))],
        )
        client = StandardMCPClient(manager, [config])
        try:
            await client.start()
            assert client.summary()["servers"][0]["state"] == "connected"
            alias = "mcp_fixture_read_document"
            assert client.owns_tool(alias) and (not client.owns_tool("mcp_fixture_dangerous_write"))
            remote = await manager.call(alias, {"query": "hello"})
            assert remote.success and remote.data["structured"]["text"] == "HELLO"
            version = manager._cache_versions[alias]
            await client.connections["fixture"].session.call_tool("refresh_catalog", {})
            for _ in range(100):
                if manager._cache_versions[alias] > version:
                    break
                await asyncio.sleep(0.01)
            assert manager._cache_versions[alias] > version
            assert client.summary()["servers"][0]["state"] == "connected"
            bad = await manager.call(alias, {"query": True})
            assert not bad.success
            llm = SimpleNamespace(
                messages=SimpleNamespace(
                    create=AsyncMock(
                        side_effect=[
                            SimpleNamespace(
                                stop_reason="tool_use",
                                content=[
                                    SimpleNamespace(
                                        type="tool_use",
                                        id="t1",
                                        name=alias,
                                        input={"query": "proof"},
                                    )
                                ],
                            ),
                            SimpleNamespace(
                                stop_reason="end_turn",
                                content=[SimpleNamespace(type="text", text="读取完成")],
                            ),
                        ]
                    )
                )
            )
            general = GeneralAgent(llm, "test-model")
            general.set_shared_tools(client.agent_tools())
            billing = BillingAgent(llm, "test-model")
            billing.set_shared_tools(client.agent_tools())
            assert alias in general.get_tools() and alias not in billing.get_tools()
            result = await general._call_llm(Request(message="读取资料", user_id="u", conv_id="c"))
            assert result.content == "读取完成"
            assert result.tool_traces[0]["source"] == "external_mcp"
            assert result.tool_traces[0]["mcp_server"] == "fixture"
            assert result.tool_traces[0]["result_success"] is True
        finally:
            await client.close()
            await manager._client.close()
        assert not client.agent_tools() and client.summary()["servers"][0]["state"] == "closed"

    asyncio.run(run())


@pytest.mark.parametrize(
    "changes",
    [
        dict(allowed_tools=[]),
        dict(agents=["unknown"]),
        dict(url="file:///tmp/data"),
        dict(timeout_s=0),
    ],
)
def test_invalid_mcp_configuration_is_rejected(changes):
    values = dict(
        name="test",
        transport="streamable_http",
        url="https://example.com/mcp",
        allowed_tools=["search"],
        agents=["general"],
    )
    values.update(changes)
    with pytest.raises(ValueError):
        MCPServerConfig(**values)


def test_alias_is_stable_and_valid_for_model_tool_names():
    import re

    a = tool_alias("test", "very.long.name" * 12)
    assert len(a) <= 64 and re.fullmatch("[A-Za-z0-9_-]+", a)
    assert a == tool_alias("test", "very.long.name" * 12)
    assert tool_alias("test", "some.name") != tool_alias("test", "some_name")


def test_large_external_result_remains_structured_and_keeps_source_url():
    from ride_agent.integrations.mcp_client import bounded_result

    result = bounded_result(
        {
            "structured": {
                "results": [
                    {
                        "content": "大段资料" * 1000,
                        "title": "身份认证文档",
                        "url": "https://learn.microsoft.com/example",
                    }
                ]
            },
            "content": [],
        },
        "mslearn",
        "search",
        500,
    )
    assert result["truncated"]
    row = result["structured"]["results"][0]
    assert row["url"] == "https://learn.microsoft.com/example"
    assert row["content"].endswith("…")
    assert len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))) <= 500
    assert json.loads(json.dumps(result)) == result
