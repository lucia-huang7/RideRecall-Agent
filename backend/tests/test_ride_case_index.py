"""Bounded summaries, signed live pagination and atomic index/source consistency."""
from contextlib import contextmanager
from copy import deepcopy
import json
import sqlite3
from uuid import uuid4
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from mobility.app import create_app
from mobility.store import Store
from test_ride_security import config, headers as account_headers
from mobility.security import Security


def record(identity,owner='index-rider',handoff=False):
    return dict(id=identity,owner=owner,trip_id='trip-river',title='Cancellation fee review',status='waiting_for_agent' if handoff else 'needs_review',
        updated_at='2026-10-22',revision=0,assigned_to=None,handoff={'open_question':'Review the pickup evidence'} if handoff else None,
        description='A long source explanation',adjustment=None,proposal=None,messages=[dict(role='rider',content='Evidence history '*1000)],events=[],evidence=[],engine_runs=[])


def seed(store,count,owner='index-rider',handoff=False):
    with store.connect() as db:
        for i in range(count):store.save(db,record(f'KU-{owner}-INDEX-{i:04}',owner,handoff))


def test_keyset_ties_complete_order_account_binding_and_restart(tmp_path,monkeypatch):
    store=Store(tmp_path/'db.sqlite3')
    monkeypatch.setattr('mobility.store.now',lambda:'2026-10-22T00:00:00Z')
    seed(store,61);seed(store,7,owner='another-rider')
    items=[];cursor=None
    while True:
        result=store.page(owner='index-rider',limit=7,cursor=cursor)
        assert result['total']==61 and result['active_total']==61
        assert all(not {'messages','events','engine_runs','description','evidence'} & set(row) for row in result['items'])
        items.extend(row['id'] for row in result['items'])
        cursor=result['next_cursor']
        if not cursor:break
    assert len(items)==len(set(items))==61 and items==sorted(items,reverse=True)
    first=store.page(owner='index-rider',limit=7)
    cursor=first['next_cursor']
    assert Store(store.path).page(owner='index-rider',limit=7,cursor=cursor)['items'][0]['id']==items[7]
    for options in (dict(owner='another-rider'),dict(owner='index-rider',handed_off=True),dict(owner='index-rider',trip_id='trip-river')):
        with pytest.raises(HTTPException) as error:store.page(cursor=cursor,**options)
        assert error.value.status_code==400
    with pytest.raises(HTTPException):store.page(owner='index-rider',cursor=cursor[:-1]+('0' if cursor[-1]!='0' else '1'))


def test_summary_queries_do_not_read_case_payloads(tmp_path,monkeypatch):
    store=Store(tmp_path/'db.sqlite3');seed(store,100)
    original=store.connect
    @contextmanager
    def index_only(**options):
        with original(**options) as db:
            def authorize(action,table,*args):return sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_READ and table=='cases' else sqlite3.SQLITE_OK
            db.set_authorizer(authorize)
            yield db
    monkeypatch.setattr(store,'connect',index_only)
    result=store.page(owner='index-rider',limit=10)
    assert len(result['items'])==10 and result['total']==100
    with pytest.raises(sqlite3.DatabaseError):store.page(owner='index-rider',limit=10,full=True)


def test_summary_update_is_atomic_with_case_and_receipt_and_deletion_cascades(tmp_path):
    store=Store(tmp_path/'db.sqlite3');seed(store,1)
    before=store.get('KU-index-rider-INDEX-0000');summary=store.page(owner='index-rider')
    def failed(db):
        case=deepcopy(before);case['status']='resolved';store.save(db,case)
        raise sqlite3.OperationalError('Injected transaction failure')
    with pytest.raises(sqlite3.OperationalError):store.write('index-rider','failed','fingerprint',failed)
    assert store.get(before['id'])==before and store.page(owner='index-rider')==summary
    with store.connect() as db:
        case=store.load(db,before['id']);case['status']='resolved';store.save(db,case)
    assert store.page(owner='index-rider')['active_total']==0
    with store.connect() as db:db.execute('DELETE FROM cases WHERE id=?',(before['id'],))
    assert store.page(owner='index-rider')['total']==0


def test_legacy_migration_backfills_without_changing_source_or_receipts(tmp_path):
    path=tmp_path/'legacy.sqlite3';store=Store(path);seed(store,3)
    with store.connect() as db:
        source=db.execute('SELECT * FROM cases ORDER BY id').fetchall()
        db.execute('DROP TABLE case_index');db.execute("DELETE FROM runtime_settings WHERE key='case_cursor_secret'");db.execute('PRAGMA user_version=3')
    migrated=Store(path)
    assert migrated.page(owner='index-rider')['total']==3
    with migrated.connect() as db:assert db.execute('SELECT * FROM cases ORDER BY id').fetchall()==source


def test_http_pages_are_bounded_and_specialist_detail_requires_handoff(tmp_path):
    registry,catalog=config(tmp_path)
    with TestClient(create_app(tmp_path/'db.sqlite3',catalog_path=catalog,security=Security('production',registry))) as client:
        store=client.app.state.store
        seed(store,61,owner='alice',handoff=True);seed(store,3,owner='bob')
        result=client.get('/api/cases/index?limit=10',headers=account_headers('alice'))
        assert result.status_code==200 and result.json()['total']==61
        assert len(result.json()['items'])==10
        assert client.get('/api/cases/index?limit=101',headers=account_headers('alice')).status_code==422
        full=client.get('/api/cases?limit=10',headers=account_headers('alice'))
        assert len(full.json())==10 and full.headers['x-total-count']=='61' and 'limit=10' in full.headers['link']
        cursor=result.json()['next_cursor']
        assert client.get('/api/cases/index?cursor='+cursor,headers=account_headers('bob')).status_code==400
        assert client.get('/api/agent/cases/index',headers=account_headers('alice')).status_code==403
        assert client.get('/api/agent/cases/index',headers=account_headers('ops')).status_code==403
        queue=client.get('/api/agent/cases/index',headers=account_headers('alex')).json()
        assert queue['total']==61 and len(queue['items'])==25
        assert client.get('/api/agent/cases/KU-alice-INDEX-0000',headers=account_headers('alex')).status_code==200
        with store.connect() as db:
            case=store.load(db,'KU-alice-INDEX-0000');case['handoff']=None;store.save(db,case)
        assert client.get('/api/agent/cases/KU-alice-INDEX-0000',headers=account_headers('alex')).status_code==404


def test_create_reuses_the_same_latest_trip_case_before_and_after_analysis(tmp_path):
    with TestClient(create_app(tmp_path/'db.sqlite3')) as client:
        h={'X-Demo-Session':'index-rider','X-Request-ID':uuid4().hex}
        body=dict(trip_id='trip-river',description='Please review my cancellation fee.')
        first=client.post('/api/cases',headers=h,json=body).json()
        duplicate=deepcopy(first);duplicate['id']='KU-LATEST-ALTERNATE';duplicate['revision']=0
        with client.app.state.store.connect() as db:client.app.state.store.save(db,duplicate)
        result=client.post('/api/cases',headers={**h,'X-Request-ID':uuid4().hex},json=body)
        assert result.status_code==200,result.text
        assert result.json()['id']==duplicate['id']
        assert client.app.state.store.get(first['id'])==first


def test_zero_fee_is_not_presented_as_a_specialist_adjustment_option(tmp_path):
    registry,catalog=config(tmp_path)
    data=json.loads(catalog.read_text())
    trip=next(item for item in data['trips'] if item['id']=='trip-river')
    trip.update(total=0,estimated=0,payments=[])
    catalog.write_text(json.dumps(data))
    with TestClient(create_app(tmp_path/'zero.sqlite3',catalog_path=catalog,security=Security('production',registry))) as client:
        case=client.post('/api/cases',headers=account_headers('alice'),json=dict(trip_id='trip-river',description='Review the canceled pickup.')).json()
        rider='/api/cases/'+case['id']+'/actions';staff='/api/agent/cases/'+case['id']+'/actions'
        case=client.post(rider,headers=account_headers('alice',**{'If-Match':'"'+str(case['revision'])+'"'}),json=dict(action='handoff')).json()
        case=client.post(staff,headers=account_headers('alex',**{'If-Match':'"'+str(case['revision'])+'"'}),json=dict(action='accept')).json()
        detail=client.get('/api/agent/cases/'+case['id'],headers=account_headers('alex')).json()
        assert detail['available_actions']==['reply','resolve']
        result=client.post(staff,headers=account_headers('alex',**{'If-Match':'"'+str(case['revision'])+'"'}),json=dict(action='propose_adjustment',text='Review the cancellation fee.'))
        assert result.status_code==409
        assert client.app.state.store.get(case['id'])['revision']==case['revision']


def test_inconsistent_legacy_identity_rolls_back_summary_migration(tmp_path):
    path=tmp_path/'bad.sqlite3';store=Store(path);seed(store,1)
    with store.connect() as db:
        case=store.load(db,'KU-index-rider-INDEX-0000');case['id']='different-identity'
        raw=json.dumps(case)
        db.execute('UPDATE cases SET data=?',(raw,));db.execute('DROP TABLE case_index')
        db.execute("DELETE FROM runtime_settings WHERE key='case_cursor_secret'");db.execute('PRAGMA user_version=3')
    with pytest.raises(RuntimeError,match='identity mismatch'):Store(path)
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==3
        assert db.execute('SELECT data FROM cases').fetchone()[0]==raw
        assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='case_index'").fetchone()[0]==0
