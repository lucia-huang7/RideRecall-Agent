"""真实 Redis：键隔离、旧键原子迁移、TTL 和歧义旧键隔离。"""
import asyncio
import os
import uuid
from memory.conversation_memory import MemoryManager, redis

async def main():
    m = MemoryManager.__new__(MemoryManager)
    m._redis = redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
    u = 'key-test-' + uuid.uuid4().hex
    legacy, summary = f'wm:{u}:c', f'summary:{u}:c'
    new, new_summary = m._wm_key(u, 'c'), m._summary_key(u, 'c')
    ambiguous = f'wm:{u}:b:c'
    left, right = m._wm_key(u+':b', 'c'), m._wm_key(u, 'b:c')
    try:
        assert left != right
        await m._redis.rpush(legacy, 'old')
        await m._redis.expire(legacy, 120)
        await m._redis.set(summary, 'summary', ex=120)
        await asyncio.gather(*[m._migrate_legacy_keys(u, 'c') for _ in range(4)])
        assert await m._redis.lrange(new, 0, -1) == ['old']
        assert 0 < await m._redis.ttl(new) <= 120
        assert await m._redis.get(new_summary) == 'summary'
        assert not await m._redis.exists(legacy)
        await m._redis.rpush(legacy, 'must-not-overwrite')
        await m._migrate_legacy_keys(u, 'c')
        assert await m._redis.lrange(new, 0, -1) == ['old']
        await m._redis.rpush(ambiguous, 'unknown-owner')
        await m._migrate_legacy_keys(u+':b', 'c')
        await m._migrate_legacy_keys(u, 'b:c')
        assert not await m._redis.exists(left, right)
        assert await m._redis.exists(ambiguous)
        print('PASS: keys isolated; migration atomic; TTL preserved; ambiguous legacy data not exposed')
    finally:
        await m._redis.delete(legacy, summary, new, new_summary, ambiguous, left, right)
        await m._redis.aclose()

asyncio.run(main())
