"""Business integration tests with a real temporary SQLite database and no LLM."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from mobility.app import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "rides.sqlite3")) as client:
        yield client


def headers(session="rider-a", key=None, agent=False):
    return {"X-Demo-Session": session, "X-Request-ID": key or uuid4().hex, **({"X-Demo-Agent": "local-demo-agent"} if agent else {})}


def create(client, trip="trip-river", session="rider-a", key=None, topic="cancellation"):
    response = client.post("/api/cases", json={"trip_id": trip, "description": "The driver did not pick me up. Please review the charge.", "topic": topic}, headers=headers(session, key))
    assert response.status_code == 200, response.text
    return response.json()


def act(client, case, action, text="", session="rider-a", key=None):
    return client.post(f"/api/cases/{case['id']}/actions", json={"action": action, "text": text}, headers=headers(session, key))


def test_no_key_demo_reuses_original_skills_and_guarded_tools(client):
    assert client.get("/health").json()["api_key_required"] is False
    case = create(client)
    assert case["status"] == "needs_review"
    assert case["skill_names"] == ["Ride resolution"]
    assert len(case["trace"]) == 3 and all(t["success"] for t in case["trace"])
    assert case["proposal"] is None


def test_request_replay_and_fingerprint_conflict(client):
    first = create(client, key="same")
    assert create(client, key="same") == first
    response = client.post("/api/cases", headers=headers(key="same"), json={"trip_id": "trip-river", "description": "A different request", "topic": "other"})
    assert response.status_code == 409
    assert len(client.get("/api/cases", headers=headers()).json()) == 1


def test_duplicate_active_case_does_not_drop_original_description(client):
    first = create(client)
    again = create(client)
    assert first["id"] == again["id"]


def test_case_ownership_and_validation(client):
    case = create(client)
    assert client.get(f"/api/cases/{case['id']}", headers=headers("rider-b")).status_code == 404
    assert act(client, case, "handoff", session="rider-b").status_code == 404
    assert client.get("/api/cases").status_code == 401
    assert client.get("/api/agent/cases").status_code == 403
    assert act(client, case, "message", "   ").status_code == 422
    assert act(client, case, "confirm").status_code == 409


def test_fare_adjustment_is_business_idempotent_across_request_ids(client):
    case = create(client, "trip-airport", topic="fare")
    assert case["status"] == "awaiting_confirmation"
    assert case["proposal"]["amount"] == 700
    first = act(client, case, "confirm").json()
    again = act(client, case, "confirm").json()
    assert first["adjustment"] == again["adjustment"]
    assert sum(e["kind"] == "adjustment" for e in again["events"]) == 1
    assert again["status"] == "resolved"


def test_correction_invalidates_unconfirmed_proposal(client):
    case = create(client, "trip-airport", topic="fare")
    corrected = act(client, case, "message", "That is incorrect. I need a different issue reviewed.").json()
    assert corrected["proposal"] is None
    assert corrected["status"] == "needs_review"
    assert act(client, corrected, "confirm").status_code == 409


def test_handoff_evidence_and_human_resolution_preserve_history(client):
    case = create(client)
    case = act(client, case, "evidence", "The pickup pin was on the opposite side.").json()
    case = act(client, case, "handoff").json()
    assert case["handoff"]["evidence_count"] == 1
    before = len(case["messages"])
    for action, text in [("accept", ""), ("reply", "I have the pickup evidence."), ("resolve", "Reviewed the conflicting records. This demo case is closed with this explanation.")]:
        result = client.post(f"/api/agent/cases/{case['id']}/actions", headers=headers(agent=True), json={"action": action, "text": text})
        assert result.status_code == 200, result.text
    resolved = result.json()
    assert resolved["status"] == "resolved"
    assert len(resolved["messages"]) == before + 3
    assert len(resolved["evidence"]) == 1
    reopened = act(client, resolved, "handoff").json()
    assert reopened["status"] == "waiting_for_agent"
    assert any(e["kind"] == "decision" for e in reopened["events"])


def test_attachment_validation_persistence_and_access(client):
    case = create(client)
    url = f"/api/cases/{case['id']}/attachments"
    invalid = client.post(url, files={"file": ("fake.png", b"not an image", "image/png")}, headers=headers())
    assert invalid.status_code == 415
    raw = b"%PDF-1.4\nFictional evidence\n%%EOF"
    response = client.post(url, files={"file": ("receipt.pdf", raw, "application/pdf")}, headers=headers())
    assert response.status_code == 200
    evidence = response.json()["evidence"][-1]
    assert "content" not in evidence
    download = url + "/" + evidence["id"]
    assert client.get(download, headers=headers()).content == raw
    assert client.get(download, headers=headers("rider-b")).status_code == 404
    assert client.get(download, headers=headers(agent=True)).content == raw


def test_concurrent_confirm_records_one_adjustment(client):
    case = create(client, "trip-airport", topic="fare")
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(lambda _: act(client, case, "confirm"), range(3)))
    assert all(r.status_code == 200 for r in responses)
    assert len({r.json()["adjustment"]["id"] for r in responses}) == 1


def test_cases_survive_new_application_instance(tmp_path):
    path = tmp_path / "persistent.sqlite3"
    with TestClient(create_app(path)) as first:
        case = create(first)
    with TestClient(create_app(path)) as second:
        assert second.get(f"/api/cases/{case['id']}", headers=headers()).json()["description"] == case["description"]


def test_payment_explanation_does_not_create_a_refund(client):
    case = create(client, "trip-museum", topic="payment")
    assert case["status"] == "awaiting_rider"
    result = act(client, case, "close").json()
    assert result["status"] == "resolved" and result["adjustment"] is None


def test_specialist_adjustment_requires_evidence_reason_and_rider_confirmation(client):
    case = create(client)
    case = act(client, case, "handoff").json()
    path = f"/api/agent/cases/{case['id']}/actions"
    assert client.post(path, headers=headers(agent=True), json={"action": "accept"}).status_code == 200
    assert client.post(path, headers=headers(agent=True), json={"action": "propose_adjustment"}).status_code == 422
    proposed = client.post(path, headers=headers(agent=True), json={"action": "propose_adjustment", "text": "The fictional pickup evidence supports waiving the fee."}).json()
    assert proposed["status"] == "awaiting_confirmation"
    assert proposed["proposal"]["amount"] == 650
    assert proposed["adjustment"] is None
    confirmed = act(client, proposed, "confirm").json()
    assert confirmed["status"] == "resolved"
    assert confirmed["adjustment"]["amount"] == 650


def test_mismatched_topic_does_not_auto_propose_a_fare_adjustment(client):
    case = create(client, "trip-airport", topic="payment")
    assert case["status"] == "needs_review" and case["proposal"] is None


def test_new_case_request_cannot_repeat_a_completed_trip_adjustment(client):
    case = create(client, "trip-airport", topic="fare")
    completed = act(client, case, "confirm").json()
    repeated = create(client, "trip-airport", topic="fare")
    assert repeated["id"] == completed["id"]
    assert repeated["adjustment"]["id"] == completed["adjustment"]["id"]
    assert len(client.get("/api/cases", headers=headers()).json()) == 1


def test_api_prefix_works_for_built_frontend(client):
    assert client.get("/ride-api/health").json()["mode"] == "demo"
    assert len(client.get("/ride-api/api/trips").json()) == 3


def test_new_appeal_preserves_statement_and_prior_adjustment(client):
    case = create(client, "trip-airport", topic="fare")
    completed = act(client, case, "confirm").json()
    statement = "The original adjustment is recorded, but my new pickup evidence needs human review."
    result = client.post('/api/cases', headers=headers(), json=dict(trip_id='trip-airport', topic='fare', description=statement))
    assert result.status_code == 200
    appeal = result.json()
    assert appeal['id'] == completed['id']
    assert appeal['adjustment']['id'] == completed['adjustment']['id']
    assert any(m['role']=='rider' and m['content']==statement for m in appeal['messages'])
    assert appeal['status'] == 'waiting_for_agent'
    assert appeal['proposal'] is None
