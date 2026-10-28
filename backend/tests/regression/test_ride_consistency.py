"""Crash, stale-analysis, conditional command and migration regression tests."""

import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from ride_agent.api.app import create_app
from ride_agent.persistence.runtime_lock import embedded_writer_lock
from ride_agent.persistence.store import Store


def headers(key=None, revision=None, staff=False):
    return {
        "X-Demo-Session": "consistency-rider",
        "X-Request-ID": key or uuid4().hex,
        **({"If-Match": '"' + str(revision) + '"'} if revision is not None else {}),
        **({"X-Demo-Agent": "local-demo-agent"} if staff else {}),
    }


def create(client, trip="trip-river", key=None):
    response = client.post(
        "/api/cases",
        headers=headers(key),
        json=dict(
            trip_id=trip,
            description="My pickup entrance is CEDAR. Please review the fare.",
            topic="cancellation" if trip == "trip-river" else "fare",
        ),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_conditional_changes_require_review_of_new_state_but_replay_is_exact(tmp_path):
    with TestClient(create_app(tmp_path / "db.sqlite3")) as client:
        case = create(client, "trip-airport")
        path = f"/api/cases/{case['id']}/actions"
        before = case["revision"]
        note = client.post(
            path,
            headers=headers(revision=before),
            json=dict(action="evidence", text="Please also review my receipt."),
        )
        assert note.status_code == 200 and note.json()["revision"] == before + 1
        stale = client.post(path, headers=headers(revision=before), json=dict(action="confirm"))
        assert stale.status_code == 412
        assert client.get("/api/cases/" + case["id"], headers=headers()).json()["adjustment"] is None
        accepted = client.post(
            path,
            headers=headers("confirm-once", before + 1),
            json=dict(action="confirm"),
        )
        assert accepted.status_code == 200
        replay = client.post(
            path,
            headers=headers("confirm-once", before + 1),
            json=dict(action="confirm"),
        )
        assert replay.json() == accepted.json()
        assert accepted.headers["etag"] == '"' + str(accepted.json()["revision"]) + '"'
        assert sum((e["kind"] == "adjustment" for e in replay.json()["events"])) == 1


def test_stale_analysis_cannot_overwrite_staff_context_or_publish_memory(tmp_path):
    with TestClient(create_app(tmp_path / "db.sqlite3")) as client:
        case = create(client)
        path = f"/api/cases/{case['id']}/actions"
        client.post(path, headers=headers(), json=dict(action="handoff"))
        staff_path = f"/api/agent/cases/{case['id']}/actions"
        client.post(staff_path, headers=headers(staff=True), json=dict(action="accept")).json()
        engine = client.app.state.service.engine
        uid = hashlib.sha256(json.dumps(["consistency-rider", case["id"], "trip-river"]).encode()).hexdigest()
        collection = engine.chroma.get_collection("ride_memory_profiles_v1", embedding_function=engine.hash_embedding)
        profile_before = collection.get(where={"user_id": uid})
        with client.app.state.store.connect() as db:
            outbox_before = db.execute("SELECT COUNT(*) FROM projection_outbox").fetchone()[0]
        ready = asyncio.Event()
        release = asyncio.Event()
        original = engine.run

        async def delayed(*args, **kwargs):
            result = await original(*args, **kwargs)
            ready.set()
            await release.wait()
            return result

        engine.run = delayed
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(
                lambda: client.post(
                    path,
                    headers=headers("slow-analysis"),
                    json=dict(action="message", text="My replacement landmark is STALE-DELTA."),
                )
            )
            client.portal.call(asyncio.wait_for, ready.wait(), 20)
            reply = client.post(
                staff_path,
                headers=headers(staff=True),
                json=dict(
                    action="reply",
                    text="STAFF-CONTEXT is newer than the analysis snapshot.",
                ),
            )
            assert reply.status_code == 200
            client.portal.call(release.set)
            stale = pending.result(timeout=15)
        assert stale.status_code == 409
        latest = client.get("/api/cases/" + case["id"], headers=headers()).json()
        assert latest["revision"] == reply.json()["revision"]
        assert any(("STAFF-CONTEXT" in m["content"] for m in latest["messages"]))
        assert not any(("STALE-DELTA" in m["content"] for m in latest["messages"]))
        assert collection.get(where={"user_id": uid}) == profile_before
        with client.app.state.store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM projection_outbox").fetchone()[0] == outbox_before
            assert db.execute("SELECT COUNT(*) FROM receipts WHERE key=?", ("slow-analysis",)).fetchone()[0] == 0
        retry = client.post(
            path,
            headers=headers("slow-analysis"),
            json=dict(action="message", text="My replacement landmark is STALE-DELTA."),
        )
        assert retry.status_code == 200 and "STAFF-CONTEXT" in "\n".join(retry.json()["memory_snapshot"]["working"])


def test_case_receipt_and_memory_outbox_survive_process_death_after_commit(tmp_path):
    path = tmp_path / "crash.sqlite3"
    code = (
        "\nimport os\nfrom fastapi.testclient import TestClient\nfrom "
        "ride_agent.api.app import create_app\napp=create_app(os.envir"
        "on['CRASH_DB'])\nwith TestClient(app) as client:\n    "
        "original=app.state.store.write\n    def "
        "crash(*args,**kwargs):\n        original(*args,**kwargs)\n    "
        "    os._exit(93)\n    app.state.store.write=crash\n    "
        "client.post('/api/cases',headers={'X-Demo-Session':'consiste"
        "ncy-rider','X-Request-ID':'crash-create'},json={'trip_id':'t"
        "rip-river','description':'My pickup entrance is CEDAR. "
        "Please review the fare.','topic':'cancellation'})\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={
            **os.environ,
            "CRASH_DB": str(path),
            "RIDERECALL_AGENT_RIDE_MODEL_MODE": "demo",
        },
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
        timeout=40,
    )
    assert result.returncode == 93, result.stderr.decode()[-1500:]
    store = Store(path)
    assert len(store.list("consistency-rider")) == 1
    assert store.projection_status()["pending"] > 0
    with TestClient(create_app(path)) as client:
        calls = len(client.app.state.service.engine.client.calls)
        restored = create(client, key="crash-create")
        assert len(client.app.state.service.engine.client.calls) == calls
        assert store.projection_status()["pending"] == 0
        assert client.get("/readyz").status_code == 200
        uid = hashlib.sha256(json.dumps(["consistency-rider", restored["id"], "trip-river"]).encode()).hexdigest()
        records = client.app.state.service.engine.chroma.get_collection(
            "ride_memory_profiles_v1",
            embedding_function=client.app.state.service.engine.hash_embedding,
        ).get(where={"user_id": uid})
        assert len(records["ids"]) == 1


def test_memory_projection_retries_without_reverting_committed_case(tmp_path, monkeypatch):
    path = tmp_path / "retry.sqlite3"
    with TestClient(create_app(path)) as client:
        worker = client.app.state.projections
        real = worker.chroma.get_collection

        def unavailable(*args, **kwargs):
            raise OSError("Injected unavailable derived index")

        with monkeypatch.context() as patch:
            patch.setattr(worker.chroma, "get_collection", unavailable)
            case = create(client)
            assert client.app.state.store.get(case["id"])["revision"] == case["revision"]
            assert client.app.state.store.projection_status()["retrying"] == 1
            assert client.get("/readyz").status_code == 503
        with client.app.state.store.connect() as db:
            db.execute("UPDATE projection_outbox SET available_at=0 WHERE completed_at IS NULL")
        client.portal.call(worker.flush)
        assert client.app.state.store.projection_status()["pending"] == 0
        assert client.get("/readyz").status_code == 200
        assert (
            real(
                "ride_memory_profiles_v1",
                embedding_function=client.app.state.service.engine.hash_embedding,
            ).count()
            == 1
        )


def test_migration_preserves_legacy_data_and_rejects_future_schema(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    legacy = dict(
        id="KU-LEGACY",
        owner="rider",
        updated_at="2026-10-21T00:00:00+00:00",
        events=[],
        messages=[],
    )
    with sqlite3.connect(path) as db:
        db.execute(("CREATE TABLE cases(id TEXT PRIMARY KEY,owner TEXT NOT NULL,data TEXT NOT NULL)"))
        db.execute(
            "INSERT INTO cases VALUES(?,?,?)",
            (legacy["id"], legacy["owner"], json.dumps(legacy)),
        )
    store = Store(path)
    assert store.get(legacy["id"])["revision"] == 0
    snapshot = store.get(legacy["id"])
    with store.connect() as db:
        store.save(db, snapshot)
    assert store.get(legacy["id"])["revision"] == 1
    with pytest.raises(HTTPException, match="409"):
        with store.connect() as db:
            store.save(db, deepcopy(legacy))
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=999")
    with pytest.raises(RuntimeError, match="refusing to downgrade"):
        Store(path)


def test_embedded_writer_is_exclusive_across_processes_and_released(tmp_path):
    path = tmp_path / "one.sqlite3"
    code = (
        "from ride_agent.persistence.runtime_lock import "
        "embedded_writer_lock; import sys\nwith "
        "embedded_writer_lock(sys.argv[1]): "
        "print('acquired')"
    )
    with embedded_writer_lock(path):
        conflict = subprocess.run(
            [sys.executable, "-c", code, str(path)],
            capture_output=True,
            cwd=Path(__file__).resolve().parents[2],
            timeout=10,
        )
        assert conflict.returncode != 0 and b"one worker" in conflict.stderr
    recovered = subprocess.run(
        [sys.executable, "-c", code, str(path)],
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
        timeout=10,
    )
    assert recovered.returncode == 0


def test_missing_primary_database_is_not_bootstrapped_over_retained_indexes(tmp_path):
    database = tmp_path / "retained.sqlite3"
    database.with_suffix(".vectors").mkdir()
    with pytest.raises(RuntimeError, match="Restore the database"):
        Store(database)
    assert not database.exists()


def test_case_database_and_embedded_index_are_private_on_posix(tmp_path):
    if os.name != "posix":
        pytest.skip("POSIX permissions are not Windows ACLs")
    database = tmp_path / "private.sqlite3"
    with TestClient(create_app(database)) as client:
        create(client)
        assert database.stat().st_mode & 63 == 0
        assert database.with_suffix(".vectors").stat().st_mode & 63 == 0
