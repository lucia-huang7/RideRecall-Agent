"""Legacy migration, immutable binary storage and replay-size regression checks."""

import base64
import hashlib
import json
import os
import sqlite3
import zlib
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from ride_agent.api.app import create_app
from ride_agent.persistence.persistence import decode_receipt
from ride_agent.persistence.store import Store


def headers(key=None):
    return {"X-Demo-Session": "blob-rider", "X-Request-ID": key or uuid4().hex}


def create(client):
    result = client.post(
        "/api/cases",
        headers=headers(),
        json=dict(
            trip_id="trip-river",
            description="Please review my pickup and cancellation fee.",
            topic="cancellation",
        ),
    )
    assert result.status_code == 200, result.text
    return result.json()


def legacy(path, case):
    with sqlite3.connect(path) as db:
        db.executescript(
            (
                "\n        CREATE TABLE cases(id TEXT PRIMARY KEY,owner TEXT "
                "NOT NULL,data TEXT NOT NULL);\n        CREATE TABLE "
                "receipts(owner TEXT,key TEXT,fingerprint TEXT,response "
                "TEXT,PRIMARY KEY(owner,key));\n        CREATE TABLE "
                "projection_outbox(sequence INTEGER PRIMARY KEY "
                "AUTOINCREMENT,id TEXT UNIQUE NOT NULL,case_id TEXT NOT NULL "
                "REFERENCES cases(id) ON DELETE CASCADE,payload TEXT NOT "
                "NULL,created_at REAL NOT NULL,available_at REAL NOT "
                "NULL,attempts INTEGER NOT NULL DEFAULT 0,error_code "
                "TEXT,completed_at REAL);\n        PRAGMA user_version=1;\n    "
                "    "
            )
        )
        raw = json.dumps(case)
        db.execute("INSERT INTO cases VALUES(?,?,?)", (case["id"], case["owner"], raw))
        db.execute(
            "INSERT INTO receipts VALUES(?,?,?,?)",
            (case["owner"], "old-upload", "fingerprint", raw),
        )


def test_migration_preserves_bytes_deduplicates_within_case_and_replays_public_payload(
    tmp_path,
):
    binary = b"%PDF-1.4\nOriginal immutable receipt\n%%EOF"
    evidence = [
        dict(
            id="file-" + str(i),
            filename="receipt.pdf",
            content_type="application/pdf",
            content=base64.b64encode(binary).decode(),
        )
        for i in range(2)
    ]
    original = dict(
        id="KU-OLD",
        owner="blob-rider",
        revision=7,
        trip_id="trip-river",
        updated_at="2026-10-22T00:00:00Z",
        messages=[],
        events=[],
        evidence=evidence,
    )
    path = tmp_path / "legacy.sqlite3"
    legacy(path, original)
    store = Store(path)
    case = store.get("KU-OLD")
    assert case["revision"] == 8
    assert all(("content" not in item for item in case["evidence"]))
    assert store.attachment("KU-OLD", "file-0")["data"] == binary
    expected = deepcopy(original)
    for item in expected["evidence"]:
        item.pop("content")
    assert store.replay("blob-rider", "old-upload", "fingerprint") == expected
    with store.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert db.execute("SELECT COUNT(*) FROM attachment_blobs").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == 2
        assert db.execute("SELECT encoding,typeof(response) FROM receipts").fetchone() == ("zlib-json", "blob")
    assert Store(path).get("KU-OLD")["revision"] == 8


def test_invalid_legacy_binary_rolls_back_the_whole_migration(tmp_path):
    path = tmp_path / "invalid.sqlite3"
    case = dict(
        id="KU-BAD",
        owner="blob-rider",
        updated_at="now",
        trip_id="trip-river",
        evidence=[
            dict(id="good", filename="a.pdf", content="aGVsbG8="),
            dict(id="bad", filename="b.pdf", content="!invalid!"),
        ],
    )
    legacy(path, case)
    with pytest.raises(RuntimeError, match="migration rolled back"):
        Store(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert json.loads(db.execute("SELECT data FROM cases").fetchone()[0]) == case
        assert "encoding" not in [r[1] for r in db.execute("PRAGMA table_info(receipts)")]
        assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='attachments'").fetchone()[0] == 0


def test_binary_is_not_duplicated_in_case_or_subsequent_receipts(tmp_path):
    with TestClient(create_app(tmp_path / "blobs.sqlite3")) as client:
        case = create(client)
        path = f"/api/cases/{case['id']}/attachments"
        raw = b"\x89PNG\r\n\x1a\n" + os.urandom(1024 * 1024)
        first = client.post(
            path,
            headers=headers("one-file"),
            files={"file": ("receipt.png", raw, "image/png")},
        )
        assert first.status_code == 200
        assert (
            client.post(
                path,
                headers=headers("one-file"),
                files={"file": ("receipt.png", raw, "image/png")},
            ).json()
            == first.json()
        )
        second = client.post(
            path,
            headers=headers(),
            files={"file": ("same-bytes.png", raw, "image/png")},
        )
        assert second.status_code == 200
        for i in range(8):
            response = client.post(
                f"/api/cases/{case['id']}/actions",
                headers=headers(),
                json=dict(action="evidence", text=f"Additional evidence note {i}"),
            )
            assert response.status_code == 200
        store = client.app.state.store
        with store.connect() as db:
            assert db.execute("SELECT SUM(length(data)) FROM attachment_blobs").fetchone()[0] == len(raw)
            assert db.execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == 2
            assert db.execute("SELECT SUM(length(response)) FROM receipts").fetchone()[0] < len(raw) // 2
            assert db.execute("SELECT length(data) FROM cases WHERE id=?", (case["id"],)).fetchone()[0] < 100000
        evidence = first.json()["evidence"][0]
        assert evidence["size_bytes"] == len(raw) and evidence["sha256"] == hashlib.sha256(raw).hexdigest()
        download = client.get(path + "/" + evidence["id"], headers=headers())
        assert download.content == raw and download.headers["etag"] == '"' + evidence["sha256"] + '"'


def test_failed_case_save_does_not_leave_blob_or_receipt(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path / "rollback.sqlite3")) as client:
        case = create(client)
        store = client.app.state.store

        def fail(*args, **kwargs):
            raise sqlite3.OperationalError("Injected storage failure after blob insertion")

        with monkeypatch.context() as patch:
            patch.setattr(store, "save", fail)
            response = client.post(
                f"/api/cases/{case['id']}/attachments",
                headers=headers("failed-upload"),
                files={"file": ("a.pdf", b"%PDF-fixture", "application/pdf")},
            )
        assert response.status_code == 503
        assert store.get(case["id"])["evidence"] == []
        with store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM attachment_blobs").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM receipts WHERE key=?", ("failed-upload",)).fetchone()[0] == 0


def test_attachment_count_budget_keeps_exact_replay_available(tmp_path):
    with TestClient(create_app(tmp_path / "quota.sqlite3")) as client:
        case = create(client)
        path = f"/api/cases/{case['id']}/attachments"
        first = None
        for i in range(10):
            result = client.post(
                path,
                headers=headers("upload-" + str(i)),
                files={"file": ("a.pdf", b"%PDF-shared", "application/pdf")},
            )
            assert result.status_code == 200
            if i == 0:
                first = result.json()
        assert (
            client.post(
                path,
                headers=headers(),
                files={"file": ("a.pdf", b"%PDF-shared", "application/pdf")},
            ).status_code
            == 409
        )
        assert (
            client.post(
                path,
                headers=headers("upload-0"),
                files={"file": ("a.pdf", b"%PDF-shared", "application/pdf")},
            ).json()
            == first
        )
        with client.app.state.store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM attachment_blobs").fetchone()[0] == 1


def test_unique_binary_budget_and_corruption_are_enforced(tmp_path):
    with TestClient(create_app(tmp_path / "bytes.sqlite3")) as client:
        case = create(client)
        path = f"/api/cases/{case['id']}/attachments"
        first = None
        for i in range(5):
            raw = b"%PDF-" + bytes([i]) + b"x" * (3 * 1024 * 1024 - 6)
            result = client.post(
                path,
                headers=headers(),
                files={"file": ("a.pdf", raw, "application/pdf")},
            )
            assert result.status_code == 200, result.text
            if i == 0:
                first = result.json()["evidence"][0]
        assert (
            client.post(
                path,
                headers=headers(),
                files={"file": ("extra.pdf", b"%PDF-extra", "application/pdf")},
            ).status_code
            == 409
        )
        with client.app.state.store.connect() as db:
            db.execute(
                "UPDATE attachment_blobs SET data=? WHERE sha256=?",
                (b"corrupt", first["sha256"]),
            )
        assert client.get(path + "/" + first["id"], headers=headers()).status_code == 503


def test_chat_budget_does_not_block_handoff_or_publish_a_failed_analysis(tmp_path):
    with TestClient(create_app(tmp_path / "chat-limit.sqlite3")) as client:
        case = create(client)
        store = client.app.state.store

        def seed(db):
            record = store.load(db, case["id"])
            record["messages"] += [dict(role="rider", content="Prior conversation " + str(i)) for i in range(49)]
            store.save(db, record)
            return record

        store.write("fixture", "seed", "seed", seed)
        calls = len(client.app.state.service.engine.client.calls)
        path = f"/api/cases/{case['id']}/actions"
        assert (
            client.post(
                path,
                headers=headers(),
                json=dict(action="message", text="Please preserve this history."),
            ).status_code
            == 409
        )
        assert len(client.app.state.service.engine.client.calls) == calls
        handoff = client.post(path, headers=headers(), json=dict(action="handoff"))
        assert handoff.status_code == 200 and handoff.json()["status"] == "waiting_for_agent"


def test_receipt_decoder_rejects_corrupt_or_unknown_formats():
    with pytest.raises(sqlite3.DatabaseError):
        decode_receipt(b"not-zlib", "zlib-json")
    with pytest.raises(sqlite3.DatabaseError):
        decode_receipt(zlib.compress(b"{}") + b"trailing", "zlib-json")
    with pytest.raises(sqlite3.DatabaseError):
        decode_receipt(b"{}", "unknown-format")
