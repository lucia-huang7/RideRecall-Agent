import asyncio
from unittest.mock import patch

from tooling.tool_manager import MCPToolManager, Tool, CircuitBreaker, CircuitState


def test_half_open_allows_one_probe_and_failure_reopens():
    breaker = CircuitBreaker(failure_threshold=2, recovery_s=0)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.allow()
    assert breaker.state == CircuitState.HALF_OPEN
    assert not breaker.allow()
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN
    assert breaker.allow()
    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED


def test_cache_scope_invalidation_and_disabled_cache():
    async def run():
        manager = MCPToolManager(api_key='test')
        calls = []
        async def handler(params, context):
            calls.append(context['user_id'])
            return {'user': context['user_id'], 'call': len(calls)}
        manager.register(Tool('lookup', '', handler, {'required': ['id']}, cache_ttl=30))
        a = await manager.call('lookup', {'id': '1'}, {'user_id': 'a'})
        a.data['user'] = 'modified'
        cached = await manager.call('lookup', {'id': '1'}, {'user_id': 'a'})
        assert cached.cached and cached.data['user'] == 'a'
        b = await manager.call('lookup', {'id': '1'}, {'user_id': 'b'})
        assert not b.cached and b.data['user'] == 'b'
        manager.invalidate_cache('lookup')
        fresh = await manager.call('lookup', {'id': '1'}, {'user_id': 'a'})
        assert not fresh.cached
        await manager.call('lookup', {'id': '1'}, {'user_id': 'a'}, use_cache=False)
        again = await manager.call('lookup', {'id': '1'}, {'user_id': 'a'})
        assert again.data == fresh.data
    asyncio.run(run())


def test_in_flight_old_result_does_not_refill_invalidated_cache():
    async def run():
        manager = MCPToolManager(api_key='test')
        started, finish = asyncio.Event(), asyncio.Event()
        async def handler(params, context):
            started.set()
            await finish.wait()
            return ['old']
        manager.register(Tool('lookup', '', handler, {}, cache_ttl=30))
        task = asyncio.create_task(manager.call('lookup', {}))
        await started.wait()
        manager.invalidate_cache('lookup')
        finish.set()
        await task
        assert manager._get_cache('lookup', {}) is None
    asyncio.run(run())


def test_bad_parameters_do_not_trip_circuit():
    async def run():
        manager = MCPToolManager(api_key='test')
        async def handler(params, context):
            raise AssertionError('should not execute')
        tool = Tool('lookup', '', handler, {'required': ['id']})
        manager.register(tool)
        for _ in range(6):
            assert not (await manager.call('lookup', {})).success
        assert tool.breaker.state == CircuitState.CLOSED
    asyncio.run(run())


def test_rerank_repairs_missing_and_duplicate_indices_and_records_status():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    async def run():
        manager = MCPToolManager(api_key='test')
        manager._client.messages.create = AsyncMock(return_value=SimpleNamespace(
            content=[{'type':'text','text':'```json\n[2,2]\n```'}], stop_reason='end_turn'))
        data, changed = await manager._rerank_with_status('query', ['a','b','c','d'], 3)
        assert changed and data == ['c','a','b']
        manager._client.messages.create = AsyncMock(return_value=SimpleNamespace(
            content=[{'type':'text','text':'[9]'}], stop_reason='end_turn'))
        data, changed = await manager._rerank_with_status('query', ['a','b','c'], 2)
        assert not changed and data == ['a','b']
        data, changed = await manager._rerank_with_status('query', ['a'], 2)
        assert not changed and data == ['a']
    asyncio.run(run())


def test_failed_rerank_is_not_reported_as_success_or_cached_as_reranked():
    from unittest.mock import AsyncMock
    async def run():
        manager = MCPToolManager(api_key='test')
        async def handler(params, ctx):
            return ['a','b','c']
        manager.register(Tool('lookup', '', handler, {}, cache_ttl=30, supports_rerank=True))
        manager._client.messages.create = AsyncMock(side_effect=RuntimeError('model unavailable'))
        result = await manager.call('lookup', {}, rerank_top_k=2)
        assert result.success and not result.reranked and result.data == ['a','b']
        cached = await manager.call('lookup', {}, rerank_top_k=2)
        assert cached.cached and not cached.reranked
    asyncio.run(run())
