"""Replay dataset payloads through FastAPI, real orchestration, Chroma and stdio MCP.

Oracle labels are read only by this driver/scorer, never by the application.
Completed episodes can be purged from the isolated replay database after their raw
responses are flushed; this bounds quadratic idempotency-receipt storage.
"""
from collections import Counter
import gzip
import hashlib
import json
import os
from pathlib import Path
import statistics
import tempfile
import time
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
from mobility.app import create_app
from mobility.simulation.generate import sha


def score_case(case,label,step):
    checks={}
    if 'status' in label:
        checks['case_state']=case['status']==label['status']
        checks['proposal_amount']=(case.get('proposal') or {}).get('amount')==label['proposal_cents']
        checks['adjustment_amount']=(case.get('adjustment') or {}).get('amount')==label['adjustment_cents']
        checks['evidence_count']=len(case.get('evidence',[]))==label['evidence_count']
        checks['evidence_unverified']=all(e.get('verified') is False for e in case.get('evidence',[]))
        checks['single_adjustment_event']=sum(e['kind']=='adjustment' for e in case['events'])<=1
    if 'engine_status' in label:
        run=case['engine_runs'][-1]
        checks['engine_status']=run['status']==label['engine_status']
        claim_context=run.get('memory',{}).get('claims',{})
        checks['clarification']=claim_context.get('requires_clarification')==label['requires_clarification']
        checks['no_withdrawn_or_unadopted_claim']=all(value not in run['response'] for value in label['forbidden'])
        if not label['requires_clarification']:
            actual={(f['field'],f['value']) for f in claim_context.get('active_claims',[])}
            required=set(label['claims'].items())
            checks['active_claims']=required.issubset(actual)
            checks['claim_answer_coverage']=all(value in run['response'] for value in label['claims'].values())
        else:checks['asks_clarification']='Please clarify' in run['response']
        doc=hashlib.sha256(json.dumps(['explicit',label['policy_id']],ensure_ascii=True).encode()).hexdigest()
        checks['policy_identity_version']=any(c['document_id']==doc and label['policy_release'] in c['content'] for c in run['citations'])
        checks['actual_core_tools']=any(t.get('tool_name')=='get_case_context' and t.get('success') for t in run['tools'])
        if label['engine_status']=='success':
            checks['actual_mcp_tool']=any(t.get('source')=='external_mcp' and t.get('success') and t.get('result_success',True) is not False for t in run['tools'])
        checks['payment_boundary']='No payment action was performed' in run['response']
    return checks


def replay(dataset,output,limit=None,split=None,retain_db=False):
    dataset=Path(dataset).resolve();output=Path(output).resolve()
    if output.exists():raise ValueError('Refusing to overwrite replay results')
    output.mkdir(parents=True)
    manifest=json.loads((dataset/'manifest.json').read_text())
    for name,expected in manifest['files'].items():
        if sha(dataset/name)!=expected:raise ValueError(f'Dataset hash mismatch: {name}')
    scenarios=[json.loads(s) for s in (dataset/'scenarios.jsonl').read_text().splitlines()]
    if split:scenarios=[s for s in scenarios if s['split']==split]
    if limit is not None:scenarios=scenarios[:limit]
    labels={r['id']:r for r in map(json.loads,(dataset/'oracle.jsonl').read_text().splitlines())}
    db=output/'replay.sqlite3'
    rows=[];latencies=[];case_state=None
    raw=output/'responses.jsonl.gz'
    started=time.monotonic()
    with patch.dict(os.environ, {'KINDER_UBER_RIDE_MODEL_MODE': 'demo'}), TestClient(create_app(db,catalog_path=dataset/'catalog.json')) as client,gzip.open(raw,'wt') as traces,(output/'scores.jsonl').open('w') as scores:
        app=client.app
        for index,scenario in enumerate(scenarios):
            actor='replay-'+scenario['id']
            case=None;initial=None;create_key=None;request_keys=[]
            checks_list=[];episode_start=time.monotonic()
            def http(method,path,body=None,staff=False,key=None,owner=actor):
                headers={'X-Demo-Session':owner,'X-Request-ID':key or uuid.uuid4().hex}
                request_keys.append(headers['X-Request-ID'])
                if staff:headers['X-Demo-Agent']='local-demo-agent'
                stamp=time.perf_counter_ns()
                response=client.request(method,path,json=body,headers=headers)
                elapsed=(time.perf_counter_ns()-stamp)/1e6
                latencies.append(elapsed)
                traces.write(json.dumps(dict(episode=scenario['id'],method=method,path=path,request=body,status=response.status_code,response=response.json(),elapsed_ms=elapsed))+'\n')
                return response
            try:
                for step,label in zip(scenario['steps'],labels[scenario['id']]['steps'],strict=True):
                    kind=step['kind'];check={}
                    if kind=='fault_disconnect':
                        client.portal.call(app.state.service.engine.operations.bridge.close)
                        check['transport_closed']=all(c.state=='closed' for c in app.state.service.engine.operations.bridge.connections.values())
                    elif kind=='fault_reconnect':
                        result=http('POST','/api/agent/mcp/reconnect',body={},staff=True)
                        check['http']=result.status_code==label['http']
                    elif kind=='probe':
                        result=http('POST','/api/agent/mcp/probe',dict(trip_id=case['trip_id']),staff=True)
                        check['http']=result.status_code==label['http']
                        record=result.json().get('data',{}).get('structured',{})
                        check['mcp_catalog_consistency']=record.get('trip_id')==label['mcp_trip_id'] and record.get('settled_cents')==label['mcp_settled']
                    elif kind=='foreign_read':
                        result=http('GET',f"/api/cases/{case['id']}",owner=actor+'-other')
                        check['http']=result.status_code==label['http']
                    elif kind=='replay_create':
                        before=len(app.state.service.engine.client.calls)
                        result=http('POST','/api/cases',scenario['steps'][1 if scenario['steps'][0]['kind']=='fault_disconnect' else 0]['body'],key=create_key)
                        check.update(http=result.status_code==200,exact_receipt_replay=result.json()==initial,no_second_model_run=len(app.state.service.engine.client.calls)==before)
                    else:
                        if kind=='create':
                            create_key=uuid.uuid4().hex
                            result=http('POST','/api/cases',step['body'],key=create_key)
                        else:
                            path=f"/api/agent/cases/{case['id']}/actions" if kind.startswith('staff_') else f"/api/cases/{case['id']}/actions"
                            result=http('POST',path,step['body'],staff=kind.startswith('staff_'))
                        check['http']=result.status_code==label['http']
                        if result.status_code==200:
                            case=result.json()
                            if kind=='create':initial=case
                        elif case:
                            case=http('GET',f"/api/cases/{case['id']}").json()
                        if case:check.update(score_case(case,label,step))
                    checks_list.append(dict(kind=kind,passed=all(check.values()),checks=check))
                row=dict(id=scenario['id'],world_id=scenario['world_id'],split=scenario['split'],family=scenario['family'],passed=all(c['passed'] for c in checks_list),steps=checks_list,elapsed_ms=(time.monotonic()-episode_start)*1000)
            except Exception as error:
                row=dict(id=scenario['id'],world_id=scenario['world_id'],split=scenario['split'],family=scenario['family'],passed=False,steps=checks_list,error=f'{type(error).__name__}: {error}',elapsed_ms=(time.monotonic()-episode_start)*1000)
            rows.append(row);scores.write(json.dumps(row)+'\n');scores.flush();traces.flush()
            if not retain_db:
                # Only this disposable evaluation database is cleaned, after raw output flush.
                with app.state.store.connect() as connection:
                    connection.execute('DELETE FROM cases WHERE owner=?',(actor,))
                    for key in request_keys:connection.execute('DELETE FROM receipts WHERE key=?',(key,))
                if case:
                    for cid in (case['id'],'new'):
                        uid=hashlib.sha256(json.dumps([actor,cid,case['trip_id']]).encode()).hexdigest()
                        for name in ('ride_memory_episodes_v1','ride_memory_profiles_v1'):
                            app.state.service.engine.chroma.get_collection(name).delete(where={'user_id':uid})
            if (index+1)%25==0:print(f"Replayed {index+1}/{len(scenarios)} episodes; passed {sum(r['passed'] for r in rows)}",flush=True)
    grouped={}
    for field in ('split','family'):
        grouped[field]={value:dict(total=sum(r[field]==value for r in rows),passed=sum(r[field]==value and r['passed'] for r in rows)) for value in sorted({r[field] for r in rows})}
    failures=Counter(name for row in rows for step in row['steps'] for name,passed in step['checks'].items() if not passed)
    total_seconds=time.monotonic()-started
    summary=dict(dataset_id=manifest['dataset_id'],dataset_manifest_sha256=sha(dataset/'manifest.json'),episodes=len(rows),unique_worlds=len({r['world_id'] for r in rows}),passed=sum(r['passed'] for r in rows),http_requests=len(latencies),grouped=grouped,failed_checks=dict(failures),total_seconds=total_seconds,
                 median_http_ms=statistics.median(latencies) if latencies else None,p95_http_ms=sorted(latencies)[max(0,__import__('math').ceil(.95*len(latencies))-1)] if latencies else None,
                 source_sha256={str(p.relative_to(Path(__file__).parents[3])):sha(p) for p in Path(__file__).parents[1].rglob('*.py')},
                 raw_sha256=sha(raw),scores_sha256=sha(output/'scores.jsonl'),mode='actual FastAPI TestClient + RideEngine + Chroma + independent stdio MCP, deterministic model',
                 limitations=['Serial replay, not a concurrency or production throughput benchmark.','Mechanical reference-state and claim checks, not human semantic judgment.','HTTP timing excludes external model inference.','Completed episodes are purged from the isolated replay DB unless retain_db is selected.','No real customer data or payment execution.'])
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary
