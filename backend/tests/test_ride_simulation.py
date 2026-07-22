"""Contract, business-boundary and real pipeline checks for synthetic releases."""
from copy import deepcopy
import json
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from mobility.app import create_app
from mobility.catalog import CatalogDocument, RideCatalog
from mobility.simulation.audit import audit
from mobility.simulation.generate import generate
from mobility.simulation.replay import replay


def test_release_is_deterministic_and_world_variants_stay_together(tmp_path):
    left=tmp_path/'left';right=tmp_path/'right'
    a=generate(left,96);b=generate(right,96)
    assert a['files']==b['files']
    assert audit(left)['group_split_overlap']==0
    with pytest.raises(ValueError):generate(left,96)
    with (left/'scenarios.jsonl').open('a') as f:f.write('{}\n')
    with pytest.raises(ValueError,match='Hash mismatch'):audit(left)


def test_runtime_contract_rejects_labels_and_inconsistent_financial_records(tmp_path):
    generate(tmp_path/'release',8)
    data=json.loads((tmp_path/'release/catalog.json').read_text())
    for modify in ('gold','amount','scope'):
        invalid=deepcopy(data)
        if modify=='gold':invalid['trips'][-1]['expected_outcome']='approved'
        elif modify=='amount':invalid['trips'][-1]['total']+=1
        else:invalid['trips'][-1]['policy_scope']='missing-policy'
        with pytest.raises(ValidationError):CatalogDocument.model_validate(invalid)


def test_policy_version_and_ledger_change_outcome_without_scenario_labels(tmp_path,monkeypatch):
    monkeypatch.setenv('KINDER_UBER_RIDE_MODEL_MODE','demo')
    release=tmp_path/'release';generate(release,16)
    data=json.loads((release/'catalog.json').read_text())
    trip=deepcopy(data['trips'][4])
    trip.update(total=2200,estimated=1000,payments=[dict(id='settled-one',status='settled',amount=2200)])
    first=deepcopy(trip);first.update(id='trip-policy-first',policy_scope='fare-r1')
    second=deepcopy(trip);second.update(id='trip-policy-second',policy_scope='fare-r2')
    data['trips']=[first,second]
    path=tmp_path/'runtime.json';path.write_text(json.dumps(data))
    # The app can start with only a runtime catalog, with no scenario/oracle file.
    with TestClient(create_app(tmp_path/'app.sqlite3',catalog_path=path)) as client:
        assert client.get('/health').json()['catalog_trips']==2
        headers={'X-Demo-Session':'version-contract','X-Request-ID':'one'}
        a=client.post('/api/cases',json=dict(trip_id=first['id'],description='Please review my fare.',topic='fare'),headers=headers)
        headers['X-Request-ID']='two'
        b=client.post('/api/cases',json=dict(trip_id=second['id'],description='Please review my fare.',topic='fare'),headers=headers)
        assert a.status_code==b.status_code==200
        assert a.json()['proposal']['amount']==1200
        assert b.json()['proposal'] is None and b.json()['status']=='needs_review'
    assert RideCatalog(path).trip(second['id'])['eligibility']=='review'


def test_full_api_replay_uses_mcp_and_preserves_failed_checks(tmp_path):
    release=tmp_path/'release';generate(release,8)
    result=replay(release,tmp_path/'result')
    assert result['episodes']==16 and result['passed']==16
    assert result['http_requests']>150
    assert result['failed_checks']=={}
    assert (tmp_path/'result/responses.jsonl.gz').stat().st_size>0
