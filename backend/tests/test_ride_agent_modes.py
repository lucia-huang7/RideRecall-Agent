"""Default generalist, explicit role collaboration and durable context through HTTP."""
import hashlib
import json
from uuid import uuid4

from fastapi.testclient import TestClient
from mobility.app import create_app


def headers(key=None):
    return {'X-Demo-Session':'mode-rider','X-Request-ID':key or uuid4().hex}


def test_default_union_tools_and_optional_mode_survive_restart(tmp_path, monkeypatch):
    monkeypatch.delenv('LLM_AGENT_SYSTEM_RIDE_API_KEY', raising=False)
    path=tmp_path/'modes.sqlite3'
    payload=dict(trip_id='trip-river',topic='cancellation',description='My boarding label is CEDAR-17. Review the driver pickup and fare payment after app error 500.')
    with TestClient(create_app(path)) as c:
        response=c.post('/api/cases',headers=headers('create-original'),json=payload)
        assert response.status_code==200,response.text
        case=response.json();run=case['engine_runs'][-1]
        assert case['agent_mode']==run['agent_mode']=='single'
        assert run['memory_mode']=='durable' and run['agents']==['general']
        assert 'Your request for human review has been identified' not in run['response']
        tools={t.get('tool_name') for t in run['tools'] if t.get('success')}
        assert {'get_trip','get_charge_breakdown','get_case_context','search_knowledge_base'}.issubset(tools)
        assert any(t.get('source')=='external_mcp' for t in run['tools'])
        assert run['citations'] and run['status']=='success' and case['adjustment'] is None
        original_fingerprint=hashlib.sha256(json.dumps(['create',payload],sort_keys=True).encode()).hexdigest()
        assert c.app.state.store.replay('mode-rider','create-original',original_fingerprint) is not None
        count=c.app.state.service.engine.client.total_calls
        assert c.post('/api/cases',headers=headers('create-original'),json=payload).json()==case
        assert c.app.state.service.engine.client.total_calls==count
        def turn(text,mode=None,key=None):
            body={'action':'message','text':text}
            if mode:body['agent_mode']=mode
            result=c.post(f"/api/cases/{case['id']}/actions",headers=headers(key),json=body)
            assert result.status_code==200,result.text
            return result.json()
        changed=turn('Review the driver pickup and fare payment with my saved boarding label.','multi','switch-mode')
        assert changed['agent_mode']=='multi' and len(changed['engine_runs'][-1]['agents'])>1
        assert 'CEDAR-17' in changed['engine_runs'][-1]['response']
        count=c.app.state.service.engine.client.total_calls
        assert turn('Review the driver pickup and fare payment with my saved boarding label.','multi','switch-mode')==changed
        assert c.app.state.service.engine.client.total_calls==count
        conflict=c.post(f"/api/cases/{case['id']}/actions",headers=headers('switch-mode'),json={'action':'message','text':'Review the driver pickup and fare payment with my saved boarding label.','agent_mode':'single'})
        assert conflict.status_code==409
        for i in range(8):changed=turn(f'Keep the evidence review open while I check the receipt, update {i}.')
        assert changed['agent_mode']=='multi'
        changed=turn('Retain my boarding label while checking the fare payment and pickup.','single')
        assert changed['agent_mode']=='single' and changed['engine_runs'][-1]['agents']==['general']
        assert 'CEDAR-17' in changed['engine_runs'][-1]['response']
        assert changed['engine_runs'][0]['agent_mode']=='single'
        invalid=c.post(f"/api/cases/{case['id']}/actions",headers=headers(),json={'action':'confirm','agent_mode':'multi'})
        assert invalid.status_code==422
    with TestClient(create_app(path)) as reopened:
        saved=reopened.get(f"/api/cases/{case['id']}",headers=headers()).json()
        assert saved['agent_mode']=='single'
        handoff=reopened.post(f"/api/cases/{case['id']}/actions",headers=headers(),json={'action':'message','text':'Please connect me to a human representative without repeating my boarding label.'})
        assert handoff.status_code==200
        assert handoff.json()['status']=='waiting_for_agent'
        assert 'CEDAR-17' in handoff.json()['engine_runs'][-1]['response']


def test_invalid_mode_and_optional_multi_create(tmp_path):
    with TestClient(create_app(tmp_path/'validation.sqlite3')) as c:
        body=dict(trip_id='trip-airport',topic='fare',description='Please review my fare payment and pickup.')
        assert c.post('/api/cases',headers=headers(),json=dict(body,agent_mode='unknown')).status_code==422
        result=c.post('/api/cases',headers=headers(),json=dict(body,agent_mode='multi'))
        assert result.status_code==200
        assert result.json()['agent_mode']=='multi'
        assert len(result.json()['engine_runs'][-1]['agents'])>1
