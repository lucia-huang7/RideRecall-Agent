"""容器内运行的 Redis/ChromaDB 与 /chat 并发回归，不调用真实模型。"""
import asyncio
import os
import uuid
from types import SimpleNamespace

import chromadb
from dotenv import load_dotenv
from redis.asyncio import Redis

import api.main as api
from agents.agent_orchestrator import AgentType, OrchestratorResult
from core.concurrency import KeyedLocks, redis_lease
from core.intent_recognizer import IntentCategory
from memory.conversation_memory import MemoryManager

load_dotenv()


async def main():
    client = chromadb.HttpClient(host=os.getenv('CHROMA_HOST', 'chromadb'),
                                port=int(os.getenv('CHROMA_PORT', '8000')),
                                settings=chromadb.Settings(anonymized_telemetry=False))
    name = 'regression_' + uuid.uuid4().hex
    collection = client.create_collection(name)
    memory = MemoryManager.__new__(MemoryManager)
    memory._redis = Redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
    memory._request_locks = KeyedLocks()
    memory._episodic = collection
    memory._get_profile = lambda user: asyncio.sleep(0, result={})
    user, conv = 'regression-' + uuid.uuid4().hex, 'request-order'
    key, skey = memory._wm_key(user, conv), memory._summary_key(user, conv)
    lease_key = 'regression-lease:' + uuid.uuid4().hex
    try:
        collection.add(ids=['same','other-conv','other-user'], documents=['同会话历史','同用户其他历史','其他用户历史'], metadatas=[
            {'user_id':user,'conv_id':conv}, {'user_id':user,'conv_id':'other'}, {'user_id':'another','conv_id':conv}])
        history = await memory._search_episodic(user, conv, '历史')
        assert '同会话历史' in history and '同用户其他历史' in history
        assert '其他用户历史' not in history
        print('PASS: ChromaDB 组合条件及同用户跨会话检索，排除其他用户')
        started, finish = asyncio.Event(), asyncio.Event()
        contexts = []
        class Orchestrator:
            def get_tool_trace(self, request_id):
                return None
            async def recognize_intent(self, message, history=None):
                contexts.append((message, history))
                return SimpleNamespace(intent=IntentCategory.REQUEST, intent_group='general',
                                       urgency=None, confidence=1.0, entities={}, source_scores={})
            async def run(self, req):
                if req.message == 'first':
                    started.set()
                    await finish.wait()
                return OrchestratorResult(request_id=uuid.uuid4().hex, response='reply:' + req.message,
                                          agent_type=AgentType.GENERAL, intent=req.intent)
        api._memory, api._orchestrator, api._profile_updater = memory, Orchestrator(), None
        first = asyncio.create_task(api.chat(api.ChatRequest(message='first', user_id=user, conv_id=conv, client_request_id='first'), SimpleNamespace(state=SimpleNamespace(account=None))))
        await started.wait()
        second = asyncio.create_task(api.chat(api.ChatRequest(message='second', user_id=user, conv_id=conv, client_request_id='second'), SimpleNamespace(state=SimpleNamespace(account=None))))
        await asyncio.sleep(0.05)
        assert len(contexts) == 1
        finish.set()
        await asyncio.gather(first, second)
        assert [m.content for m in await memory._get_working_memory(user,conv)] == ['first','reply:first','second','reply:second']
        assert [m['content'] for m in contexts[1][1]] == ['first','reply:first']
        print('PASS: 完整 /chat 链路串行化，第二条请求读到第一条回复')
        async with redis_lease(memory._redis, lease_key, lease_s=0.3):
            await asyncio.sleep(0.45)
            assert await memory._redis.exists(lease_key)
        assert not await memory._redis.exists(lease_key)
        print('PASS: 真实 Redis 锁续租及释放')
    finally:
        await memory._redis.delete(key, skey, memory._state_key(user,conv), lease_key, memory.receipt_key(user,conv,"first"), memory.receipt_key(user,conv,"second"))
        await memory._redis.aclose()
        client.delete_collection(name)


if __name__ == '__main__':
    asyncio.run(main())
