"""Catalog changes cannot silently orphan or transfer existing case history."""
import json

import pytest
from fastapi.testclient import TestClient
from mobility.app import create_app
from mobility.security import Security
from test_ride_security import config, create, headers
from test_ride_engine import headers as demo_headers


@pytest.mark.parametrize('production',[False,True])
def test_removed_historical_trip_refuses_startup_without_rewriting_cases(tmp_path,production):
    registry,path=config(tmp_path);database=tmp_path/'app.sqlite3'
    def app():
        return create_app(database,catalog_path=path,security=Security('production',registry) if production else Security('test'))
    with TestClient(app()) as client:
        case=create(client) if production else client.post('/api/cases',json=dict(trip_id='trip-river',description='Please review my cancellation fee.'),headers=demo_headers()).json()
        with client.app.state.store.connect() as db:
            before=db.execute('SELECT data FROM cases WHERE id=?',(case['id'],)).fetchone()[0]
    data=json.loads(path.read_text());data['trips']=[trip for trip in data['trips'] if trip['id']!=case['trip_id']]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='omits a stored case trip'):
        with TestClient(app()):pass
    import sqlite3
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT data FROM cases WHERE id=?',(case['id'],)).fetchone()[0]==before


def test_catalog_owner_change_requires_reviewed_migration(tmp_path):
    registry,path=config(tmp_path);database=tmp_path/'app.sqlite3'
    def app():return create_app(database,catalog_path=path,security=Security('production',registry))
    with TestClient(app()) as client:case=create(client)
    with TestClient(app()) as client:
        assert client.get('/api/cases/'+case['id'],headers=headers('alice')).json()==case
    data=json.loads(path.read_text())
    for trip in data['trips']:
        if trip['id']==case['trip_id']:trip['rider_id']='bob'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='changes stored case ownership'):
        with TestClient(app()):pass
