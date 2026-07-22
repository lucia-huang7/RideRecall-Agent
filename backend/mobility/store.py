"""Versioned transactional case state, idempotency receipts and a memory outbox."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from fastapi import HTTPException

from mobility.persistence import SCHEMA_VERSION, migrate, encode_receipt, decode_receipt, put_attachment, get_attachment


def now():
    return datetime.now(timezone.utc).isoformat()


def event(case, kind, title, detail, actor='system'):
    case['events'].append(dict(id=uuid4().hex,kind=kind,title=title,detail=detail,actor=actor,at=now()))


def message(case, role, content):
    case['messages'].append(dict(id=uuid4().hex,role=role,content=content,at=now()))


def decoded(raw):
    case=json.loads(raw)
    case.setdefault('revision',0)
    return case


class Store:
    def __init__(self, path):
        self.path=str(path)
        target=Path(path)
        if not target.exists() and target.with_suffix('.vectors').exists():
            raise RuntimeError('Case database is missing while derived indexes remain. Restore the database or choose a fresh data directory.')
        target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        try:
            descriptor=os.open(target,os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600)
            os.close(descriptor)
        except FileExistsError:pass
        if os.name=='posix':target.chmod(0o600)
        with self.connect(create=True) as db:
            db.execute('BEGIN IMMEDIATE')
            migrate(db)

    @contextmanager
    def connect(self, *, create=False):
        mode='rwc' if create else 'rw'
        uri=Path(self.path).resolve().as_uri()+'?mode='+mode
        db=sqlite3.connect(uri,uri=True,timeout=5)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA synchronous=FULL')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:db.close()

    def ping(self):
        uri=Path(self.path).resolve().as_uri()+'?mode=rw'
        with sqlite3.connect(uri,uri=True,timeout=.25) as db:
            db.execute('SELECT 1 FROM cases LIMIT 1').fetchone()
            db.execute('SELECT 1 FROM case_index LIMIT 1').fetchone()
            key=db.execute("SELECT value FROM runtime_settings WHERE key='case_cursor_secret'").fetchone()
            if key is None or len(key[0])!=64 or any(c not in '0123456789abcdef' for c in key[0]):return False
            if db.execute('PRAGMA user_version').fetchone()[0]!=SCHEMA_VERSION:return False
        return True

    def list(self, owner=None):
        with self.connect() as db:
            rows=db.execute('SELECT data FROM cases' if owner is None else 'SELECT data FROM cases WHERE owner=?',() if owner is None else (owner,)).fetchall()
        return sorted([decoded(r[0]) for r in rows],key=lambda c:c['updated_at'],reverse=True)

    def page(self,**options):
        from mobility.case_index import page
        return page(self,**options)

    def find_trip(self,owner,trip_id):
        from mobility.case_index import find_trip
        with self.connect() as db:return find_trip(db,owner,trip_id)

    def get(self, case_id, owner=None):
        with self.connect() as db:return self.load(db,case_id,owner)

    def replay(self, owner, key, fingerprint):
        with self.connect() as db:
            row=db.execute('SELECT fingerprint,response,encoding FROM receipts WHERE owner=? AND key=?',(owner,key)).fetchone()
        if row is None:return None
        if row[0]!=fingerprint:raise HTTPException(409,'This request ID was already used for a different operation.')
        return decode_receipt(row[1],row[2])

    def write(self, owner, key, fingerprint, operation):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT fingerprint,response,encoding FROM receipts WHERE owner=? AND key=?',(owner,key)).fetchone()
            if row:
                if row[0]!=fingerprint:raise HTTPException(409,'This request ID was already used for a different operation.')
                return decode_receipt(row[1],row[2])
            result=operation(db)
            db.execute('INSERT INTO receipts(owner,key,fingerprint,response,encoding) VALUES(?,?,?,?,?)',(owner,key,fingerprint,encode_receipt(result),'zlib-json'))
            return result

    @staticmethod
    def load(db, case_id, owner=None):
        row=db.execute('SELECT owner,data FROM cases WHERE id=?',(case_id,)).fetchone()
        if row is None or (owner is not None and row[0]!=owner):raise HTTPException(404,'Case not found.')
        return decoded(row[1])

    @staticmethod
    def save(db, case):
        current=db.execute('SELECT owner,data FROM cases WHERE id=?',(case['id'],)).fetchone()
        revision=case.get('revision',0)
        if current is not None:
            if current[0]!=case['owner']:raise HTTPException(409,'Case ownership cannot change.')
            if decoded(current[1])['revision']!=revision:raise HTTPException(409,'The case changed. Refresh and retry.')
        elif revision!=0:raise HTTPException(409,'The case no longer exists.')
        case['revision']=revision+1
        case['updated_at']=now()
        pending=case.pop('_pending_projections',[])
        db.execute('INSERT INTO cases VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',(case['id'],case['owner'],json.dumps(case)))
        from mobility.case_index import index_case
        index_case(db,case)
        for projection in pending:
            db.execute('INSERT INTO projection_outbox(id,case_id,payload,created_at,available_at) VALUES(?,?,?,?,?)',
                       (projection['id'],case['id'],json.dumps(projection['effects']),time.time(),time.time()))

    @staticmethod
    def put_attachment(db,case_id,evidence_id,media,raw):
        return put_attachment(db,case_id,evidence_id,media,raw)

    def attachment(self,case_id,evidence_id):
        with self.connect() as db:return get_attachment(db,case_id,evidence_id)

    def next_projection(self):
        with self.connect() as db:
            db.row_factory=sqlite3.Row
            row=db.execute('SELECT * FROM projection_outbox WHERE completed_at IS NULL ORDER BY sequence LIMIT 1').fetchone()
        return dict(row) if row else None

    def finish_projection(self, sequence):
        with self.connect() as db:
            db.execute('UPDATE projection_outbox SET completed_at=?,error_code=NULL WHERE sequence=?',(time.time(),sequence))

    def retry_projection(self, sequence, error_code):
        with self.connect() as db:
            attempts=db.execute('SELECT attempts FROM projection_outbox WHERE sequence=?',(sequence,)).fetchone()[0]+1
            db.execute('UPDATE projection_outbox SET attempts=?,error_code=?,available_at=? WHERE sequence=?',
                       (attempts,error_code[:80],time.time()+min(2**min(attempts,6),60),sequence))

    def projection_status(self):
        with self.connect() as db:
            row=db.execute('SELECT COUNT(*),COALESCE(SUM(attempts>0),0),MIN(created_at) FROM projection_outbox WHERE completed_at IS NULL').fetchone()
        return dict(pending=row[0],retrying=row[1],oldest_age_seconds=max(0,time.time()-row[2]) if row[2] else 0)
