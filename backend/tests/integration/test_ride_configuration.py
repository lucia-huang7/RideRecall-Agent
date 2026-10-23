"""Control-plane transactions, crash recovery and fair read/write exclusion."""

import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from ride_agent.api.app import create_app
from ride_agent.ops.configuration import ConfigurationGate


def headers(key=None):
    return {
        "X-Demo-Session": "configuration-rider",
        "X-Demo-Agent": "local-demo-agent",
        "X-Request-ID": key or uuid4().hex,
    }


def document():
    return dict(
        topic="fare",
        document_id="supplemental-recovery",
        title="Independent recovery evidence",
        content=("CONTROL-RECOVERY-726: Preserve disputed payment evidence and ask for missing receipts only."),
    )


def test_policy_commit_survives_failed_index_and_restarts_without_duplicate_command(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"
    with TestClient(create_app(path)) as client:
        worker = client.app.state.configuration

        async def unavailable(payload):
            raise OSError("Injected projection failure")

        monkeypatch.setattr(worker, "apply", unavailable)
        result = client.post("/api/agent/knowledge", headers=headers("policy-once"), json=document())
        assert result.status_code == 200 and result.json()["status"] == "accepted"
        assert (
            client.post("/api/agent/knowledge", headers=headers("policy-once"), json=document()).json() == result.json()
        )
        status = client.get("/api/agent/configuration", headers=headers()).json()
        assert status["pending"] == 1 and status["retrying"] == 1
        assert client.get("/readyz").status_code == 503
        calls = len(client.app.state.service.engine.client.calls)
        attempt = client.post(
            "/api/cases",
            headers=headers(),
            json=dict(trip_id="trip-airport", description="Please review my fare and receipt."),
        )
        assert attempt.status_code == 503 and len(client.app.state.service.engine.client.calls) == calls
        assert any(
            (
                row["document_id"] == document()["document_id"]
                for row in client.get("/api/agent/knowledge/fare", headers=headers()).json()["source_documents"]
            )
        )
    with sqlite3.connect(path) as db:
        db.execute("UPDATE configuration_outbox SET available_at=0")
    with TestClient(create_app(path)) as client:
        assert client.get("/readyz").status_code == 200
        assert client.get("/api/agent/configuration", headers=headers()).json()["pending"] == 0
        result = client.get("/api/agent/knowledge/fare", headers=headers()).json()
        assert any(("CONTROL-RECOVERY-726" in row["content"] for row in result["documents"]))
        assert (
            client.app.state.store.replay(
                "agent",
                "policy-once",
                hashlib.sha256(json.dumps(["knowledge", document()], sort_keys=True).encode()).hexdigest(),
            )["status"]
            == "accepted"
        )
        with client.app.state.store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM configuration_outbox").fetchone()[0] == 1


@pytest.mark.parametrize("kind", ["policy", "skill"])
def test_receipt_failure_rolls_back_content_and_never_changes_runtime(tmp_path, monkeypatch, kind):
    with TestClient(create_app(tmp_path / "db.sqlite3")) as client:
        service = client.app.state.service
        original = service.skill_file.read_text()
        documents = service.engine.knowledge["fare"]._collection.get(include=["documents"])

        def full_disk(value):
            raise sqlite3.OperationalError("Injected disk-full receipt failure")

        monkeypatch.setattr("ride_agent.persistence.store.encode_receipt", full_disk)
        endpoint = "/api/agent/knowledge" if kind == "policy" else "/api/agent/skills"
        payload = (
            document()
            if kind == "policy"
            else dict(content=original + "\n- CONTROL-UNCOMMITTED: Keep original assertions.\n")
        )
        result = client.post(endpoint, headers=headers(), json=payload)
        assert result.status_code == 503
        assert service.skill_file.read_text() == original
        assert service.engine.knowledge["fare"]._collection.get(include=["documents"]) == documents
        with client.app.state.store.connect() as db:
            for table in ("configuration_outbox", "policy_sources", "receipts"):
                assert db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM runtime_settings WHERE key='ride_skill'").fetchone()[0] == 0


def test_skill_failure_keeps_committed_content_pending_and_recovers_in_order(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"
    with TestClient(create_app(path)) as client:
        original = client.app.state.service.skill_file.read_text()

        def unavailable(*args):
            raise OSError("Injected file write failure")

        monkeypatch.setattr("ride_agent.ops.configuration.atomic_skill", unavailable)
        receipts = []
        for marker in ("FIRST", "LATEST"):
            result = client.post(
                "/api/agent/skills",
                headers=headers(),
                json=dict(content=original + "\n- CONTROL-" + marker + ": Preserve context.\n"),
            )
            assert result.status_code == 200
            receipts.append(result.json()["configuration_sequence"])
        assert receipts[1] > receipts[0]
        assert client.app.state.service.skill_file.read_text() == original
        assert client.get("/api/agent/configuration", headers=headers()).json()["pending"] == 2
    monkeypatch.undo()
    with sqlite3.connect(path) as db:
        db.execute("UPDATE configuration_outbox SET available_at=0")
    with TestClient(create_app(path)) as client:
        content = client.get("/api/agent/skills", headers=headers()).json()["editable_content"]
        assert "CONTROL-LATEST" in content and "CONTROL-FIRST" not in content
        assert client.get("/api/agent/configuration", headers=headers()).json()["pending"] == 0


def test_process_exit_immediately_after_management_commit_recovers_without_repeating_command(
    tmp_path,
):
    path = tmp_path / "crash.sqlite3"
    code = (
        "\nimport os\nfrom fastapi.testclient import TestClient\nfrom "
        "ride_agent.api.app import create_app\nfrom "
        "ride_agent.persistence.store import "
        "Store\nreal=Store.write\ndef crash(self,*args,**kwargs):\n    "
        "value=real(self,*args,**kwargs)\n    "
        "os._exit(94)\nStore.write=crash\nwith "
        "TestClient(create_app(os.environ['CRASH_DB'])) as client:\n  "
        "  client.post('/api/agent/knowledge',headers={'X-Demo-Agent'"
        ":'local-demo-agent','X-Request-ID':'crash-config'},json=dict"
        "(topic='fare',document_id='crash-source',title='Crash "
        "recovery',content='CRASH-CONTROL-64: Preserve "
        "evidence.'))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "CRASH_DB": str(path)},
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
        timeout=40,
    )
    assert result.returncode == 94, result.stderr.decode()[-2000:]
    with TestClient(create_app(path)) as client:
        result = client.get("/api/agent/knowledge/fare", headers=headers()).json()
        assert any(("CRASH-CONTROL-64" in item["content"] for item in result["documents"]))
        assert client.get("/api/agent/configuration", headers=headers()).json()["pending"] == 0
        with client.app.state.store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM configuration_outbox").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_configuration_gate_allows_parallel_readers_but_a_waiting_writer_precedes_new_readers():
    gate = ConfigurationGate()
    events = []
    release = asyncio.Event()

    async def first():
        async with gate.read():
            events.append("first")
            await release.wait()

    async def reader():
        async with gate.read():
            events.append("second")

    async def writer():
        async with gate.write():
            events.append("writer")

    first_task = asyncio.create_task(first())
    await asyncio.sleep(0)
    async with gate.read():
        assert gate.readers == 2
    writer_task = asyncio.create_task(writer())
    await asyncio.sleep(0)
    reader_task = asyncio.create_task(reader())
    await asyncio.sleep(0)
    assert events == ["first"]
    release.set()
    await asyncio.wait_for(asyncio.gather(first_task, writer_task, reader_task), 2)
    assert events == ["first", "writer", "second"]
    assert gate.readers == 0 and (not gate.writer)


@pytest.mark.asyncio
async def test_cancelled_waiting_writer_does_not_strand_readers():
    gate = ConfigurationGate()
    async with gate.read():

        async def wait():
            async with gate.write():
                raise AssertionError("Must not acquire yet")

        task = asyncio.create_task(wait())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert gate.waiting_writers == 0
        async with gate.read():
            assert gate.readers == 2
