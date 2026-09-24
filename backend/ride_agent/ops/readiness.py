"""Dependency-aware readiness without exposing connection details or customer data."""

import asyncio

from ride_agent.ops.configuration import status as configuration_status


async def readiness(app):
    service = app.state.service
    components = {
        "startup": bool(getattr(app.state, "started", False)),
        "database": False,
        "policy_index": False,
        "mcp": False,
        "memory_projection": False,
        "configuration": False,
    }
    try:
        components["database"] = await asyncio.to_thread(app.state.store.ping)
    except Exception:
        pass
    try:
        status = await asyncio.to_thread(app.state.store.projection_status)
        components["memory_projection"] = bool(
            app.state.projections.task
            and (not app.state.projections.task.done())
            and (app.state.projections.last_error is None)
            and (status["retrying"] == 0)
            and (status["oldest_age_seconds"] < 30)
        )
    except Exception:
        pass
    try:
        pending = await asyncio.to_thread(configuration_status, app.state.store)
        components["configuration"] = bool(
            app.state.configuration.task
            and (not app.state.configuration.task.done())
            and (not pending["pending"])
            and (app.state.configuration.last_error is None)
        )
    except Exception:
        pass
    try:
        components["policy_index"] = await asyncio.to_thread(
            lambda: all((kb._collection.count() > 0 for kb in service.engine.knowledge.values()))
        )
    except Exception:
        pass
    if components["startup"]:
        try:
            trip_id = service.catalog.trips[0]["id"]
            result = await asyncio.wait_for(
                service.engine.rag_tools.call("mcp_rides_read_trip_record", {"trip_id": trip_id}),
                timeout=2.5,
            )
            components["mcp"] = bool(result.success and result.data.get("structured", {}).get("trip_id") == trip_id)
        except Exception:
            pass
    ready = all(components.values())
    return (
        dict(status="ok" if ready else "not_ready", components=components),
        200 if ready else 503,
    )
