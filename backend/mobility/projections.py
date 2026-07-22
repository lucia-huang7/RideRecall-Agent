"""Stage vector effects until the case transaction commits, then replay durably.

SQLite owns business state and the outbox. Chroma is a derived memory index.
Repeated delivery uses upsert with stable IDs, so a crash after a vector write
but before acknowledgement does not create duplicate episodic records.
"""
import asyncio
from copy import deepcopy
import json
import time

MEMORY_COLLECTIONS = {'ride_memory_episodes_v1', 'ride_memory_profiles_v1'}


class StagedCollection:
    def __init__(self, collection, effects):
        self.collection=collection
        self.effects=effects
        self.pending={}

    def upsert(self, *, ids, documents, metadatas):
        payload=deepcopy(dict(ids=ids,documents=documents,metadatas=metadatas))
        self.effects.append(dict(collection=self.collection.name, payload=payload))
        for key,document,metadata in zip(ids,documents,metadatas,strict=True):
            self.pending[key]=(document,metadata)

    def add(self, **payload):
        self.upsert(**payload)

    def get(self, ids=None, where=None, **kwargs):
        # Core profile reads have simple exact-match filters; overlay staged values.
        existing=self.collection.get(ids=ids,where=where,**kwargs)
        records={key:(doc,meta) for key,doc,meta in zip(existing['ids'],existing.get('documents') or [],existing.get('metadatas') or [])}
        for key,(doc,meta) in self.pending.items():
            if ids is not None and key not in ids:continue
            if where and not all(meta.get(k)==v for k,v in where.items()):continue
            records[key]=(doc,meta)
        return dict(ids=list(records),documents=[v[0] for v in records.values()],metadatas=[v[1] for v in records.values()])

    def query(self, **kwargs):
        return self.collection.query(**kwargs)


class ProjectionWorker:
    def __init__(self, store, chroma, embedding_function):
        self.store=store
        self.chroma=chroma
        self.embedding_function=embedding_function
        self.lock=asyncio.Lock()
        self.wake=asyncio.Event()
        self.task=None
        self.stopping=False
        self.last_error=None

    async def start(self):
        self.stopping=False
        for name in MEMORY_COLLECTIONS:
            self.chroma.get_or_create_collection(name,embedding_function=self.embedding_function)
        await self.flush()
        self.task=asyncio.create_task(self._run(),name='memory-projection-outbox')

    async def _run(self):
        while not self.stopping:
            try:await asyncio.wait_for(self.wake.wait(),timeout=1)
            except TimeoutError:pass
            self.wake.clear()
            await self.flush()

    async def flush(self, limit=32):
        async with self.lock:
            for _ in range(limit):
                try:row=await asyncio.to_thread(self.store.next_projection)
                except Exception as error:
                    self.last_error=type(error).__name__
                    return
                if row is None:
                    self.last_error=None
                    return
                if row['available_at']>time.time():return
                try:
                    effects=json.loads(row['payload'])
                    for effect in effects:
                        if effect['collection'] not in MEMORY_COLLECTIONS:
                            raise ValueError('Unsupported memory projection collection')
                        collection=self.chroma.get_collection(effect['collection'],embedding_function=self.embedding_function)
                        await asyncio.to_thread(collection.upsert,**effect['payload'])
                    await asyncio.to_thread(self.store.finish_projection,row['sequence'])
                    self.last_error=None
                except Exception as error:
                    self.last_error=type(error).__name__
                    try:await asyncio.to_thread(self.store.retry_projection,row['sequence'],self.last_error)
                    except Exception as database_error:self.last_error=type(database_error).__name__
                    return
            self.wake.set()

    async def close(self):
        self.stopping=True
        self.wake.set()
        if self.task:
            try:await asyncio.wait_for(self.task,timeout=10)
            except TimeoutError:
                self.task.cancel()
                await asyncio.gather(self.task,return_exceptions=True)
            self.task=None
