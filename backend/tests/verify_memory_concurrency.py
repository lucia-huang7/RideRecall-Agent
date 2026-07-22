"""真实 Redis 并发回归检查。

在项目根目录运行：PYTHONPATH=. python tests/verify_memory_concurrency.py
从环境读取 REDIS_URL，仅创建随机测试会话，结束后自动清理。
"""
import asyncio
import os
import json
import uuid
import types
from datetime import datetime
from unittest.mock import AsyncMock
from dotenv import load_dotenv
from memory.conversation_memory import MemoryManager, redis

load_dotenv()

async def main():
 m = MemoryManager.__new__(MemoryManager)
 m._redis = redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
 m._model = 'test'
 user = 'regression-' + uuid.uuid4().hex
 conv = 'concurrency'
 key, skey = m._wm_key(user, conv), m._summary_key(user, conv)
 def raw(text):
  return json.dumps({'role':'user','content':text,'ts':datetime.now().isoformat(),'metadata':{}})
 snapshot = [raw('old-'+str(i)) for i in reversed(range(25))]
 started, finish = asyncio.Event(), asyncio.Event()
 async def generate(**kwargs):
  started.set()
  await finish.wait()
  return types.SimpleNamespace(content=[{'type':'text','text':'有效摘要'}], stop_reason='end_turn')
 m._client = types.SimpleNamespace(messages=types.SimpleNamespace(create=generate))
 m._merge_summary = AsyncMock(return_value='有效摘要')
 m._store_episodic = AsyncMock()
 try:
  await m._redis.rpush(key, *snapshot)
  a = asyncio.create_task(m._compress(user, conv))
  b = asyncio.create_task(m._compress(user, conv))
  await started.wait()
  new = raw('new-message')
  await m._redis.lpush(key, new)
  finish.set()
  await asyncio.gather(a, b)
  current = await m._redis.lrange(key, 0, -1)
  assert current == [new] + snapshot[:5], current
  assert await m._redis.get(skey) == '有效摘要'
  print('PASS: 25条快照压缩、并发新增消息保留、并发压缩不会覆盖摘要')
  await m._redis.delete(key,skey)
  await m._redis.rpush(key,*snapshot)
  m._client.messages.create = AsyncMock(side_effect=RuntimeError('model unavailable'))
  await m._compress(user,conv)
  assert await m._redis.lrange(key,0,-1) == snapshot
  print('PASS: 摘要生成失败时保留全部原始消息')
 finally:
  await m._redis.delete(key,skey)
  await m._redis.aclose()
asyncio.run(main())
