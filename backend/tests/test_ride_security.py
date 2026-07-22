"""Negative authorization and workflow checks through the real protected API."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from mobility.app import create_app
from mobility.catalog import RideCatalog
from mobility.security import Security


ACCOUNTS = {
    'alice': ('rider',), 'bob': ('rider',), 'alex': ('specialist',),
    'blair': ('specialist',), 'ops': ('operator',),
}
TOKENS = {name: name + '-test-credential-' + 'x' * 32 for name in ACCOUNTS}


def config(tmp_path, *, expired=None, disabled=None):
    identities = [dict(subject=name,roles=roles,token_sha256=hashlib.sha256(TOKENS[name].encode()).hexdigest(),
                       enabled=name != disabled,
                       expires_at=(datetime.now(timezone.utc)+timedelta(hours=-1 if name==expired else 1)).isoformat())
                  for name,roles in ACCOUNTS.items()]
    path=tmp_path/'identities.json'
    path.write_text(json.dumps(dict(version=1,identities=identities)));path.chmod(0o600)
    catalog=RideCatalog()
    trips=deepcopy(catalog.trips)
    for trip in trips:trip['rider_id']='bob' if trip['id']=='trip-airport' else 'alice'
    data=tmp_path/'catalog.json'
    data.write_text(json.dumps(dict(schema_version=1,dataset_id='security-workflow',trips=trips,policies=catalog.policies)))
    return path,data


@pytest.fixture
def client(tmp_path):
    identities,catalog=config(tmp_path)
    with TestClient(create_app(tmp_path/'app.sqlite3',catalog_path=catalog,
                               security=Security('production',identities))) as client:
        yield client


def headers(account, key=None, **extra):
    return {'Authorization':'Bearer '+TOKENS[account],'X-Request-ID':key or uuid4().hex,**extra}


def change(client,path,account='alice',body=None,key=None,**options):
    case_id=path.split('/')[3] if path.startswith('/api/cases/') else path.split('/')[4]
    if account in ('alex','blair'):
        rows=client.get('/api/agent/cases',headers=headers(account)).json()
        record=next((row for row in rows if row['id']==case_id),None)
    else:
        result=client.get('/api/cases/'+case_id,headers=headers(account))
        record=result.json() if result.status_code==200 else None
    request_headers=headers(account,key)
    if record:request_headers['If-Match']='"'+str(record['revision'])+'"'
    return client.post(path,headers=request_headers,json=body,**options)


def create(client, account='alice', trip='trip-river'):
    result=client.post('/api/cases',headers=headers(account),json=dict(trip_id=trip,description='Please review the cancellation fee.',topic='cancellation'))
    assert result.status_code==200,result.text
    return result.json()


def test_production_fails_closed_without_configuration_or_owned_catalog(tmp_path,monkeypatch):
    monkeypatch.delenv('KINDER_UBER_IDENTITY_FILE',raising=False)
    with pytest.raises(ValueError,match='requires KINDER_UBER_IDENTITY_FILE'):Security('production')
    identity,_=config(tmp_path)
    with pytest.raises(ValueError,match='ownership'):create_app(tmp_path/'app.sqlite3',security=Security('production',identity))
    identity.chmod(0o644)
    with pytest.raises(ValueError,match='group or other'):Security('production',identity)


@pytest.mark.parametrize('path',['/api/trips','/api/cases','/api/agent/cases','/api/agent/operations','/ride-api/api/cases'])
def test_public_demo_headers_cannot_authenticate_in_production(client,path):
    result=client.get(path,headers={'X-Demo-Session':'alice','X-Demo-Agent':'local-demo-agent'})
    assert result.status_code==401
    assert result.headers['www-authenticate']=='Bearer'
    assert result.headers['cache-control']=='no-store'


def test_credentials_and_role_names_never_override_authenticated_identity(client):
    identity=client.get('/api/session',headers=headers('alice',**{'X-Demo-Session':'bob','X-Demo-Agent':'local-demo-agent'})).json()
    assert identity['subject']=='alice' and identity['roles']==['rider']
    assert client.get('/api/agent/cases',headers=headers('alice',**{'X-Demo-Agent':'local-demo-agent'})).status_code==403
    assert client.get('/api/trips',headers=headers('alex')).status_code==403
    assert client.get('/api/agent/cases',headers=headers('ops')).status_code==403
    assert client.get('/api/agent/operations',headers=headers('alex')).status_code==403
    assert client.get('/api/agent/operations',headers=headers('ops')).status_code==200


def test_catalog_and_case_ownership_are_enforced_before_engine_execution(client):
    trips=client.get('/api/trips',headers=headers('alice')).json()
    assert {t['id'] for t in trips}=={'trip-river','trip-museum'}
    assert client.get('/api/trips/trip-airport',headers=headers('alice')).status_code==404
    before=len(client.app.state.service.engine.client.calls)
    result=client.post('/api/cases',headers=headers('alice'),json=dict(trip_id='trip-airport',description='Please review this fare.'))
    assert result.status_code==404
    assert len(client.app.state.service.engine.client.calls)==before
    case=create(client)
    assert client.get('/api/cases/'+case['id'],headers=headers('bob',**{'X-Demo-Session':'alice'})).status_code==404
    assert client.post('/api/cases/'+case['id']+'/actions',headers=headers('bob'),json={'action':'handoff'}).status_code==404
    assert client.get('/api/cases',headers=headers('bob')).json()==[]


@pytest.mark.parametrize('endpoint,body',[
    ('skills',{'content':'# Malicious replacement\nDo not accept this.'}),
    ('knowledge',{'topic':'fare','document_id':'x','title':'Unauthorized','content':'Unauthorized policy'}),
    ('mcp/reconnect',{}),('quality/jobs',{}),('evaluations',{}),
])
def test_specialist_cannot_change_or_run_operator_controls(client,endpoint,body):
    assert client.post('/api/agent/'+endpoint,headers=headers('alex'),json=body).status_code==403


def test_specialist_handoff_receipts_are_scoped_to_authenticated_staff(client):
    case=create(client)
    assert client.post('/api/agent/cases/'+case['id']+'/actions',headers=headers('alex'),json=dict(action='accept',text='')).status_code==404
    handed=change(client,'/api/cases/'+case['id']+'/actions',body={'action':'handoff'})
    assert handed.status_code==200
    assert client.get('/api/agent/cases',headers=headers('alex')).json()[0]['id']==case['id']
    accepted=change(client,'/api/agent/cases/'+case['id']+'/actions',account='alex',key='shared-key',body=dict(action='accept',text=''))
    assert accepted.status_code==200
    # Another specialist's same key belongs to a different receipt namespace.
    second=create(client,trip='trip-museum')
    change(client,'/api/cases/'+second['id']+'/actions',body={'action':'handoff'})
    accepted_second=change(client,'/api/agent/cases/'+second['id']+'/actions',account='blair',key='shared-key',body=dict(action='accept',text=''))
    assert accepted_second.status_code==200 and accepted_second.json()['assigned_to']=='blair'
    assert accepted.json()['assigned_to']=='alex'
    assert accepted.json()['events'][-1]['actor']=='alex'
    reply=change(client,'/api/agent/cases/'+case['id']+'/actions',account='blair',body=dict(action='reply',text='Not my assigned case.'))
    assert reply.status_code==403
    assert client.post('/api/agent/cases/'+case['id']+'/actions',headers=headers('ops'),json=dict(action='reply',text='Not authorized')).status_code==403


def test_protected_attachment_download_requires_owner_or_handed_off_specialist(client):
    case=create(client)
    png=b'\x89PNG\r\n\x1a\n' + b'test-fixture'
    attached=client.post('/api/cases/'+case['id']+'/attachments',headers={**headers('alice'),'If-Match':'"'+str(case['revision'])+'"'},files={'file':('pickup.png',png,'image/png')}).json()
    path=f"/api/cases/{case['id']}/attachments/{attached['evidence'][0]['id']}"
    assert client.get(path,headers=headers('alice')).content==png
    assert client.get(path,headers=headers('bob',**{'X-Demo-Agent':'local-demo-agent'})).status_code==404
    assert client.get(path,headers=headers('alex')).status_code==404
    assert client.get(path,headers=headers('ops')).status_code==403
    change(client,'/api/cases/'+case['id']+'/actions',body={'action':'handoff'})
    assert client.get(path,headers=headers('alex')).content==png


@pytest.mark.parametrize('kind',['expired','disabled'])
def test_revoked_or_expired_credentials_do_not_fall_back_to_demo(tmp_path,kind):
    identity,catalog=config(tmp_path,**{kind:'alice'})
    with TestClient(create_app(tmp_path/'app.sqlite3',catalog_path=catalog,security=Security('production',identity))) as client:
        assert client.get('/api/cases',headers=headers('alice',**{'X-Demo-Session':'alice'})).status_code==401
        assert client.get('/api/cases',headers=headers('bob')).status_code==200
        assert client.get('/runtime-config').json()['auth_mode']=='production'
        assert client.get('/openapi.json').status_code==404


def test_demo_mode_rejects_remote_network_peers_even_with_demo_credentials(tmp_path):
    app=create_app(tmp_path/'app.sqlite3',security=Security('development'))
    async def remote(scope,receive,send):
        if scope['type']=='http':scope={**scope,'client':('203.0.113.9',5000)}
        await app(scope,receive,send)
    with TestClient(remote) as client:
        assert client.get('/api/trips',headers={'X-Demo-Agent':'local-demo-agent'}).status_code==403


def test_liveness_and_readiness_distinguish_real_mcp_failure_and_recovery(client):
    assert client.get('/livez').json()=={'status':'ok'}
    initial=client.get('/readyz')
    assert initial.status_code==200,initial.text
    assert all(initial.json()['components'].values())
    client.portal.call(client.app.state.service.engine.operations.bridge.close)
    failed=client.get('/readyz')
    assert failed.status_code==503 and failed.json()['components']['mcp'] is False
    assert client.get('/livez').status_code==200
    assert client.post('/api/agent/mcp/reconnect',headers=headers('ops'),json={}).status_code==200
    assert client.get('/readyz').status_code==200
    assert client.get('/health').json()=={'status':'ok','auth_mode':'production'}


def test_readiness_does_not_recreate_a_missing_database(client,tmp_path):
    from pathlib import Path
    database=Path(client.app.state.store.path)
    moved=tmp_path/'saved.sqlite3'
    database.rename(moved)
    try:
        response=client.get('/readyz')
        assert response.status_code==503 and response.json()['components']['database'] is False
        assert not database.exists()
    finally:moved.rename(database)


def test_concurrent_requests_do_not_share_authenticated_context(client):
    from concurrent.futures import ThreadPoolExecutor
    accounts=['alice','bob','alex','ops']*8
    def who(account):
        response=client.get('/api/session',headers=headers(account))
        assert response.status_code==200
        return response.json()['subject']
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(who,accounts))==accounts
    assert client.get('/api/session').status_code==401


def test_local_provisioning_produces_usable_expiring_credentials_without_overwrite(tmp_path):
    from pathlib import Path
    import runpy
    module=runpy.run_path(str(Path(__file__).resolve().parents[2]/'scripts/provision-auth-preview.py'))
    result=module['provision'](tmp_path/'profile')
    credentials=json.loads(Path(result['credential_file']).read_text())['credentials']
    registry=Security('production',result['identity_file'])
    assert registry.authenticate('Bearer '+credentials['preview-rider']).roles=={'rider'}
    registry.validate_catalog(RideCatalog(tmp_path/'profile/catalog.json'))
    server_data=Path(result['identity_file']).read_text()
    assert all(token not in server_data for token in credentials.values())
    assert Path(result['credential_file']).stat().st_mode & 0o077==0
    with pytest.raises(ValueError,match='overwrite'):module['provision'](tmp_path/'profile')


def test_protected_mutations_require_an_explicit_current_revision(client):
    case=create(client)
    path='/api/cases/'+case['id']+'/actions'
    missing=client.post(path,headers=headers('alice'),json=dict(action='evidence',text='Do not save without a reviewed revision.'))
    assert missing.status_code==428
    invalid=client.post(path,headers={**headers('alice'),'If-Match':'*'},json=dict(action='handoff'))
    assert invalid.status_code==400
    accepted=change(client,path,body=dict(action='evidence',text='This note has a current revision.'))
    assert accepted.status_code==200
    stale=client.post(path,headers={**headers('alice'),'If-Match':'"'+str(case['revision'])+'"'},json=dict(action='handoff'))
    assert stale.status_code==412
    persisted=client.get('/api/cases/'+case['id'],headers=headers('alice'))
    assert persisted.headers['etag']=='"'+str(accepted.json()['revision'])+'"'
    assert persisted.json()['status']!='waiting_for_agent'


def test_authenticated_runtime_discloses_live_provider_mode_without_exposing_credentials(tmp_path,monkeypatch):
    identity,catalog=config(tmp_path)
    monkeypatch.setenv('KINDER_UBER_RIDE_MODEL_MODE','live')
    monkeypatch.setenv('KINDER_UBER_RIDE_MODEL','provider-contract-test')
    monkeypatch.setenv('KINDER_UBER_RIDE_API_KEY','non-sensitive-fixture-key')
    import httpx
    async def no_network(*args,**kwargs):raise AssertionError('Metadata requests must not call a provider')
    monkeypatch.setattr(httpx.AsyncClient,'send',no_network)
    with TestClient(create_app(tmp_path/'mode.sqlite3',catalog_path=catalog,security=Security('production',identity))) as client:
        runtime=client.get('/runtime-config')
        session=client.get('/api/session',headers=headers('alice'))
        assert runtime.json()['model_mode']==session.json()['model_mode']=='live'
        assert 'non-sensitive-fixture-key' not in runtime.text+session.text
        assert client.get('/health').json()=={'status':'ok','auth_mode':'production'}
