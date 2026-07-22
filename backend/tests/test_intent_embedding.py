import asyncio
import time
import httpx
import pytest
from core.intent_embedding import IntentEmbedding
from core.intent_recognizer import IntentRecognizer


def provider(monkeypatch):
    monkeypatch.setenv('SILICONFLOW_API_KEY','test-only-not-a-real-key')
    monkeypatch.setenv('INTENT_EMBEDDING_PROVIDER','auto')
    return IntentEmbedding()


def local(text):
    return [1.0, 0.0]


def test_online_batches_caches_templates_and_preserves_space(monkeypatch):
    async def run():
        client=provider(monkeypatch)
        calls=[]
        async def request(http, texts):
            calls.append(texts)
            return [[1.0,0.0,0.0] for _ in texts]
        client._request=request
        templates={'a':[str(i) for i in range(40)]}
        groups, vector, source=await client.vectors('first',templates,local)
        assert [len(x) for x in calls]==[32,8,1]
        assert source=='siliconflow/BAAI/bge-m3' and len(vector)==3
        await client.vectors('second',templates,local)
        assert len(calls)==4 and calls[-1]==['second']
        assert all(len(v)==3 for v in groups['a'])
    asyncio.run(run())


def test_query_failure_falls_back_with_local_templates_and_cooldown(monkeypatch):
    async def run():
        client=provider(monkeypatch)
        calls=[]
        async def request(http,texts):
            calls.append(texts)
            if texts==['question']:
                raise httpx.HTTPStatusError('rate limit',request=httpx.Request('POST',client.URL),response=httpx.Response(429))
            return [[1.,0.,0.] for _ in texts]
        client._request=request
        groups,vector,source=await client.vectors('question',{'a':['template']},local)
        assert source=='local-ngram' and len(vector)==len(groups['a'][0])==2
        assert client.last_error=='http_429'
        await client.vectors('another',{'a':['template']},local)
        assert len(calls)==2
        client.retry_at=time.monotonic()-1
        _,vector,source=await client.vectors('another',{'a':['template']},local)
        assert source=='siliconflow/BAAI/bge-m3' and len(vector)==3
    asyncio.run(run())


def test_missing_key_never_calls_online(monkeypatch):
    monkeypatch.delenv('SILICONFLOW_API_KEY',raising=False)
    monkeypatch.setenv('INTENT_EMBEDDING_PROVIDER','auto')
    client=IntentEmbedding()
    groups,vector,source=asyncio.run(client.vectors('q',{'a':['t']},local))
    assert source=='local-ngram' and not client.enabled


@pytest.mark.parametrize('data', [[], [{'index':0,'embedding':[0,0]}], [{'index':True,'embedding':[1,0]}], [{'index':0,'embedding':['1',0]}], [{'index':0,'embedding':[float('nan'),0]}]])
def test_bad_vectors_rejected(monkeypatch,data):
    async def run():
        client=provider(monkeypatch)
        class Response:
            def raise_for_status(self): pass
            def json(self): return {'data':data}
        class HTTP:
            async def post(self,*args,**kwargs): return Response()
        with pytest.raises(ValueError): await client._request(HTTP(),['q'])
    asyncio.run(run())


def test_batch_response_reorders_and_uses_only_free_model(monkeypatch):
    async def run():
        client=provider(monkeypatch)
        def handle(request):
            import json
            payload=json.loads(request.content)
            assert payload['model']=='BAAI/bge-m3'
            return httpx.Response(200,json={'data':[{'index':1,'embedding':[0,2]},{'index':0,'embedding':[3,0]}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            assert await client._request(http,['a','b'])==[[1,0],[0,1]]
    asyncio.run(run())


def test_template_initialization_is_single_flight(monkeypatch):
    async def run():
        client=provider(monkeypatch)
        templates=0
        async def request(http,texts):
            nonlocal templates
            if texts==['template']:
                templates+=1
                await asyncio.sleep(.01)
            return [[1.,0.] for _ in texts]
        client._request=request
        await asyncio.gather(*(client.vectors(str(i),{'a':['template']},local) for i in range(5)))
        assert templates==1
    asyncio.run(run())


def test_intent_response_exposes_embedding_source(monkeypatch):
    from core.intent_recognizer import IntentCategory
    from unittest.mock import AsyncMock
    async def run():
        monkeypatch.delenv('SILICONFLOW_API_KEY',raising=False)
        rec=IntentRecognizer('test')
        rec._llm_recognize=AsyncMock(return_value={'intent':IntentCategory.REFUND,'confidence':.95})
        result=await rec.recognize('我要退款')
        assert result.embedding_source=='local-ngram'
        assert rec._cache_key('a'*200+'b') != rec._cache_key('a'*200+'c')
        await rec.client.close()
    asyncio.run(run())
