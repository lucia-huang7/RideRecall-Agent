"""Generate reproducible event worlds, runtime catalogs, dialogue inputs and labels."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random

from mobility.catalog import CatalogDocument
from mobility.seed import POLICIES, TRIPS
from mobility.simulation.oracle import financial_expectation, label_episode

FAMILIES=('pickup_conflict','fare_supported','fare_over_cap','authorization','duplicate_settlement','evidence_mismatch','mcp_recovery','no_excess')


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def episode(world,variant):
    steps=[];semantics=[]
    def add(kind,body=None,operation=None):
        steps.append(dict(kind=kind,**({'body':body} if body else {})))
        semantics.append(dict(operation=operation))
    if world['family']=='mcp_recovery':add('fault_disconnect')
    initial=f"Review the driver pickup and fare payment after app error 500. My boarding label is {world['old']}. My supporting reference is {world['support']}."
    add('create',dict(trip_id=world['trip_id'],topic=world['issue'],description=initial))
    add('replay_create')
    if world['family']=='mcp_recovery':
        add('fault_reconnect')
        add('message',dict(action='message',text='Please retry the driver pickup and fare payment review after app error 500.'))
    if world['family']=='evidence_mismatch':
        note=f"This unverified receipt names {world['other_trip_id']}, not the linked trip. Keep this mismatch for specialist review."
    else:note=f"Unverified supporting receipt reference: {world['support']}."
    add('evidence',dict(action='evidence',text=note))
    mode=world['mode']
    if mode=='no_change':
        change=f"What if we replace {world['old']} with {world['new']}?" if variant==0 else f"The driver said replace {world['old']} with {world['new']}."
        add('message',dict(action='message',text=change))
    elif mode=='ambiguous_then_resolved':
        add('message',dict(action='message',text=f"Make that {world['new']}."),operation='ambiguous')
        add('message',dict(action='message',text=f"My boarding label is {world['new']}."),operation='replace')
    else:
        if world['split']=='test':
            change=(f"It wasn't {world['old']} but {world['new']}." if variant==0 else f"Please replace {world['old']} with {world['middle']}; replace {world['middle']} with {world['new']}.")
        elif world['split']=='calibration':
            change=(f"Use {world['new']} rather than {world['old']}." if variant==0 else f"Please switch that label from {world['old']} to {world['new']}.")
        else:
            change=(f"Correction: my boarding label is {world['new']}." if variant==0 else f"Replace {world['old']} with {world['new']}.")
        add('message',dict(action='message',text=change),operation='replace')
    for i in range(world['gap']):
        add('message',dict(action='message',text=f"Keep the evidence review open while I check the receipt, update {i+1}."))
    add('message',dict(action='message',text='Review the driver pickup, app error 500 and fare payment with my current details. Retain the completed correction without starting another edit.'))
    add('probe')
    add('foreign_read')
    outcome=financial_expectation(world)
    if outcome['status']=='awaiting_confirmation' and world['family']!='mcp_recovery':
        add('confirm',dict(action='confirm'))
        add('confirm',dict(action='confirm'))
    elif outcome['status']=='awaiting_rider' and world['family']!='mcp_recovery':
        add('close',dict(action='close'))
        add('close',dict(action='close'))
    else:
        add('handoff',dict(action='handoff'))
        add('staff_accept',dict(action='accept',text=''))
        add('staff_reply',dict(action='reply',text='I have reviewed the existing trip, messages and unverified evidence.'))
        add('staff_resolve',dict(action='resolve',text='The evidence review is recorded. No financial adjustment is authorized by this review.'))
    row=dict(schema_version=1,id=f"{world['id']}-v{variant}",world_id=world['id'],split=world['split'],family=world['family'],variant=variant,steps=steps)
    return row,dict(id=row['id'],world_id=world['id'],steps=label_episode(world,steps,semantics))


def generate(output,worlds=1000,seed=20261021):
    if worlds < 8: raise ValueError("At least eight worlds are required")
    output=Path(output)
    if output.exists():raise ValueError('Refusing to overwrite an existing dataset release')
    output.mkdir(parents=True)
    rng=random.Random(seed)
    policies=deepcopy(POLICIES)
    for topic in ('cancellation','fare','payment'):
        for release,cap in ((1,1500),(2,500)):
            scope=f'{topic}-r{release}'
            policy=deepcopy(POLICIES[topic])
            policy.update(id=f'synthetic-{topic}-r{release}',version=f'Fictional policy release R{release}',rules=dict(max_auto_adjustment_cents=cap))
            policy['text']+=f' Policy release R{release} applies to this catalog scope.'
            if topic=='fare':policy['text']+=f' Automatic simulated adjustments require a positive excess of at most {cap} cents; larger differences require specialist review.'
            policies[scope]=policy
    records=[]
    for i in range(worlds):
        family=FAMILIES[i%len(FAMILIES)]
        issue='cancellation' if family in ('pickup_conflict','evidence_mismatch') else ('payment' if family in ('authorization','duplicate_settlement') else 'fare')
        release=1+(i//len(FAMILIES))%2
        cap=1500 if release==1 else 500
        estimate=rng.randrange(500,8000)
        price=estimate+rng.randrange(1,cap+1) if family in ('fare_supported','mcp_recovery') else (estimate+cap+rng.randrange(1,1800) if family=='fare_over_cap' else estimate)
        if family=='no_excess':price=max(100,estimate-rng.randrange(0,min(estimate,300)))
        if issue=='cancellation':price=rng.randrange(200,1400);estimate=0
        events=[dict(type='estimate',amount=estimate),dict(type='settled',amount=price)]
        if family=='duplicate_settlement':events.append(dict(type='settled',amount=price))
        if family=='authorization':events.append(dict(type='pending',amount=price))
        group=f'world-{i:06d}'
        token=lambda prefix:prefix+'-'+''.join(rng.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(9))
        bucket=int(hashlib.sha256(f'{seed}/{group}'.encode()).hexdigest()[:8],16)%10
        split='development' if bucket<6 else ('calibration' if bucket<8 else 'test')
        records.append(dict(id=group,trip_id=f'trip-synthetic-{i:06d}',family=family,issue=issue,release=f'R{release}',policy_id=f'synthetic-{issue}-r{release}',scope=f'{issue}-r{release}',cap=cap,events=events,split=split,
                            old=token('ENTRY'),new=token('UPDATED'),middle=token('INTERMEDIATE'),support=token('RECEIPT'),mode=('correction','no_change','ambiguous_then_resolved','chain')[i//len(FAMILIES)%4],gap=(0,1,8)[i//32%3]))
    trips=deepcopy(TRIPS)
    for i,world in enumerate(records):
        world['other_trip_id']=records[(i+1)%len(records)]['trip_id']
        posted=sum(e['amount'] for e in world['events'] if e['type']=='settled')
        trip=dict(id=world['trip_id'],date='Oct 21, 2026',time=f'{i%24:02d}:{i%60:02d}',destination=f'Fictional District {i+1}',pickup=f'Fictional Stop {rng.randrange(1,10000)}',vehicle=('Standard','Comfort')[i%2],status='Canceled' if world['issue']=='cancellation' else 'Completed',total=posted,estimated=world['events'][0]['amount'],duration=f'{rng.randrange(5,80)} min',distance=f'{rng.randrange(1,40)} mi',issue=world['issue'],arrival_disputed=world['issue']=='cancellation',caption='Synthetic ride-support event record.',color='sand',policy_scope=world['scope'],payments=[dict(id=f"{world['id']}-payment-{j}",status=e['type'],amount=e['amount']) for j,e in enumerate(world['events']) if e['type'] in ('settled','pending')])
        trips.append(trip)
    catalog=dict(schema_version=1,dataset_id=f'ride-support-{seed}-{worlds}',trips=trips,policies=policies)
    CatalogDocument.model_validate(catalog)
    (output/'catalog.json').write_text(json.dumps(catalog,indent=2)+'\n')
    # Preview is a subset of the same runtime contract, never a different demo pipeline.
    preview=dict(catalog,trips=trips[:3]+trips[3:19])
    (output/'catalog-preview.json').write_text(json.dumps(preview,indent=2)+'\n')
    (output/'catalog.schema.json').write_text(json.dumps(CatalogDocument.model_json_schema(),indent=2)+'\n')
    with (output/'scenarios.jsonl').open('w') as inputs,(output/'oracle.jsonl').open('w') as labels,(output/'world-events.jsonl').open('w') as events:
        for world in records:
            events.write(json.dumps(world)+'\n')
            for variant in (0,1):
                row,gold=episode(world,variant)
                inputs.write(json.dumps(row)+'\n');labels.write(json.dumps(gold)+'\n')
    # Explicitly pending review, never pretend a generated label is human-approved.
    sample=random.Random(seed+1).sample(records,min(100,len(records)))
    (output/'human-review.jsonl').write_text(''.join(json.dumps(dict(world_id=w['id'],review_status='pending',checks=['event consistency','business outcome','dialogue semantics','oracle correctness']))+'\n' for w in sample))
    stats=dict(worlds=worlds,episodes=worlds*2,catalog_trips=len(trips),variants_per_world=2,families=dict(Counter(w['family'] for w in records)),splits=dict(Counter(w['split'] for w in records)),human_review_status='not yet independently reviewed')
    sources={str(p.relative_to(Path(__file__).parents[3])):sha(p) for p in Path(__file__).parent.glob('*.py')}
    manifest=dict(schema_version=1,seed=seed,dataset_id=catalog['dataset_id'],statistics=stats,files={p.name:sha(p) for p in output.iterdir() if p.is_file()},generator_sources=sources,
                  limitations=['Synthetic event worlds, not real user traffic.','Dialogue variants of a world remain in the same split.','Development/calibration/test splits are not training of a model.','The oracle is an independent reference implementation, not human ground truth.','Runtime receives catalog only; scenario labels never enter the application.','Language is deterministic synthetic variation; semantic quality needs independent human/model review.'])
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest
