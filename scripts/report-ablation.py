"""Summarize frozen paired replay results without changing the original scorer."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
import math
from pathlib import Path
import statistics

CONFIGS = ('single_recent4', 'multi_recent4', 'single_durable', 'multi_durable')
BUSINESS = {'http','case_state','proposal_amount','adjustment_amount','evidence_count','evidence_unverified','single_adjustment_event','transport_closed','mcp_catalog_consistency','exact_receipt_replay','no_second_model_run'}


def percentile(values, q):
    return sorted(values)[max(0, math.ceil(q * len(values))-1)] if values else None


def summarize(rows, contexts, analyses):
    ids = {r['id'] for r in rows}
    probes = [x for x in contexts if x['episode'] in ids]
    eligible = [x for x in probes if x['eligible']]
    by_episode = {}
    for x in eligible:
        by_episode.setdefault(x['episode'], []).append(x)
    final = [items[-1] for items in by_episode.values()]
    a = [x for x in analyses if x['episode'] in ids]
    clarification = [x for x in probes if x['expected_clarification']]
    business = sum('error' not in r and all(v for st in r['steps'] for k,v in st['checks'].items() if k in BUSINESS) for r in rows)
    return dict(dialogues=len(rows),worlds=len({r['world_id'] for r in rows}),strict_completed=sum(r['passed'] for r in rows),strict_completion_rate=sum(r['passed'] for r in rows)/len(rows),business_conformant=business,business_conformance_rate=business/len(rows),episode_exceptions=sum('error' in r for r in rows),failed_checks=dict(Counter(k for r in rows for st in r['steps'] for k,v in st['checks'].items() if not v)),context_eligible_checkpoints=len(eligible),context_lost_checkpoints=sum(x['context_lost'] for x in eligible),context_loss_rate=sum(x['context_lost'] for x in eligible)/len(eligible) if eligible else None,context_eligible_dialogues=len(by_episode),dialogues_with_context_loss=sum(any(x['context_lost'] for x in items) for items in by_episode.values()),final_context_losses=sum(x['context_lost'] for x in final),final_context_loss_rate=sum(x['context_lost'] for x in final)/len(final) if final else None,stale_claim_checkpoints=sum(x['stale_claim'] for x in probes),expected_clarification_checkpoints=len(clarification),passed_clarification_checkpoints=sum(bool(x['clarification_passed']) for x in clarification),median_dialogue_ms=statistics.median(r['elapsed_ms'] for r in rows),p95_dialogue_ms=percentile([r['elapsed_ms'] for r in rows],.95),analyses=len(a),model_calls=sum(x['model_calls'] for x in a),multi_role_analyses=sum(len(x['agents'])>1 for x in a),role_execution_counts=dict(Counter(len(x['agents']) for x in a)),median_analysis_ms=statistics.median(x['elapsed_ms'] for x in a) if a else None,p95_analysis_ms=percentile([x['elapsed_ms'] for x in a],.95))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',type=Path,required=True);args=parser.parse_args(); root=args.run
    freeze=json.loads((root/'freeze.json').read_text());dataset=Path(freeze['dataset']);source=Path(freeze['frozen_root']);sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    assert all(sha(source/k)==v for k,v in freeze['source_sha256'].items()), 'Frozen source changed'
    assert all(sha(dataset/k)==v for k,v in freeze['dataset_sha256'].items()), 'Dataset changed'
    scenarios=[json.loads(s) for s in (dataset/'scenarios.jsonl').read_text().splitlines()];expected={s['id'] for s in scenarios};assert len(expected)==2000
    worlds={}
    for s in scenarios:worlds.setdefault(s['world_id'],set()).add(s['split'])
    assert len(worlds)==1000 and all(len(v)==1 for v in worlds.values())
    result={'freeze':freeze,'conditions':{},'paired':{},'integrity':{}};paired={}
    for c in CONFIGS:
        d=root/c; summary=json.loads((d/'summary.json').read_text());rows=[json.loads(s) for s in (d/'replay/scores.jsonl').read_text().splitlines()];obs=[json.loads(s) for s in (d/'observations.jsonl').read_text().splitlines()]
        assert len(rows)==2000 and {x['id'] for x in rows}==expected
        assert sha(d/'observations.jsonl')==summary['observations_sha256']
        assert sha(d/'replay/scores.jsonl')==summary['scores_sha256'] and sha(d/'replay/responses.jsonl.gz')==summary['raw_sha256']
        # Distinct observations for an analysis, even if a state checkpoint reuses it.
        contexts=list({(x['episode'],x['analysis_id']):x for x in obs if x['kind']=='context'}.values());analyses=[x for x in obs if x['kind']=='analysis']
        assert all(len(x['agents'])==1 for x in analyses) if c.startswith('single') else any(len(x['agents'])>1 for x in analyses)
        if c.endswith('recent4'):assert all(x['visible_rider_rounds']<=4 for x in analyses)
        statuses=Counter()
        with gzip.open(d/'replay/responses.jsonl.gz','rt') as h:
            for line in h:statuses[str(json.loads(line)['status'])]+=1
        assert sum(statuses.values())==summary['http_requests']
        groups={field:{key:summarize([x for x in rows if x[field]==key],contexts,analyses) for key in sorted({x[field] for x in rows})} for field in ('split','family')}
        result['conditions'][c]=dict(overall=summarize(rows,contexts,analyses),groups=groups,total_seconds=summary['total_seconds'],http_requests=summary['http_requests'],http_status_counts=dict(statuses),median_http_ms=summary['median_http_ms'],p95_http_ms=summary['p95_http_ms'],raw_sha256=summary['raw_sha256'],scores_sha256=summary['scores_sha256'],observations_sha256=summary['observations_sha256'],exception_examples=[{'id':x['id'],'error':x['error']} for x in rows if 'error' in x][:10],failure_examples=[{'id':x['id'],'failed_checks':sorted({k for st in x['steps'] for k,v in st['checks'].items() if not v})} for x in rows if not x['passed']][:10])
        final={}
        for x in contexts:
            if x['eligible']:final[x['episode']]=x['context_lost']
        paired[c]={x['id']:{'passed':x['passed'],'final_context_lost':final.get(x['id'])} for x in rows}
        result['integrity'][c]={'coverage':True,'hashes_match':True,'raw_count_matches':True,'role_and_window_verified':True}
    for a,b in [('single_recent4','single_durable'),('multi_recent4','multi_durable'),('single_recent4','multi_recent4'),('single_durable','multi_durable')]:
        comparisons={}
        for split in ['all','development','calibration','test']:
            ids=expected if split=='all' else {x['id'] for x in scenarios if x['split']==split}; counts=Counter();context=Counter()
            for key in ids:
                x,y=paired[a][key],paired[b][key];counts['b_wins' if y['passed'] and not x['passed'] else 'a_wins' if x['passed'] and not y['passed'] else 'ties']+=1
                if x['final_context_lost'] is not None and y['final_context_lost'] is not None:context['b_wins' if x['final_context_lost'] and not y['final_context_lost'] else 'a_wins' if y['final_context_lost'] and not x['final_context_lost'] else 'ties']+=1
            comparisons[split]={'strict_task':dict(counts),'final_context':dict(context)}
        result['paired'][a+'__vs__'+b]=comparisons
    (root/'verified-comparison.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({c:v['overall'] for c,v in result['conditions'].items()},indent=2))


if __name__=='__main__':main()
