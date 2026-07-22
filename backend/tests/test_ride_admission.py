"""Bounded inference admission, cancellation, deadline and responsive handoff."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from mobility.admission import AnalysisAdmission
from mobility.app import create_app


def headers(key=None):return {'X-Demo-Session':'admission-rider','X-Request-ID':key or uuid4().hex}


@pytest.mark.asyncio
async def test_admission_rejects_excess_without_queue_and_releases_cancelled_work():
    gate=AnalysisAdmission(max_active=2,max_per_owner=1)
    async with gate.hold('alice'):
        with pytest.raises(HTTPException) as error:
            async with gate.hold('alice'):pass
        assert error.value.status_code==429
        async with gate.hold('bob'):
            with pytest.raises(HTTPException) as error:
                async with gate.hold('charlie'):pass
            assert error.value.status_code==503
            async with gate.hold('alice',enabled=False):assert gate.status()['active']==2
    assert gate.status()['active']==0 and not gate.active
    started=asyncio.Event()
    async def waiting():
        async with gate.hold('alice'):started.set();await asyncio.Event().wait()
    task=asyncio.create_task(waiting());await started.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    async with gate.hold('alice'):assert gate.status()['active']==1
    assert gate.status()['active']==0


def test_deadline_commits_no_case_receipt_or_memory_and_releases_capacity(tmp_path,monkeypatch):
    with TestClient(create_app(tmp_path/'db.sqlite3')) as client:
        service=client.app.state.service
        service.admission.timeout_seconds=.02
        async def slow(*args,**kwargs):await asyncio.Event().wait()
        monkeypatch.setattr(service.engine,'run',slow)
        result=client.post('/api/cases',headers=headers('deadline'),json=dict(trip_id='trip-river',description='Please review the pickup fee.'))
        assert result.status_code==503 and result.headers['retry-after']=='2'
        assert service.admission.status()['active']==0 and service.admission.status()['timeouts']==1
        with client.app.state.store.connect() as db:
            assert db.execute('SELECT COUNT(*) FROM cases').fetchone()[0]==0
            assert db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0]==0
            assert db.execute('SELECT COUNT(*) FROM projection_outbox').fetchone()[0]==0


def test_handoff_does_not_wait_for_analysis_and_invalidates_its_stale_result(tmp_path):
    with TestClient(create_app(tmp_path/'db.sqlite3')) as client:
        service=client.app.state.service
        case=client.post('/api/cases',headers=headers(),json=dict(trip_id='trip-river',description='Please review my cancellation fee.')).json()
        path='/api/cases/'+case['id']+'/actions'
        started=asyncio.Event();release=asyncio.Event();original=service.engine.run
        async def paused(*args,**kwargs):started.set();await release.wait();return await original(*args,**kwargs)
        service.engine.run=paused
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending=pool.submit(client.post,path,headers=headers('slow-message'),json=dict(action='message',text='I was waiting beside the west entrance.'))
            client.portal.call(started.wait)
            handoff=pool.submit(client.post,path,headers=headers(),json=dict(action='handoff')).result(timeout=3)
            assert handoff.status_code==200 and handoff.json()['status']=='waiting_for_agent'
            client.portal.call(release.set)
            result=pending.result(timeout=15)
        assert result.status_code==409
        record=client.app.state.store.get(case['id'])
        assert record['handoff'] and record['status']=='waiting_for_agent'
        assert not any(m['content']=='I was waiting beside the west entrance.' for m in record['messages'])
        assert service.admission.status()['active']==0
        with client.app.state.store.connect() as db:assert db.execute("SELECT COUNT(*) FROM receipts WHERE key='slow-message'").fetchone()[0]==0


def test_saturated_inference_still_allows_handoff_and_exact_cheap_command_replay(tmp_path):
    with TestClient(create_app(tmp_path/'db.sqlite3')) as client:
        service=client.app.state.service
        case=client.post('/api/cases',headers=headers(),json=dict(trip_id='trip-river',description='Please review the pickup fee.')).json()
        path='/api/cases/'+case['id']+'/actions'
        async def saturated():
            async with service.admission.hold('admission-rider'),service.admission.hold('admission-rider'):
                return await asyncio.to_thread(client.post,path,headers=headers('handoff-once'),json=dict(action='handoff'))
        result=client.portal.call(saturated)
        assert result.status_code==200
        assert client.post(path,headers=headers('handoff-once'),json=dict(action='handoff')).json()==result.json()
