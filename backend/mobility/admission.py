"""Bound expensive API work without an unbounded application waiting queue."""
import asyncio
from contextlib import asynccontextmanager

from fastapi import HTTPException


class AnalysisAdmission:
    def __init__(self,max_active=4,max_per_owner=2,timeout_seconds=35):
        if type(max_active) is not int or type(max_per_owner) is not int or not 1<=max_per_owner<=max_active<=32:
            raise ValueError('Invalid analysis admission capacity')
        if not 0<timeout_seconds<=120:raise ValueError('Invalid analysis deadline')
        self.capacity=max_active
        self.per_owner=max_per_owner
        self.timeout_seconds=timeout_seconds
        self.active={}
        self.lock=asyncio.Lock()
        self.admitted=self.rejected=self.timeouts=0

    @asynccontextmanager
    async def hold(self,owner,enabled=True):
        if not enabled:
            yield
            return
        async with self.lock:
            count=self.active.get(owner,0)
            if count>=self.per_owner:
                self.rejected+=1
                raise HTTPException(429,'Your other analyses are still running. Retry in a moment.',headers={'Retry-After':'2'})
            if sum(self.active.values())>=self.capacity:
                self.rejected+=1
                raise HTTPException(503,'The analysis workspace is busy. Retry the same operation shortly.',headers={'Retry-After':'2'})
            self.active[owner]=count+1
            self.admitted+=1
        try:yield
        finally:
            async with self.lock:
                count=self.active[owner]-1
                if count:self.active[owner]=count
                else:self.active.pop(owner)

    def status(self):
        return dict(active=sum(self.active.values()),capacity=self.capacity,per_account=self.per_owner,
                    admitted=self.admitted,rejected=self.rejected,timeouts=self.timeouts,deadline_seconds=self.timeout_seconds)
