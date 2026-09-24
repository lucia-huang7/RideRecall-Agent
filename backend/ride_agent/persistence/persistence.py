"""Schema migrations, bounded receipt decoding and case-scoped immutable blobs."""

import base64
import hashlib
import json
import sqlite3
import time
import zlib

from fastapi import HTTPException

from ride_agent.persistence.case_index import migrate_index

SCHEMA_VERSION = 4
MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
MAX_RECEIPT_BYTES = 64 * 1024 * 1024
SAFE_MEDIA = {"image/png", "image/jpeg", "application/pdf", "application/octet-stream"}


def public_receipt(value):
    if isinstance(value, dict) and "trip_id" in value and isinstance(value.get("evidence"), list):
        value = {
            **value,
            "evidence": [{k: v for k, v in item.items() if k != "content"} for item in value["evidence"]],
        }
    return value


def encode_receipt(value):
    raw = json.dumps(public_receipt(value), separators=(",", ":")).encode()
    if len(raw) > MAX_RECEIPT_BYTES:
        raise HTTPException(
            409,
            ("This case exceeds the response storage budget. Request workspace assistance."),
        )
    return zlib.compress(raw, level=6)


def decode_receipt(value, encoding):
    if encoding == "json":
        return json.loads(value)
    if encoding != "zlib-json":
        raise sqlite3.DatabaseError("Unsupported receipt encoding")
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(value, MAX_RECEIPT_BYTES + 1)
    except zlib.error:
        raise sqlite3.DatabaseError("Invalid receipt data") from None
    if len(raw) > MAX_RECEIPT_BYTES or not decoder.eof or decoder.unused_data:
        raise sqlite3.DatabaseError("Receipt decoding limit or integrity failure")
    return json.loads(raw)


def put_attachment(db, case_id, evidence_id, media, raw, enforce_limits=True):
    digest = hashlib.sha256(raw).hexdigest()
    if enforce_limits:
        count = db.execute("SELECT COUNT(*) FROM attachments WHERE case_id=?", (case_id,)).fetchone()[0]
        used = db.execute(
            "SELECT COALESCE(SUM(size_bytes),0) FROM attachment_blobs WHERE case_id=?",
            (case_id,),
        ).fetchone()[0]
        duplicate = db.execute(
            "SELECT 1 FROM attachment_blobs WHERE case_id=? AND sha256=?",
            (case_id, digest),
        ).fetchone()
        if count >= MAX_ATTACHMENTS:
            raise HTTPException(
                409,
                ("This case already has ten attachments. Ask your specialist which evidence is needed."),
            )
        if used + (0 if duplicate else len(raw)) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(409, "This case has reached its 15 MB attachment budget.")
    media = media if media in SAFE_MEDIA else "application/octet-stream"
    db.execute(
        ("INSERT OR IGNORE INTO attachment_blobs(case_id,sha256,size_bytes,data) VALUES(?,?,?,?)"),
        (case_id, digest, len(raw), raw),
    )
    db.execute(
        ("INSERT INTO attachments(case_id,evidence_id,blob_sha256,content_type,created_at) VALUES(?,?,?,?,?)"),
        (case_id, evidence_id, digest, media, time.time()),
    )
    return dict(sha256=digest, size_bytes=len(raw), content_type=media)


def get_attachment(db, case_id, evidence_id):
    row = db.execute(
        (
            "SELECT b.data,a.content_type,b.sha256,b.size_bytes FROM "
            "attachments a\n        JOIN attachment_blobs b ON "
            "b.case_id=a.case_id AND b.sha256=a.blob_sha256\n        "
            "WHERE a.case_id=? AND "
            "a.evidence_id=?"
        ),
        (case_id, evidence_id),
    ).fetchone()
    if row is None:
        return None
    if len(row[0]) != row[3] or hashlib.sha256(row[0]).hexdigest() != row[2]:
        raise sqlite3.DatabaseError("Attachment integrity verification failed")
    return dict(data=row[0], content_type=row[1], sha256=row[2], size_bytes=row[3])


def migrate(db):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError("Database schema is newer than this application; refusing to downgrade")
    db.execute(("CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY,owner TEXT NOT NULL,data TEXT NOT NULL)"))
    db.execute("CREATE INDEX IF NOT EXISTS cases_owner ON cases(owner)")
    db.execute(
        (
            "CREATE TABLE IF NOT EXISTS receipts(owner TEXT,key "
            "TEXT,fingerprint TEXT,response TEXT,PRIMARY "
            "KEY(owner,key))"
        )
    )
    if version < 1:
        db.execute(
            (
                "CREATE TABLE projection_outbox(\n            sequence "
                "INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,\n "
                "           case_id TEXT NOT NULL REFERENCES cases(id) ON "
                "DELETE CASCADE,\n            payload TEXT NOT "
                "NULL,created_at REAL NOT NULL,available_at REAL NOT NULL,\n  "
                "          attempts INTEGER NOT NULL DEFAULT 0,error_code "
                "TEXT,completed_at "
                "REAL)"
            )
        )
        db.execute(("CREATE INDEX projection_pending ON projection_outbox(completed_at,sequence)"))
    if version < 2:
        db.execute(
            (
                "CREATE TABLE attachment_blobs(\n            case_id TEXT NOT "
                "NULL REFERENCES cases(id) ON DELETE CASCADE,\n            "
                "sha256 TEXT NOT NULL,size_bytes INTEGER NOT NULL "
                "CHECK(size_bytes>=0),data BLOB NOT NULL,\n            "
                "PRIMARY KEY(case_id,sha256))"
            )
        )
        db.execute(
            (
                "CREATE TABLE attachments(\n            case_id TEXT NOT NULL "
                "REFERENCES cases(id) ON DELETE CASCADE,evidence_id TEXT NOT "
                "NULL,\n            blob_sha256 TEXT NOT NULL,content_type "
                "TEXT NOT NULL,created_at REAL NOT NULL,\n            PRIMARY "
                "KEY(case_id,evidence_id),FOREIGN KEY(case_id,blob_sha256)\n  "
                "          REFERENCES attachment_blobs(case_id,sha256) ON "
                "DELETE CASCADE)"
            )
        )
        db.execute("ALTER TABLE receipts ADD COLUMN encoding TEXT NOT NULL DEFAULT 'json'")
        for case_id, raw in db.execute("SELECT id,data FROM cases ORDER BY id"):
            case = json.loads(raw)
            changed = False
            for evidence in case.get("evidence", []):
                if "content" not in evidence:
                    continue
                try:
                    binary = base64.b64decode(evidence["content"], validate=True)
                except Exception:
                    raise RuntimeError(
                        ("Invalid legacy attachment; migration rolled back without discarding data")
                    ) from None
                metadata = put_attachment(
                    db,
                    case_id,
                    evidence["id"],
                    evidence.get("content_type"),
                    binary,
                    enforce_limits=False,
                )
                evidence.pop("content")
                evidence.update(metadata)
                changed = True
            if changed:
                case["revision"] = case.get("revision", 0) + 1
                db.execute("UPDATE cases SET data=? WHERE id=?", (json.dumps(case), case_id))
        for rowid, response in db.execute("SELECT rowid,response FROM receipts ORDER BY rowid"):
            db.execute(
                "UPDATE receipts SET response=?,encoding=? WHERE rowid=?",
                (encode_receipt(json.loads(response)), "zlib-json", rowid),
            )
    if version < 3:
        db.execute(
            (
                "CREATE TABLE IF NOT EXISTS policy_sources(topic "
                "TEXT,document_id TEXT,data TEXT,PRIMARY "
                "KEY(topic,document_id))"
            )
        )
        db.execute("CREATE TABLE runtime_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        db.execute(
            (
                "CREATE TABLE configuration_outbox(sequence INTEGER PRIMARY "
                "KEY AUTOINCREMENT,payload TEXT NOT NULL,created_at REAL NOT "
                "NULL,available_at REAL NOT NULL,attempts INTEGER NOT NULL "
                "DEFAULT 0,error_code TEXT,completed_at "
                "REAL)"
            )
        )
        db.execute(("CREATE INDEX configuration_pending ON configuration_outbox(completed_at,sequence)"))
    if version < 4:
        migrate_index(db)
    db.execute("PRAGMA user_version=" + str(SCHEMA_VERSION))
