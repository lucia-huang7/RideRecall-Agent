"""Audit release integrity and event/catalog agreement without starting the service."""
from collections import Counter
import json
from pathlib import Path
from mobility.catalog import CatalogDocument
from mobility.simulation.generate import sha


def audit(dataset):
    dataset=Path(dataset)
    manifest=json.loads((dataset/'manifest.json').read_text())
    for name,digest in manifest['files'].items():
        if sha(dataset/name)!=digest:raise ValueError(f'Hash mismatch: {name}')
    catalog=CatalogDocument.model_validate_json((dataset/'catalog.json').read_text())
    worlds={w['id']:w for w in map(json.loads,(dataset/'world-events.jsonl').read_text().splitlines())}
    scenarios=list(map(json.loads,(dataset/'scenarios.jsonl').read_text().splitlines()))
    labels={r['id']:r for r in map(json.loads,(dataset/'oracle.jsonl').read_text().splitlines())}
    trips={t.id:t for t in catalog.trips}
    assert len({s['id'] for s in scenarios})==len(scenarios)==len(labels)
    assert len(worlds)==manifest['statistics']['worlds']
    for world in worlds.values():
        trip=trips[world['trip_id']]
        assert trip.total==sum(e['amount'] for e in world['events'] if e['type']=='settled')
        assert trip.estimated==next(e['amount'] for e in world['events'] if e['type']=='estimate')
        assert trip.policy_scope==world['scope']
        assert catalog.policies[trip.policy_scope].rules.max_auto_adjustment_cents==world['cap']
    groups=Counter()
    for scenario in scenarios:
        world=worlds[scenario['world_id']]
        assert scenario['split']==world['split'], 'Related world variants crossed split boundaries'
        assert labels[scenario['id']]['world_id']==world['id']
        assert len(scenario['steps'])==len(labels[scenario['id']]['steps'])
        groups[world['id']]+=1
    assert all(count==2 for count in groups.values()) and set(groups)==set(worlds)
    return dict(dataset_id=catalog.dataset_id,worlds=len(worlds),episodes=len(scenarios),verified_file_hashes=len(manifest['files']),group_split_overlap=0,human_review='pending',status='passed')
