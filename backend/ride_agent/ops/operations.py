"""English operations monitor and independent MCP client lifecycle."""

import asyncio
import sys
from dataclasses import replace
from pathlib import Path

from prometheus_client import CollectorRegistry, generate_latest

from ride_agent.evaluation.quality import RideQuality
from ride_agent.integrations.mcp_client import MCPServerConfig, StandardMCPClient
from ride_agent.ops.performance_monitor import PerformanceMonitor


class CombinedTools:
    def __init__(self, engine):
        self.engine = engine

    def get_stats(self):
        return {
            **self.engine.support.tools.get_stats(),
            **self.engine.rag_tools.get_stats(),
        }


class RideOperations:
    def __init__(self, engine, orchestrator):
        self.engine = engine
        self.monitor = PerformanceMonitor(
            orchestrator,
            CombinedTools(engine),
            interval_s=5,
            registry=CollectorRegistry(),
        )
        config = MCPServerConfig(
            name="rides",
            transport="stdio",
            allowed_tools=["read_trip_record"],
            agents=["general", "technical", "billing", "escalation"],
            command=sys.executable,
            args=[str(Path(__file__).resolve().parents[1] / "integrations" / "mcp_server.py")]
            + (["--catalog", engine.support.catalog.path] if engine.support.catalog.path else []),
            cache_ttl=0,
            timeout_s=3,
            read_only=True,
        )
        self.bridge = StandardMCPClient(engine.rag_tools, [config])
        self.lock = asyncio.Lock()

        self.quality = RideQuality(engine)

    async def start(self):
        await self.bridge.start()
        await self.monitor.start()

    async def close(self):
        await self.quality.jobs.close()
        await self.monitor.stop()
        await self.bridge.close()

    def scoped_tools(self, trip_id):
        tools = {}
        for alias, spec in self.bridge.agent_tools().items():
            original = spec.handler

            async def scoped(req, args, original=original):
                return await original(req, {"trip_id": trip_id})

            tools[alias] = replace(
                spec,
                handler=scoped,
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            )
        return tools

    async def status(self):
        await self.monitor._collect()
        summary = self.monitor.summary()
        return dict(
            **summary,
            mcp=self.bridge.summary(),
            active=self.monitor._active,
            interval_seconds=5,
            scope="This server process, including synthetic pipeline checks",
            persistence=("Runtime metrics reset on restart; case execution records persist in SQLite."),
        )

    async def reconnect(self):
        async with self.lock:
            await self.bridge.close()
            configs = [c.config for c in self.bridge.connections.values()]
            self.bridge = StandardMCPClient(self.engine.rag_tools, configs)
            await self.bridge.start()
            return self.bridge.summary()

    def metrics(self):
        return generate_latest(self.monitor._registry)
