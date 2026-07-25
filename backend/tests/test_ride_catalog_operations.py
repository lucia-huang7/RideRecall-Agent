"""Operator tools and diagnostics must use the selected catalog, not seed-only IDs."""
from copy import deepcopy
import json

from fastapi.testclient import TestClient
from mobility.app import create_app
from mobility.security import Security
from test_ride_security import config, headers


def custom_catalog(tmp_path,partial=False):
    registry,path=config(tmp_path)
    data=json.loads(path.read_text());data['dataset_id']='custom-operator-release'
    scopes={'cancellation':'city-cancel-v3','fare':'city-fare-v3','payment':'city-pay-v3'}
    data['policies']={scopes[key]:value for key,value in data['policies'].items()}
    for index,trip in enumerate(data['trips']):trip.update(id='trip-custom-'+str(index),policy_scope=scopes[trip['issue']])
    if partial:
        data['trips']=[trip for trip in data['trips'] if trip['issue']=='fare']
        data['policies']={'city-fare-v3':data['policies']['city-fare-v3']}
    path.write_text(json.dumps(data))
    return registry,path,data


def test_operator_catalog_discovery_and_actual_mcp_probe_use_custom_ids(tmp_path):
    registry,path,data=custom_catalog(tmp_path)
    with TestClient(create_app(tmp_path/'db.sqlite3',catalog_path=path,security=Security('production',registry))) as client:
        response=client.get('/api/agent/mcp/catalog',headers=headers('ops'))
        assert response.status_code==200
        catalog=response.json()
        assert catalog['dataset_id']==data['dataset_id']
        assert {trip['id'] for trip in catalog['trips']}=={trip['id'] for trip in data['trips']}
        assert all(set(trip)=={'id','label'} and trip['label']==trip['id'] for trip in catalog['trips'])
        assert client.get('/api/agent/mcp/catalog',headers=headers('alice')).status_code==403
        assert client.get('/api/agent/mcp/catalog',headers=headers('alex')).status_code==403
        probe=client.post('/api/agent/mcp/probe',headers=headers('ops'),json=dict(trip_id=catalog['trips'][0]['id']))
        assert probe.status_code==200 and probe.json()['data']['structured']['trip_id']==catalog['trips'][0]['id']
        scopes=client.get('/api/agent/knowledge',headers=headers('ops')).json()['scopes']
        assert 'fare' not in {scope['id'] for scope in scopes}
        assert all(client.get('/api/agent/knowledge/'+scope['id'],headers=headers('ops')).json()['count']>0 for scope in scopes)


def test_custom_catalog_diagnostics_execute_without_creating_rider_cases(tmp_path):
    registry,path,data=custom_catalog(tmp_path)
    with TestClient(create_app(tmp_path/'db.sqlite3',catalog_path=path,security=Security('production',registry))) as client:
        report=client.post('/api/agent/evaluations',headers=headers('ops')).json()
        assert report['dataset_id']==data['dataset_id'] and report['skipped']==0
        assert report['passed']==report['total']
        assert all(row.get('trip_id','trip-custom-').startswith('trip-custom-') for row in report['cases'])
        quality=client.app.state.service.engine.operations.quality
        def progress(*args):pass
        result=client.portal.call(quality.run,progress)
        assert result['dataset_id']==data['dataset_id'] and result['fixture_trip_id'].startswith('trip-custom-')
        assert result['status']=='complete' and result['passed']==result['total']
        assert client.app.state.store.list()==[]


def test_missing_trip_categories_are_not_reported_as_failures_or_inferred_passes(tmp_path):
    registry,path,data=custom_catalog(tmp_path,partial=True)
    with TestClient(create_app(tmp_path/'db.sqlite3',catalog_path=path,security=Security('production',registry))) as client:
        response=client.post('/api/agent/evaluations',headers=headers('ops'))
        assert response.status_code==200
        report=response.json();skipped=[row for row in report['cases'] if row.get('status')=='not_applicable']
        assert report['skipped']==len(skipped)==2
        assert all(not row['passed'] and not row['checks'] for row in skipped)
        assert report['total']==len(report['cases'])-len(skipped)
        assert report['passed']==report['total']


def test_regression_signature_is_bound_to_catalog_content_not_file_location(tmp_path):
    registry,path,data=custom_catalog(tmp_path)
    def signature(name):
        with TestClient(create_app(tmp_path/(name+'.sqlite3'),catalog_path=path,security=Security('production',registry))) as client:
            return client.app.state.service.engine.operations.quality.evaluator._suite_id([],[])
    first=signature('a');assert signature('same-content')==first
    changed=deepcopy(data);changed['trips'][0]['caption']='A separately reviewed catalog release.'
    path.write_text(json.dumps(changed))
    assert signature('changed-content')!=first
