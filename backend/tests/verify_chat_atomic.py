"""容器内：真实 Redis 回执原子写入、重复请求、身份绑定和管理接口权限。"""
import asyncio, os, uuid, json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
from redis.asyncio import Redis
from memory.conversation_memory import MemoryManager
from core.concurrency import KeyedLocks
from core.intent_recognizer import IntentCategory, UrgencyLevel
from agents.agent_orchestrator import OrchestratorResult, AgentType
import api.main as api

async def main():
    redis = Redis.from_url(os.environ['REDIS_URL'],decode_responses=True)
    user = 'regression-'+uuid.uuid4().hex
    conv = uuid.uuid4().hex
    memory = MemoryManager.__new__(MemoryManager)
    memory._redis, memory._request_locks = redis, KeyedLocks()
    memory.get_context = AsyncMock(return_value=SimpleNamespace(recent_messages=[],to_prompt_text=lambda:''))
    intent = SimpleNamespace(intent=IntentCategory.TECHNICAL_LOGIN,intent_group='technical',entities={'error_code':['401']},urgency=UrgencyLevel.MEDIUM,confidence=.9,source_scores={})
    request_id = uuid.uuid4().hex
    orch = SimpleNamespace(recognize_intent=AsyncMock(return_value=intent), run=AsyncMock(return_value=OrchestratorResult(request_id=request_id,response='ok',agent_type=AgentType.TECHNICAL,intent=intent.intent)), get_tool_trace=lambda rid: {'request_id':rid,'tool_calls':[{'input':{'api_key':'must-remove'}}]})
    api._memory, api._orchestrator, api._profile_updater, api._monitor = memory, orch, None, None
    token='regression-token-with-at-least-24-characters'
    os.environ['KINDER_UBER_API_TOKENS']=json.dumps({token:{'user_id':user,'role':'user'}})
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),base_url='http://localhost') as client:
            assert (await client.get('/monitor')).status_code == 401
            headers={'Authorization':'Bearer '+token}
            assert (await client.get('/monitor',headers=headers)).status_code == 403
            body={'message':'登录401','user_id':'spoofed','conv_id':conv,'client_request_id':'retry-me'}
            a,b=await asyncio.gather(*(client.post('/chat',json=body,headers=headers) for _ in range(2)))
            assert a.status_code == b.status_code == 200,(a.text,b.text)
            assert a.json()==b.json()
            assert orch.run.await_count == 1
            assert orch.run.call_args.args[0].user_id == user
            rows=await redis.lrange(memory._wm_key(user,conv),0,-1)
            assert [json.loads(r)['role'] for r in rows]==['assistant','user']
            assert not await redis.exists(memory._wm_key('spoofed',conv))
            trace=await memory.get_trace(request_id)
            assert trace['tool_calls'][0]['input']['api_key']=='[REDACTED]'
            body['message']='不同消息'
            assert (await client.post('/chat',json=body,headers=headers)).status_code==409
            print('PASS: atomic turn + receipt; concurrent retry runs once; authenticated identity; 401/403; redacted persistent trace')
    finally:
        await redis.delete(memory._wm_key(user,conv), memory._state_key(user,conv), memory.receipt_key(user,conv,'retry-me'),'trace:'+request_id)
        await redis.lrem('trace:recent',0,request_id)
        await redis.aclose()

asyncio.run(main())
