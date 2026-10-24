"""Concurrency."""

import asyncio
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class ConversationBusyError(RuntimeError):
    pass


class LeaseLostError(RuntimeError):
    pass


class KeyedLocks:
    """Keyed locks."""

    def __init__(self):
        self._locks = {}

    @asynccontextmanager
    async def hold(self, key):
        lock, users = self._locks.get(key, (asyncio.Lock(), 0))
        self._locks[key] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            _, users = self._locks[key]
            if users == 1:
                del self._locks[key]
            else:
                self._locks[key] = (lock, users - 1)


@asynccontextmanager
async def redis_lease(client, key, wait_s=30, lease_s=120):
    """Redis lease."""
    lock = client.lock(key, timeout=lease_s, blocking_timeout=wait_s, thread_local=False)
    if not await lock.acquire():
        raise ConversationBusyError("This conversation is busy; retry later")
    owner = asyncio.current_task()
    lost = False

    async def renew():
        nonlocal lost
        try:
            while True:
                await asyncio.sleep(lease_s / 3)
                if not await lock.extend(lease_s, replace_ttl=True):
                    raise LeaseLostError("Conversation lease renewal failed")
        except asyncio.CancelledError:
            raise
        except Exception:
            lost = True
            owner.cancel()

    renewal = asyncio.create_task(renew())
    try:
        yield
    except asyncio.CancelledError:
        if lost:
            raise LeaseLostError("Conversation lease expired; request aborted") from None
        raise
    finally:
        renewal.cancel()
        await asyncio.gather(renewal, return_exceptions=True)
        try:
            await lock.release()
        except Exception as ex:
            logger.warning("Conversation lease release failed: %s", type(ex).__name__)


class ProfileUpdater:
    """Profile updater."""

    def __init__(self, memory, workers=4, max_pending=256):
        self._memory = memory
        self._worker_count = max(1, workers)
        self._queue = asyncio.Queue(maxsize=max_pending)
        self._pending = {}
        self._scheduled = set()
        self._tasks = []
        self._closed = False

    def start(self):
        self._tasks = [asyncio.create_task(self._worker()) for _ in range(self._worker_count)]

    def submit(self, user_id, conv_id):
        if self._closed:
            return False
        if user_id not in self._scheduled:
            try:
                self._queue.put_nowait(user_id)
            except asyncio.QueueFull:
                logger.warning("Profile update queue full; skipping this update")
                return False
            self._scheduled.add(user_id)
        self._pending[user_id] = conv_id
        return True

    async def _worker(self):
        while not self._closed:
            user_id = await self._queue.get()
            try:
                while user_id in self._pending and (not self._closed):
                    conv_id = self._pending.pop(user_id)
                    try:
                        await (
                            self._memory.maintain(user_id, conv_id)
                            if hasattr(self._memory, "maintain")
                            else self._memory.update_profile(user_id, conv_id)
                        )
                    except Exception as ex:
                        logger.warning("Background profile update failed: %s", type(ex).__name__)
            finally:
                self._scheduled.discard(user_id)
                self._queue.task_done()

    async def close(self):
        self._closed = True
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        while not self._queue.empty():
            self._queue.get_nowait()
            self._queue.task_done()
        self._pending.clear()
        self._scheduled.clear()
        self._tasks.clear()
