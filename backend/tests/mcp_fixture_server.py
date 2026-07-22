"""标准 MCP stdio 回归测试服务，不连接外部网络。"""
from mcp.server.fastmcp import FastMCP, Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel


class Document(BaseModel):
    query: str
    text: str

server = FastMCP('KinderUber Test')


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def read_document(query: str) -> Document:
    return Document(query=query, text=query.upper())


@server.tool(annotations=ToolAnnotations(destructiveHint=True))
def dangerous_write(value: str) -> str:
    return value


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def refresh_catalog(ctx: Context) -> Document:
    await ctx.session.send_tool_list_changed()
    return Document(query="refresh", text="catalog updated")


if __name__ == '__main__':
    server.run(transport='stdio')
