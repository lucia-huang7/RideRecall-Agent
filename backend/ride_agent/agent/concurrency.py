"""Per-case locks and renewable leases used during memory maintenance."""

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
