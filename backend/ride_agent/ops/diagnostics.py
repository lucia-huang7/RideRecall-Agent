"""Isolated fixture cases using the same transaction/outbox path as API commands.

The live engine and read-only tools are exercised, but diagnostic case records
never enter the application's rider database. Vector namespaces are unique and
removed when the diagnostic run finishes, including on failures.
"""

import hashlib
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from fastapi import HTTPException

from ride_agent.persistence.projections import ProjectionWorker
from ride_agent.persistence.store import Store


def select_trip(catalog, issue=None):
    """Select an actual configured record; never inject a seed-only trip ID."""
    return next(
        (trip for trip in catalog.trips if issue is None or trip["issue"] == issue),
        None,
    )


class DiagnosticCases:
    def __init__(self, engine):
        self.engine = engine
        self.directory = None
        self.store = None
        self.projection = None
        self.records = {}

    def _ensure_store(self):
        if self.store is None:
            self.directory = TemporaryDirectory(prefix="ride-diagnostics-")
            self.store = Store(Path(self.directory.name) / "cases.sqlite3")
            self.projection = ProjectionWorker(self.store, self.engine.chroma, self.engine.hash_embedding)

    async def turn(self, record, text):
        self._ensure_store()
        self.records[record["id"]] = record
        try:
            snapshot = self.store.get(record["id"], record["owner"])
        except HTTPException as error:
            if error.status_code != 404:
                raise
            snapshot = record
        run = await self.engine.run(record["trip_id"], text, snapshot)

        def apply(db):
            exists = db.execute("SELECT 1 FROM cases WHERE id=?", (record["id"],)).fetchone()
            if exists:
                current = self.store.load(db, record["id"], record["owner"])
                self.engine.support.act(current, "message", text)
            else:
                trip = self.engine.support.trip(record["trip_id"])
                current = self.engine.support.create(
                    db,
                    record["owner"],
                    trip,
                    text,
                    trip["issue"],
                    [],
                    case_id=record["id"],
                )
            self.engine.support.attach_analysis(current, run)
            if run["handoff"]:
                self.engine.support.handoff(current)
            self.store.save(db, current)
            return current

        committed = self.store.write(
            record["owner"],
            uuid4().hex,
            hashlib.sha256(text.encode()).hexdigest(),
            apply,
        )
        await self.projection.flush()
        if self.store.projection_status()["pending"]:
            raise RuntimeError("Diagnostic memory projection did not complete")
        record.clear()
        record.update(committed)
        return record["engine_runs"][-1]

    async def close(self):
        try:
            for record in self.records.values():
                uid = hashlib.sha256(
                    json.dumps([record["owner"], record["id"], record["trip_id"]]).encode()
                ).hexdigest()
                for name in ("ride_memory_episodes_v1", "ride_memory_profiles_v1"):
                    try:
                        self.engine.chroma.get_collection(name, embedding_function=self.engine.hash_embedding).delete(
                            where={"user_id": uid}
                        )
                    except Exception:
                        logging.getLogger(__name__).warning("Diagnostic memory cleanup could not finish")
        finally:
            self.records.clear()
            if self.directory:
                self.directory.cleanup()
            self.directory = self.store = self.projection = None
