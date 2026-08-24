"""End-to-end checks for original monitor, real MCP subprocess and management APIs."""
from collections import Counter

from fastapi.testclient import TestClient
from monitor.performance_monitor import PerformanceMonitor
from tooling.mcp_client import StandardMCPClient
from mobility.app import create_app
from mobility.providers import LocalMessages
from tests.test_ride_engine import headers, create


def test_real_mcp_calls_feed_original_monitor_and_metrics(tmp_path, monkeypatch):
    calls = Counter()
    for cls, name in [(StandardMCPClient,"_call_remote"),(PerformanceMonitor,"_collect")]:
        original = getattr(cls,name)
        def wrap(original,name):
            async def spy(*args,**kwargs):
                calls[name]+=1
                return await original(*args,**kwargs)
            return spy
        monkeypatch.setattr(cls,name,wrap(original,name))
    with TestClient(create_app(tmp_path/'operations.sqlite')) as client:
        case=create(client)
        run=case['engine_runs'][-1]
        remote=[t for t in run['tools'] if t.get('source')=='external_mcp']
        assert remote and all(t['result_success'] for t in remote)
        result=client.get('/api/agent/operations',headers=headers(agent=True)).json()
        assert result['active']
        assert result['mcp']['servers'][0]['state']=='connected'
        assert result['agent_stats']['general_0']['total']>=1
        assert result['agent_stats']['billing_0']['total']==0
        assert result['tool_stats']['mcp_rides_read_trip_record']['total']==len(remote)
        assert calls['_call_remote']==len(remote) and calls['_collect']
        metrics=client.get('/api/agent/metrics',headers=headers(agent=True))
        assert metrics.status_code==200 and 'requests_total 1.0' in metrics.text
        assert 'agent_latency_ms_count' in metrics.text
        assert client.get('/api/agent/operations').status_code==403


def test_transport_disconnect_and_real_reconnect(tmp_path):
    with TestClient(create_app(tmp_path/'transport.sqlite')) as client:
        ops=client.app.state.service.engine.operations
        client.portal.call(ops.bridge.close)
        case=create(client)
        assert case['engine_runs'][-1]['status']=='partial'
        assert case['engine_runs'][-1]['mcp_status']=='unavailable'
        assert case['proposal'] is None
        r=client.post('/api/agent/mcp/probe',headers=headers(agent=True),json={'trip_id':'trip-river'})
        assert r.status_code==503
        r=client.post('/api/agent/mcp/reconnect',headers=headers(agent=True),json={})
        assert r.status_code==200 and r.json()['servers'][0]['state']=='connected'
        r=client.post('/api/agent/mcp/probe',headers=headers(agent=True),json={'trip_id':'trip-airport'})
        assert r.status_code==200
        assert r.json()['data']['structured']['trip_id']=='trip-airport'
        assert r.json()['source']=='external_mcp'
        assert client.post('/api/agent/mcp/probe',headers=headers(agent=True),json={'trip_id':'not-a-trip'}).status_code==422


def test_monitor_detects_actual_tool_failures_and_updates_routing(tmp_path):
    with TestClient(create_app(tmp_path/'alerts.sqlite')) as client:
        engine=client.app.state.service.engine
        async def fail_calls():
            for _ in range(3):
                result=await engine.rag_tools.call('mcp_rides_read_trip_record',{'trip_id':'not-a-trip'})
                assert not result.success
        client.portal.call(fail_calls)
        report=client.get('/api/agent/operations',headers=headers(agent=True)).json()
        assert report['tool_stats']['mcp_rides_read_trip_record']['consecutive_fails']==3
        assert report['active_alerts'] and report['suggestions']
        assert all(not any('\u4e00'<=ch<='\u9fff' for ch in item['message']) for item in report['active_alerts'])
        assert engine.operations.monitor._active
        from agents.agent_orchestrator import AgentType
        stats=engine.monitor_pool[AgentType.BILLING][0].stats
        for i in range(5):
            stats.record(False, 4000, request_id=f"failure-{i}")
        report=client.get('/api/agent/operations',headers=headers(agent=True)).json()
        assert report['agent_stats']['billing_0']['monitor_penalty'] > 0
        assert stats.monitor_penalty > 0


def test_skill_hot_reload_changes_actual_agent_prompt_and_persists(tmp_path,monkeypatch):
    captured=[]
    original=LocalMessages.create
    async def capture(self,**kwargs):
        captured.append(kwargs.get('system',''))
        return await original(self,**kwargs)
    monkeypatch.setattr(LocalMessages,'create',capture)
    path=tmp_path/'skills.sqlite'
    with TestClient(create_app(path)) as client:
        current=client.get('/api/agent/skills',headers=headers(agent=True)).json()
        content=current['editable_content']+'\n- OPS-SKILL-42: Preserve conflicting evidence explicitly.\n'
        result=client.post('/api/agent/skills',headers=headers('skill-save',agent=True),json={'content':content})
        assert result.status_code==200
        assert client.post('/api/agent/skills',headers=headers('skill-save',agent=True),json={'content':content}).json()==result.json()
        create(client)
        assert any('OPS-SKILL-42' in system and 'RIDE_AGENT:' in system for system in captured)
        assert client.post('/api/agent/skills/reload',headers=headers(agent=True),json={}).status_code==200
    with TestClient(create_app(path)) as client:
        assert 'OPS-SKILL-42' in client.get('/api/agent/skills',headers=headers(agent=True)).json()['editable_content']


def test_policy_management_edits_exact_source_and_retrieval(tmp_path):
    with TestClient(create_app(tmp_path/'policy.sqlite')) as client:
        result=client.get('/api/agent/knowledge/fare',headers=headers(agent=True)).json()
        doc=next(d for d in result['source_documents'] if d['document_id']=='demo-fare-v1')
        doc.update(topic='fare',content='OPS-POLICY-42 fare difference review: preserve the estimate and settled charge. Fictional evidence only.')
        result=client.post('/api/agent/knowledge',headers=headers(agent=True),json=doc)
        assert result.status_code==200
        docs=client.get('/api/agent/knowledge/fare',headers=headers(agent=True)).json()
        assert next(d for d in docs['source_documents'] if d['document_id']=='demo-fare-v1')['content']==doc['content']
        case=create(client,text='Explain the OPS-POLICY-42 fare review policy.',trip='trip-airport',topic='fare')
        assert 'OPS-POLICY-42' in case['engine_runs'][-1]['response']
        assert case['adjustment'] is None


def test_original_evaluator_judge_and_jobs_execute_and_restore_baseline(tmp_path,monkeypatch):
    import asyncio
    from evaluation.evaluator import EndToEndEvaluator, LLMJudge
    from evaluation.jobs import EvaluationJobs
    called=Counter()
    for cls,name in [(EndToEndEvaluator,'_evaluate_dialog_case'),(LLMJudge,'judge'),(EvaluationJobs,'_execute')]:
        original=getattr(cls,name)
        def wrap(original,name):
            async def spy(*args,**kwargs):
                called[name]+=1
                return await original(*args,**kwargs)
            return spy
        monkeypatch.setattr(cls,name,wrap(original,name))
    path=tmp_path/'quality.sqlite'
    with TestClient(create_app(path)) as client:
        job=client.post('/api/agent/quality/jobs',headers=headers(agent=True),json={}).json()
        async def wait():
            await asyncio.gather(*client.app.state.service.engine.operations.quality.jobs.tasks)
        client.portal.call(wait)
        job=client.get('/api/agent/quality/jobs/'+job['job_id'],headers=headers(agent=True)).json()
        assert job['status']=='completed'
        assert job['result']['passed']==job['result']['total']==5
        assert not job['result']['baseline_compared']
        assert all(called.values()) and len(called)==3
        assert len(client.get('/api/agent/quality/history',headers=headers(agent=True)).json())==1
    with TestClient(create_app(path)) as client:
        quality=client.app.state.service.engine.operations.quality
        report=client.portal.call(quality.run,lambda *args:None)
        assert report['baseline_compared']
        assert len(quality.history())==2
        assert client.get('/api/agent/quality/jobs/unknown',headers=headers(agent=True)).status_code==404


def test_quality_regression_does_not_replace_good_baseline(tmp_path,monkeypatch):
    from mobility.providers import LocalMessages
    with TestClient(create_app(tmp_path/'regression.sqlite')) as client:
        quality=client.app.state.service.engine.operations.quality
        good=client.portal.call(quality.run,lambda *args:None)
        baseline=quality.evaluator._baseline_path.read_bytes()
        monkeypatch.setattr(LocalMessages,'answer',staticmethod(lambda role,data:'There is no useful answer.'))
        bad=client.portal.call(quality.run,lambda *args:None)
        assert bad['passed']<bad['total'] and bad['regressions']
        assert quality.evaluator._baseline_path.read_bytes()==baseline
        assert all(not any('\u4e00'<=ch<='\u9fff' for ch in line) for line in bad['regressions'])


def test_invalid_management_updates_keep_active_skill_and_policy(tmp_path):
    with TestClient(create_app(tmp_path/'validation.sqlite')) as client:
        before=client.get('/api/agent/skills',headers=headers(agent=True)).json()['editable_content']
        result=client.post('/api/agent/skills',headers=headers(agent=True),json={'content':'---\nname: Broken\nagents: [billing\n---\n# A malformed runtime rule'})
        assert result.status_code==422
        assert client.get('/api/agent/skills',headers=headers(agent=True)).json()['editable_content']==before
        assert client.post('/api/agent/knowledge',headers=headers(agent=True),json={'topic':'fare','document_id':'new','title':'Test','content':'\u4e2d\u6587'}).status_code==422


def test_invalid_request_ids_cannot_modify_management_data(tmp_path):
    with TestClient(create_app(tmp_path/'ids.sqlite')) as client:
        before=client.get('/api/agent/skills',headers=headers(agent=True)).json()['editable_content']
        assert client.post('/api/agent/skills',headers=headers('x'*101,agent=True),json={'content':before+'\n- Keep all evidence attached.\n'}).status_code==400
        assert client.get('/api/agent/skills',headers=headers(agent=True)).json()['editable_content']==before
        before_docs=client.get('/api/agent/knowledge/fare',headers=headers(agent=True)).json()
        assert client.post('/api/agent/knowledge',headers=headers('x'*101,agent=True),json={'topic':'fare','document_id':'invalid-request','title':'Invalid request','content':'Must not be indexed.'}).status_code==400
        assert client.get('/api/agent/knowledge/fare',headers=headers(agent=True)).json()==before_docs


def test_original_mcp_automatically_reopens_a_broken_transport(tmp_path):
    import asyncio
    with TestClient(create_app(tmp_path/'automatic-reconnect.sqlite')) as client:
        bridge=client.app.state.service.engine.operations.bridge
        connection=bridge.connections['rides']
        async def break_and_wait():
            old=connection.session
            connection.reconnect.set()
            async with asyncio.timeout(10):
                while connection.session is old or connection.session is None or connection.state!='connected':
                    await asyncio.sleep(.02)
            assert connection.session is not old
        client.portal.call(break_and_wait)
        result=client.post('/api/agent/mcp/probe',headers=headers(agent=True),json={'trip_id':'trip-river'})
        assert result.status_code==200 and result.json()['success']
