"""Behavioral regressions for original memory reuse in the ride application."""
from collections import Counter
from fastapi.testclient import TestClient
from memory.conversation_memory import MemoryManager
from mobility.app import create_app
from tests.test_ride_engine import headers, create


def send(client, case, text, key=None):
    result = client.post(f"/api/cases/{case['id']}/actions", headers=headers(key), json=dict(action="message", text=text))
    assert result.status_code == 200, result.text
    return result.json()


def test_original_memory_compresses_restores_and_isolates(tmp_path, monkeypatch):
    calls = Counter()
    for name in ("get_context", "commit_turn", "_compress", "_store_episodic", "_update_profile"):
        original = getattr(MemoryManager, name)
        def wrap(original, name):
            async def spy(*args, **kwargs):
                calls[name] += 1
                return await original(*args, **kwargs)
            return spy
        monkeypatch.setattr(MemoryManager, name, wrap(original, name))
    path = tmp_path / "memory.sqlite3"
    with TestClient(create_app(path)) as client:
        case = create(client, text="The driver never arrived. My landmark is CEDAR-17. I dispute $6.50.")
        case = send(client, case, "Correction: I dispute $5.25 instead.")
        for index in range(9):
            case = send(client, case, f"Keep my case open, evidence update {index}.")
        run = case["engine_runs"][-1]
        assert run["memory"]["summary"] and run["memory"]["episodic_count"]
        assert run["memory"]["state"]["entities"]["amount"] == ["$5.25"]
        case = send(client, case, "Please retain my pickup landmark.", "memory-retry")
        assert "CEDAR-17" in case["engine_runs"][-1]["response"]
        assert send(client, case, "Please retain my pickup landmark.", "memory-retry")["memory_snapshot"] == case["memory_snapshot"]
        other = create(client, text="What landmark did I provide?", trip="trip-airport", topic="fare")
        assert "CEDAR-17" not in other["engine_runs"][-1]["response"]
        assert other["engine_runs"][-1]["memory"]["episodic_count"] == 0
    with TestClient(create_app(path)) as client:
        case = send(client, case, "Please connect me to a human representative.")
        run = case["engine_runs"][-1]
        assert "CEDAR-17" in run["response"]
        assert run["agents"] == ["general"]
        assert run["memory"]["state"]["entities"]["amount"] == ["$5.25"]
    assert len(calls) == 5 and all(calls.values())


def test_summary_failure_preserves_claims_and_trip_link(tmp_path, monkeypatch):
    from mobility.memory import MemoryModel
    with TestClient(create_app(tmp_path / "failure.sqlite3")) as client:
        case = create(client, text="My pickup landmark is CEDAR-17. I dispute $6.50.")
        async def fail(*args, **kwargs):
            raise RuntimeError("Summary model unavailable")
        monkeypatch.setattr(MemoryModel, "create", fail)
        for index in range(8):
            case = send(client, case, f"Keep this dispute open. Evidence note {index}.")
        assert len(case["memory_snapshot"]["working"]) >= 15
        case = send(client, case, "Use trip-airport instead.")
        assert case["trip_id"] == "trip-river"
        assert case["engine_runs"][-1]["memory"]["state"]["entities"]["order_id"] == ["trip-river"]
        assert "CEDAR-17" in " ".join(case["memory_snapshot"]["working"])


def test_handoff_evidence_and_staff_reply_enter_original_memory(tmp_path):
    with TestClient(create_app(tmp_path / "handoff.sqlite3")) as client:
        case = create(client)
        result = client.post(f"/api/cases/{case['id']}/actions", headers=headers(), json=dict(action="evidence", text="CEDAR-17 pickup pin was across the street."))
        assert result.status_code == 200
        case = send(client, result.json(), "Please connect me to a human representative.")
        assert "CEDAR-17" in case["engine_runs"][-1]["response"]
        assert case["handoff"]["evidence_count"] == 1
        for action, text in [("accept", ""), ("reply", "STAFF-REVIEW-42: I have reviewed your attached pickup evidence.")]:
            result = client.post(f"/api/agent/cases/{case['id']}/actions", headers=headers(agent=True), json=dict(action=action,text=text))
            assert result.status_code == 200
        case = send(client, result.json(), "Keep this conversation attached.")
        assert "STAFF-REVIEW-42" in " ".join(case["memory_snapshot"]["working"])
        assert case["adjustment"] is None


def test_user_isolation_and_claims_do_not_change_billing_facts(tmp_path):
    with TestClient(create_app(tmp_path / "claims.sqlite3")) as client:
        case = create(client, text="My private landmark is CEDAR-17. I dispute $6.50.")
        case = send(client, case, "Correction: the amount I dispute is $999.00.")
        run = case["engine_runs"][-1]
        assert run["memory"]["state"]["entities"]["amount"] == ["$999.00"]
        assert case["adjustment"] is None
        assert all("999" not in fact["text"] for fact in case["facts"])
        result = client.post('/api/cases', headers={**headers(), 'X-Demo-Session':'another-rider'}, json=dict(trip_id='trip-river', topic='cancellation', description='What landmark did I provide?'))
        assert result.status_code == 200
        assert "CEDAR-17" not in result.json()["engine_runs"][-1]["response"]
        assert result.json()["engine_runs"][-1]["memory"]["episodic_count"] == 0
