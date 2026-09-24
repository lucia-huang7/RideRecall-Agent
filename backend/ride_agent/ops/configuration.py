"""Durable management releases, applied in order after their command commits.

SQLite owns requested content. Files and retrieval indexes are projections, not
an alternative authority. Read/write gating keeps analyses out of partial index
replacements while allowing unrelated analyses to execute concurrently.
"""

import asyncio
import json
import os
import sqlite3
import time
from contextlib import asynccontextmanager


class ConfigurationGate:
    def __init__(self):
        self.condition = asyncio.Condition()
        self.readers = 0
        self.writer = False
        self.waiting_writers = 0

    @asynccontextmanager
    async def read(self):
        async with self.condition:
            await self.condition.wait_for(lambda: not self.writer and (not self.waiting_writers))
            self.readers += 1
        try:
            yield
        finally:
            async with self.condition:
                self.readers -= 1
                self.condition.notify_all()

    @asynccontextmanager
    async def write(self):
        async with self.condition:
            self.waiting_writers += 1
            try:
                await self.condition.wait_for(lambda: not self.writer and (not self.readers))
                self.writer = True
            finally:
                self.waiting_writers -= 1
                self.condition.notify_all()
        try:
            yield
        finally:
            async with self.condition:
                self.writer = False
                self.condition.notify_all()


def enqueue(db, payload):
    if payload["kind"] == "policy":
        document = payload["document"]
        db.execute(
            (
                "INSERT INTO policy_sources(topic,document_id,data) "
                "VALUES(?,?,?) ON CONFLICT(topic,document_id) DO UPDATE SET "
                "data=excluded.data"
            ),
            (payload["topic"], document["document_id"], json.dumps(document)),
        )
    elif payload["kind"] == "skill":
        db.execute(
            (
                "INSERT INTO runtime_settings(key,value) "
                "VALUES('ride_skill',?) ON CONFLICT(key) DO UPDATE SET "
                "value=excluded.value"
            ),
            (payload["content"],),
        )
    else:
        raise ValueError("Unsupported configuration kind")
    return db.execute(
        ("INSERT INTO configuration_outbox(payload,created_at,available_at) VALUES(?,?,?)"),
        (json.dumps(payload), time.time(), time.time()),
    ).lastrowid


def status(store):
    with store.connect() as db:
        row = db.execute(
            (
                "SELECT COUNT(*),COALESCE(SUM(attempts>0),0),MIN(created_at) "
                "FROM configuration_outbox WHERE completed_at IS "
                "NULL"
            )
        ).fetchone()
        sequence = db.execute("SELECT COALESCE(MAX(sequence),0) FROM configuration_outbox").fetchone()[0]
    return dict(
        pending=row[0],
        retrying=row[1],
        oldest_age_seconds=max(0, time.time() - row[2]) if row[2] else 0,
        latest_sequence=sequence,
    )


def atomic_skill(path, content):
    temporary = path.with_suffix(".pending")
    descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 384)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    if os.name == "posix":
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class ConfigurationWorker:
    def __init__(self, service):
        self.service = service
        self.store = service.store
        self.lock = asyncio.Lock()
        self.wake = asyncio.Event()
        self.task = None
        self.stopping = False
        self.last_error = None

    async def start(self):
        await self.flush()
        self.task = asyncio.create_task(self.run(), name="configuration-outbox")

    async def run(self):
        while not self.stopping:
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=1)
            except TimeoutError:
                pass
            self.wake.clear()
            await self.flush()

    async def apply(self, payload):
        if payload["kind"] == "policy":
            await self.service.engine.knowledge[payload["topic"]].add_documents_async([payload["document"]])
            self.service.engine.rag_tools.invalidate_cache("ride_policy_search")
        elif payload["kind"] == "skill":
            await asyncio.to_thread(atomic_skill, self.service.skill_file, payload["content"])
            self.service.skills.reload()
            if self.service.skills.errors:
                raise ValueError("Committed Skill did not load")
        else:
            raise ValueError("Unsupported configuration projection")

    async def flush(self, limit=16):
        async with self.lock:
            for _ in range(limit):
                record = None
                try:
                    with self.store.connect() as db:
                        db.row_factory = sqlite3.Row
                        record = db.execute(
                            ("SELECT * FROM configuration_outbox WHERE completed_at IS NULL ORDER BY sequence LIMIT 1")
                        ).fetchone()
                    if record is None:
                        self.last_error = None
                        return
                    if record["available_at"] > time.time():
                        return
                    async with self.service.configuration_gate.write():
                        await self.apply(json.loads(record["payload"]))
                        with self.store.connect() as db:
                            db.execute(
                                ("UPDATE configuration_outbox SET completed_at=?,error_code=NULL WHERE sequence=?"),
                                (time.time(), record["sequence"]),
                            )
                    self.last_error = None
                except Exception as error:
                    self.last_error = type(error).__name__
                    if record is not None:
                        try:
                            with self.store.connect() as db:
                                db.execute(
                                    (
                                        "UPDATE configuration_outbox SET "
                                        "attempts=attempts+1,error_code=?,available_at=? WHERE "
                                        "sequence=?"
                                    ),
                                    (
                                        self.last_error,
                                        time.time() + min(2 ** min(record["attempts"] + 1, 6), 60),
                                        record["sequence"],
                                    ),
                                )
                        except Exception:
                            pass
                    return
            self.wake.set()

    async def close(self):
        self.stopping = True
        self.wake.set()
        if self.task:
            try:
                await asyncio.wait_for(self.task, timeout=10)
            except TimeoutError:
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
