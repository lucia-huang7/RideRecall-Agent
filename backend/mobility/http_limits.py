"""Bound request bytes before JSON/multipart parsing, including chunked bodies."""
import asyncio
from starlette.responses import JSONResponse


class RequestBodyLimit:
    def __init__(self, app, max_bytes=4*1024*1024, timeout_seconds=15):
        self.app=app
        self.max_bytes=max_bytes
        self.timeout_seconds=timeout_seconds

    async def __call__(self, scope, receive, send):
        if scope['type']!='http' or scope['method'] not in ('POST','PUT','PATCH'):
            return await self.app(scope,receive,send)
        lengths=[value for key,value in scope.get('headers',[]) if key.lower()==b'content-length']
        if len(lengths)>1 or (lengths and not lengths[0].isdigit()):
            return await self.reject(scope,receive,send,400,'Invalid content length.')
        if lengths and (len(lengths[0])>12 or int(lengths[0])>self.max_bytes):
            return await self.reject(scope,receive,send,413,'The request body is too large.')
        chunks=[];size=0
        try:
            async with asyncio.timeout(self.timeout_seconds):
                while True:
                    item=await receive()
                    if item['type']=='http.disconnect':return
                    chunk=item.get('body',b'');size+=len(chunk)
                    if size>self.max_bytes:
                        return await self.reject(scope,receive,send,413,'The request body is too large.')
                    chunks.append(chunk)
                    if not item.get('more_body',False):break
        except TimeoutError:
            return await self.reject(scope,receive,send,408,'The request body took too long to arrive.')
        if lengths and int(lengths[0])!=size:
            return await self.reject(scope,receive,send,400,'Content length does not match the request body.')
        body=b''.join(chunks);del chunks
        delivered=False
        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered=True
                return {'type':'http.request','body':body,'more_body':False}
            return await receive()
        await self.app(scope,bounded_receive,send)

    @staticmethod
    async def reject(scope,receive,send,status,detail):
        response=JSONResponse({'detail':detail},status_code=status,headers={'Connection':'close'})
        await response(scope,receive,send)
