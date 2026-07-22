"""Dependency-aware readiness without exposing connection details or customer data."""
import asyncio


async def readiness(app):
    service = app.state.service
    components = {'startup': bool(getattr(app.state, 'started', False)),
                  'database': False, 'policy_index': False, 'mcp': False}
    try:
        components['database'] = await asyncio.to_thread(app.state.store.ping)
    except Exception:
        pass
    try:
        components['policy_index'] = await asyncio.to_thread(
            lambda: all(kb._collection.count() > 0 for kb in service.engine.knowledge.values()))
    except Exception:
        pass
    if components['startup']:
        try:
            trip_id = service.catalog.trips[0]['id']
            result = await asyncio.wait_for(service.engine.rag_tools.call('mcp_rides_read_trip_record', {'trip_id': trip_id}), timeout=2.5)
            components['mcp'] = bool(result.success and result.data.get('structured', {}).get('trip_id') == trip_id)
        except Exception:
            pass
    ready = all(components.values())
    return dict(status='ok' if ready else 'not_ready', components=components), 200 if ready else 503
