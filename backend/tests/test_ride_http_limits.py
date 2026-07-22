"""Transport-level limits must apply before framework deserialization."""
import asyncio
import json
import pytest
from fastapi.testclient import TestClient
from mobility.app import create_app
from mobility.http_limits import RequestBodyLimit


async def drive(headers,chunks,delay=0):
    output=[];invoked=[];position=0
    async def downstream(scope,receive,send):
        invoked.append((await receive())['body'])
        await send({'type':'http.response.start','status':204,'headers':[]})
        await send({'type':'http.response.body','body':b''})
    async def receive():
        nonlocal position
        if delay:await asyncio.sleep(delay)
        chunk=chunks[position];position+=1
        return {'type':'http.request','body':chunk,'more_body':position<len(chunks)}
    async def send(value):output.append(value)
    await RequestBodyLimit(downstream,max_bytes=8,timeout_seconds=.02)({'type':'http','method':'POST','headers':headers},receive,send)
    return output,invoked


@pytest.mark.asyncio
@pytest.mark.parametrize('headers,chunks,expected',[
    ([(b'content-length',b'9')],[b'not read'],413),
    ([],[b'12345',b'6789'],413),
    ([(b'content-length',b'1')],[b'123456789'],413),
    ([(b'content-length',b'-1')],[b''],400),
    ([(b'content-length',b'9'*5000)],[b''],413),
    ([(b'content-length',b'1')],[b'12'],400),
    ([(b'content-length',b'1'),(b'content-length',b'1')],[b'1'],400),
])
async def test_transport_rejects_oversized_or_ambiguous_lengths(headers,chunks,expected):
    output,invoked=await drive(headers,chunks)
    assert output[0]['status']==expected and invoked==[]


@pytest.mark.asyncio
async def test_valid_chunked_body_is_preserved_and_slow_body_times_out():
    output,invoked=await drive([],[b'12',b'345678'])
    assert output[0]['status']==204 and invoked==[b'12345678']
    output,invoked=await drive([],[b'x'],delay=.04)
    assert output[0]['status']==408 and invoked==[]


def test_real_api_rejects_large_json_before_parsing_and_does_not_reflect_input(tmp_path):
    with TestClient(create_app(tmp_path/'app.sqlite3')) as client:
        response=client.post('/api/cases',content=b'x'*(4*1024*1024+1),headers={'X-Demo-Session':'a','Content-Type':'application/json'})
        assert response.status_code==413
        response=client.post('/api/cases',json={'trip_id':'trip-river','description':'A valid request','secret':'do-not-reflect-me'},headers={'X-Demo-Session':'a','X-Request-ID':'strict'})
        assert response.status_code==422
        assert 'do-not-reflect-me' not in response.text
        assert 'cache-control' in response.headers
