"""Knowledge server."""

import json
import os
from typing import Annotated, Any, Dict, List

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

hosts = os.getenv(
    "MCP_ALLOWED_HOSTS",
    "localhost,localhost:*,127.0.0.1,127.0.0.1:*,llmagentsystem,llmagentsystem:*,llmagentsystem-python,llmagentsystem-python:*",
)
origins = os.getenv(
    "MCP_ALLOWED_ORIGINS",
    "http://localhost,http://localhost:*,http://127.0.0.1,http://127.0.0.1:*",
)
server = FastMCP(
    "LlmAgentSystem Knowledge",
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[s.strip() for s in hosts.split(",") if s.strip()],
        allowed_origins=[s.strip() for s in origins.split(",") if s.strip()],
    ),
)
knowledge_base = None
tool_manager = None


def configure(kb, manager=None):
    global knowledge_base, tool_manager
    knowledge_base, tool_manager = (kb, manager)


class KnowledgeSearchResult(BaseModel):
    results: List[Dict[str, Any]]
    source: str = "llmagentsystem_knowledge"


@server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
async def search_local_knowledge(
    query: Annotated[str, Field(min_length=1, max_length=2000)],
    top_k: Annotated[int, Field(ge=1, le=20, strict=True)] = 5,
) -> KnowledgeSearchResult:
    """Search local knowledge."""
    if knowledge_base is None:
        raise ToolError("Knowledge index is not ready")
    query = query.strip()
    if not query:
        raise ToolError("query must not be empty")
    if tool_manager is not None:
        result = await tool_manager.call("knowledge_search", {"query": query, "top_k": top_k})
        if not result.success or result.error:
            raise ToolError("Knowledge retrieval failed; retry later")
        return KnowledgeSearchResult(results=result.data)
    return KnowledgeSearchResult(results=await knowledge_base.search_async(query, top_k))


@server.resource("llmagentsystem://knowledge/stats")
async def knowledge_stats() -> str:
    """Knowledge stats."""
    if knowledge_base is None:
        raise ValueError("Knowledge index is not ready")
    return json.dumps({"total_chunks": await knowledge_base.doc_count_async()})


http_app = server.streamable_http_app()
