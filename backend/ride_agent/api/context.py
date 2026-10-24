"""Application-local resources, lifecycle and transactional HTTP helpers."""

import hashlib
import json
import re
from contextlib import asynccontextmanager
from urllib.parse import urlencode

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from ride_agent.api.security import principal_context
from ride_agent.api.telemetry import HttpObservation, configure_logging
from ride_agent.domain.case import RideSupport
from ride_agent.ops.configuration import ConfigurationWorker
from ride_agent.persistence.projections import ProjectionWorker
from ride_agent.persistence.runtime_lock import embedded_writer_lock
from ride_agent.persistence.store import Store


def public(case):
    """Do not repeat attachment payloads in list and conversation responses."""
    return {
        **case,
        "evidence": [{k: v for k, v in e.items() if k != "content"} for e in case["evidence"]],
    }


class RuntimeContext:
    def __init__(self, security, catalog, model_settings, database_path):
        self.security = security
        self.catalog = catalog
        self.model_settings = model_settings
        self.database_path = database_path
        self.observation = HttpObservation()
        self.store = self.service = self.projections = self.configuration = None

    @asynccontextmanager
    async def lifespan(self, app):

        configure_logging()
        with embedded_writer_lock(self.database_path):
            self.store = Store(self.database_path)
            self.security.validate_stored_cases(self.store, self.catalog)
            self.service = RideSupport(self.store, self.catalog, model_settings=self.model_settings)
            self.projections = ProjectionWorker(
                self.store,
                self.service.engine.chroma,
                self.service.engine.hash_embedding,
            )

            self.configuration = ConfigurationWorker(self.service)
            app.state.configuration = self.configuration
            app.state.store = self.store
            app.state.service = self.service
            app.state.projections = self.projections
            try:
                if self.security.production:
                    self.service.engine.validate_authoritative_policies()
                await self.configuration.start()
                await self.service.engine.operations.start()
                await self.projections.start()
                app.state.started = True
                yield
            finally:
                app.state.started = False
                await self.configuration.close()
                await self.projections.close()
                await self.service.tools._client.close()
                await self.service.engine.close()

    def owner(self, value):
        if self.security.production:
            principal = principal_context.get()
            if principal is None:
                raise HTTPException(401, "A valid access token is required.")
            return principal.subject
        if not value or len(value) > 100 or (not all((c.isalnum() or c in "-_" for c in value))):
            raise HTTPException(401, "A local demo session is required.")
        return value

    def staff(self, token):
        if self.security.production:
            principal = principal_context.get()
            if principal is None or not principal.roles.intersection({"specialist", "operator"}):
                raise HTTPException(403, "Staff access is required.")
            return
        if token != "local-demo-agent":
            raise HTTPException(403, "Open the local Agent desk to use this demo-only endpoint.")

    def check_revision(self, record, value):
        if not value:
            if self.security.production:
                raise HTTPException(428, "Refresh this case before making a change.")
            return
        match = re.fullmatch('"([0-9]{1,12})"', value)
        if not match:
            raise HTTPException(400, "If-Match must contain the quoted case revision.")
        if int(match.group(1)) != record.get("revision", 0):
            raise HTTPException(
                412,
                ("The case changed. Refresh and review the latest state before retrying."),
            )

    def case_response(self, record):
        return JSONResponse(
            public(dict(record, revision=record.get("revision", 0))),
            headers={"ETag": '"' + str(record.get("revision", 0)) + '"'},
        )

    def write(self, actor, request_id, data, fn):
        if not request_id or len(request_id) > 100:
            raise HTTPException(400, "A request ID is required for every change.")
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        return self.store.write(actor, request_id, fingerprint, fn)

    def list_response(self, data, path):

        headers = {"X-Total-Count": str(data["total"])}
        if data["next_cursor"]:
            headers["Link"] = (
                "<" + path + "?" + urlencode(dict(cursor=data["next_cursor"], limit=data["limit"])) + '>; rel="next"'
            )
            headers["X-Next-Cursor"] = data["next_cursor"]
        return JSONResponse([public(record) for record in data["items"]], headers=headers)

    public = staticmethod(public)
