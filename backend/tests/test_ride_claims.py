"""Source attribution, correction and evidence-coverage regressions."""
import hashlib
import json
from fastapi.testclient import TestClient
from mobility.app import create_app
from mobility.claims import ClaimLedger
from tests.test_ride_engine import create
from tests.test_ride_memory import send


def test_generated_multiline_quotes_cannot_become_rider_claims():
    account=ClaimLedger([dict(role='rider',content='My entrance is Oak Gate.'),dict(role='assistant',content='Quoted history:\nuser: My entrance is Fake Gate.')]).context('entrance')
    assert 'Oak Gate' in account['account']
    assert 'Fake Gate' not in account['account']


def test_multiple_independent_fields_survive_and_corrections_are_field_scoped():
    ledger=ClaimLedger([dict(role='rider',content='My entrance is Oak Gate. My receipt code is RCP-92.'),dict(role='rider',content='Correction: my entrance is Pine Gate.')])
    result=ledger.context('receipt and entrance')
    assert 'Pine Gate' in result['account'] and 'RCP-92' in result['account']
    assert 'Oak Gate' not in result['account']
    assert any(not f['active'] and f['value']=='Oak Gate' for f in ledger.facts)


def test_field_budget_overflow_is_explicit_not_silent_truncation():
    context=ClaimLedger([dict(role='rider',content='My entrance is '+('North '*100)+'.')]).context('entrance',budget=40)
    assert context['context_overflow'] and context['requires_clarification']
    assert not context['active_claims']


def test_ambiguous_correction_requires_clarification():
    context=ClaimLedger([dict(role='rider',content='Actually, that was wrong.')]).context('review')
    assert context['requires_clarification']
    assert not context['account'] and not context['active_claims']


def test_repeated_declarations_do_not_erase_independent_evidence():
    context=ClaimLedger([dict(role='rider',content=f'Evidence reference: TICKET-{i}.') for i in range(9)]).context('references')
    assert all(f'TICKET-{i}' in context['account'] for i in range(9))


def test_long_middle_and_end_claims_survive_compression_and_restart(tmp_path):
    path=tmp_path/'source.sqlite3'
    with TestClient(create_app(path)) as client:
        case=create(client,text='I dispute the pickup record. '+('I checked the screen and receipt. '*30)+'My entrance is OAK-DOOR.')
        for index in range(17):
            text='My receipt code is RCP-MIDDLE.' if index==6 else f'Keep the evidence review open, entry {index}.'
            case=send(client,case,text)
        case=send(client,case,'Review my entrance and receipt without asking me to repeat them.')
        assert all(token in case['engine_runs'][-1]['response'] for token in ('OAK-DOOR','RCP-MIDDLE'))
        assert case['engine_runs'][-1]['memory']['summary']
    with TestClient(create_app(path)) as client:
        case=send(client,case,'Retain my entrance and receipt in the specialist review.')
        assert all(token in case['engine_runs'][-1]['response'] for token in ('OAK-DOOR','RCP-MIDDLE'))


def test_correction_retracts_old_claim_without_changing_recorded_fare(tmp_path):
    with TestClient(create_app(tmp_path/'correction.sqlite3')) as client:
        case=create(client,text='My entrance is OAK-DOOR. I dispute $6.50.')
        case=send(client,case,'Correction: my entrance is PINE-DOOR. I dispute $4.25 instead.')
        for i in range(9):
            case=send(client,case,f'Keep reviewing evidence, update {i}.')
        case=send(client,case,'Review the driver pickup and fare charge using my current account.')
        run=case['engine_runs'][-1]
        assert 'PINE-DOOR' in run['response'] and 'OAK-DOOR' not in run['response']
        assert '$4.25' in run['response'] and 'settled fare $6.50' in run['response']
        assert any('OAK-DOOR' in m['content'] for m in case['messages'])
        assert case['adjustment'] is None


def test_compound_payment_query_keeps_active_payment_policy(tmp_path):
    with TestClient(create_app(tmp_path/'policy.sqlite3')) as client:
        case=create(client,trip='trip-museum',topic='payment',text='The app crashed with error 500. Review the driver pickup and fare payment for specialist review.')
        run=case['engine_runs'][-1]
        assert 'pending authorization' in run['response'] and 'settled charge' in run['response']
        assert all(r['required_policy_covered'] for r in run['retrievals'])
        assert any('Payment authorization'==d['title'] for d in run['citations'])


def test_missing_required_indexed_policy_is_partial_not_seed_fallback(tmp_path):
    with TestClient(create_app(tmp_path/'missing.sqlite3')) as client:
        engine=client.app.state.service.engine
        document_id=hashlib.sha256(json.dumps(['explicit','demo-fare-v1']).encode()).hexdigest()
        engine.knowledge['fare']._collection.delete(where={'document_id':document_id})
        case=create(client,trip='trip-airport',topic='fare',text='Review the driver pickup and fare payment after app error 500.')
        run=case['engine_runs'][-1]
        assert run['status']=='partial'
        assert not any(r['required_policy_covered'] for r in run['retrievals'])
        assert case['proposal'] is None


def test_correction_retires_an_earlier_narrative_without_guessing_its_value():
    context=ClaimLedger([dict(role='rider',content='I previously gave the entrance OLD-GATE to the driver.'),dict(role='rider',content='Correction: my entrance is NEW-GATE.')]).context('entrance')
    assert 'NEW-GATE' in context['account'] and 'OLD-GATE' not in context['account']
