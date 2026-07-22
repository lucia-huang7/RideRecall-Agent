import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.concurrency import KeyedLocks, ProfileUpdater, redis_lease, ConversationBusyError, LeaseLostError


def test_same_conversation_is_fifo_and_other_conversations_can_run():
    async def run():
        locks = KeyedLocks()
        first, finish = asyncio.Event(), asyncio.Event()
        order = []
        async def request(key, number):
            async with locks.hold(key):
                order.append(number)
                if number == 1:
                    first.set()
                    await finish.wait()
        a = asyncio.create_task(request('same', 1))
        await first.wait()
        b = asyncio.create_task(request('same', 2))
        await asyncio.sleep(0)
        await request('other', 3)
        assert order == [1, 3]
        finish.set()
        await asyncio.gather(a, b)
        assert order == [1, 3, 2]
        assert not locks._locks
    asyncio.run(run())


def test_cancelled_waiter_does_not_leak_keyed_lock():
    async def run():
        locks = KeyedLocks()
        async with locks.hold('same'):
            async def wait():
                async with locks.hold('same'):
                    pass
            task = asyncio.create_task(wait())
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert not locks._locks
    asyncio.run(run())


def test_redis_lease_renews_and_releases():
    async def run():
        lock = SimpleNamespace(acquire=AsyncMock(return_value=True), extend=AsyncMock(return_value=True), release=AsyncMock())
        client = SimpleNamespace(lock=lambda *a, **kw: lock)
        async with redis_lease(client, 'key', lease_s=0.03):
            await asyncio.sleep(0.025)
        assert lock.extend.await_count >= 1
        assert lock.release.await_count == 1
    asyncio.run(run())


def test_redis_lease_loss_stops_request():
    async def run():
        lock = SimpleNamespace(acquire=AsyncMock(return_value=True), extend=AsyncMock(return_value=False), release=AsyncMock())
        client = SimpleNamespace(lock=lambda *a, **kw: lock)
        with pytest.raises(LeaseLostError):
            async with redis_lease(client, 'key', lease_s=0.03):
                await asyncio.sleep(10)
        assert lock.release.await_count == 1
    asyncio.run(run())


def test_busy_redis_lease_reports_busy():
    async def run():
        client = SimpleNamespace(lock=lambda *a, **kw: SimpleNamespace(acquire=AsyncMock(return_value=False)))
        with pytest.raises(ConversationBusyError):
            async with redis_lease(client, 'key'):
                pytest.fail('must not run')
    asyncio.run(run())


def test_profile_updates_coalesce_per_user_and_shutdown_cancels_workers():
    async def run():
        entered, finish, other = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = []
        async def update(user, conv):
            calls.append((user, conv))
            if conv == 'first':
                entered.set()
                await finish.wait()
            if user == 'b':
                other.set()
        queue = ProfileUpdater(SimpleNamespace(update_profile=update), workers=2)
        queue.start()
        queue.submit('a', 'first')
        await entered.wait()
        queue.submit('a', 'discarded')
        queue.submit('a', 'latest')
        queue.submit('b', 'parallel')
        await asyncio.wait_for(other.wait(), 1)
        finish.set()
        await asyncio.wait_for(queue._queue.join(), 1)
        assert ('a', 'discarded') not in calls
        assert calls.index(('a', 'first')) < calls.index(('a', 'latest'))
        await queue.close()
        assert not queue._tasks and not queue._pending
        assert not queue.submit('a', 'after-close')
    asyncio.run(run())


def test_profile_shutdown_cleans_active_and_queued_work():
    async def run():
        started = asyncio.Event()
        async def update(user, conv):
            started.set()
            await asyncio.Event().wait()
        queue = ProfileUpdater(SimpleNamespace(update_profile=update), workers=1)
        queue.start()
        queue.submit('a', 'active')
        await started.wait()
        queue.submit('b', 'queued')
        await asyncio.wait_for(queue.close(), 1)
        await asyncio.wait_for(queue._queue.join(), 1)
        assert queue._queue.empty() and not queue._tasks and not queue._pending
    asyncio.run(run())
