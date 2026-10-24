"""Independent read-only fictional trip service speaking standard MCP over stdio."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import argparse

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ride_agent.domain.catalog import RideCatalog

parser = argparse.ArgumentParser()
parser.add_argument("--catalog")
args = parser.parse_args()
catalog = RideCatalog(args.catalog)
server = FastMCP("LLM-Agent-System Demo Service")


@server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
def read_trip_record(trip_id: str) -> dict:
    "Read a fictional trip's recorded status and payment amounts; never execute changes."
    try:
        trip = catalog.trip(trip_id)
    except KeyError:
        raise ValueError("Fictional trip not found")
    return dict(
        trip_id=trip_id,
        status=trip["status"],
        settled_cents=trip["total"],
        estimated_cents=trip["estimated"],
        source="Independent MCP demo service",
        fictional=True,
        policy_scope=catalog.scope(trip),
    )


if __name__ == "__main__":
    server.run(transport="stdio")
