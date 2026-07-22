"""Transactional local demo storage; business facts are separate from chat memory."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException


def now():
    return datetime.now(timezone.utc).isoformat()


def event(case, kind, title, detail, actor="system"):
    case["events"].append(dict(id=uuid4().hex, kind=kind, title=title, detail=detail, actor=actor, at=now()))


def message(case, role, content):
    case["messages"].append(dict(id=uuid4().hex, role=role, content=content, at=now()))


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY, owner TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS cases_owner ON cases(owner);
            CREATE TABLE IF NOT EXISTS receipts(owner TEXT, key TEXT, fingerprint TEXT, response TEXT,
                PRIMARY KEY(owner, key));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def ping(self):
        # URI mode=rw prevents a health check from creating a missing database.
        uri = Path(self.path).resolve().as_uri() + '?mode=rw'
        with sqlite3.connect(uri, uri=True, timeout=0.25) as db:
            db.execute('SELECT 1 FROM cases LIMIT 1').fetchone()
        return True

    def list(self, owner=None):
        with self.connect() as db:
            rows = db.execute("SELECT data FROM cases" if owner is None else "SELECT data FROM cases WHERE owner=?", () if owner is None else (owner,)).fetchall()
        return sorted([json.loads(r[0]) for r in rows], key=lambda c: c["updated_at"], reverse=True)

    def get(self, case_id, owner=None):
        with self.connect() as db:
            row = db.execute("SELECT owner,data FROM cases WHERE id=?", (case_id,)).fetchone()
        if row is None or (owner is not None and row[0] != owner):
            raise HTTPException(404, "Case not found.")
        return json.loads(row[1])

    def replay(self, owner, key, fingerprint):
        with self.connect() as db:
            row = db.execute("SELECT fingerprint,response FROM receipts WHERE owner=? AND key=?", (owner, key)).fetchone()
        if row is None:
            return None
        if row[0] != fingerprint:
            raise HTTPException(409, "This request ID was already used for a different operation.")
        return json.loads(row[1])

    def write(self, owner, key, fingerprint, operation):
        """Commit a mutation and its exact replay response in the same transaction."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT fingerprint,response FROM receipts WHERE owner=? AND key=?", (owner, key)).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise HTTPException(409, "This request ID was already used for a different operation.")
                return json.loads(row[1])
            result = operation(db)
            db.execute("INSERT INTO receipts VALUES(?,?,?,?)", (owner, key, fingerprint, json.dumps(result)))
            return result

    @staticmethod
    def load(db, case_id, owner=None):
        row = db.execute("SELECT owner,data FROM cases WHERE id=?", (case_id,)).fetchone()
        if row is None or (owner is not None and row[0] != owner):
            raise HTTPException(404, "Case not found.")
        return json.loads(row[1])

    @staticmethod
    def save(db, case):
        case["updated_at"] = now()
        db.execute("INSERT INTO cases VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (case["id"], case["owner"], json.dumps(case)))
