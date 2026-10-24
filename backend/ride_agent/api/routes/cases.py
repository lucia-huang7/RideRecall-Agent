"""Cases endpoints; installed on an isolated application instance."""

import hashlib
import json
from contextlib import nullcontext
from urllib.parse import quote
from uuid import uuid4

from fastapi import File, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response

from ride_agent.api.schemas import Action, NewCase
from ride_agent.api.security import principal_context


def install(app, runtime):

    @app.get("/api/trips")
    def trips():
        records = runtime.service.catalog.list()
        if runtime.security.production:
            subject = principal_context.get().subject
            records = [t for t in records if t.get("rider_id") == subject]
        return records

    @app.get("/api/trips/{trip_id}")
    def trip(trip_id: str):
        runtime.security.require_trip(runtime.catalog, trip_id)
        return runtime.service.trip(trip_id)

    @app.get("/api/cases")
    def cases(
        x_demo_session: str = Header(default=""),
        limit: int = Query(default=25, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
    ):
        return runtime.list_response(
            runtime.store.page(
                owner=runtime.owner(x_demo_session),
                limit=limit,
                cursor=cursor,
                full=True,
            ),
            "/api/cases",
        )

    @app.get("/api/cases/index")
    def case_index(
        x_demo_session: str = Header(default=""),
        limit: int = Query(default=25, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
        trip_id: str | None = Query(default=None, max_length=100, pattern="^trip-[a-z0-9-]+$"),
    ):
        if trip_id:
            runtime.security.require_trip(runtime.catalog, trip_id)
        return runtime.store.page(
            owner=runtime.owner(x_demo_session),
            limit=limit,
            cursor=cursor,
            trip_id=trip_id,
        )

    @app.get("/api/cases/{case_id}")
    def case(case_id: str, x_demo_session: str = Header(default="")):
        return runtime.case_response(runtime.store.get(case_id, runtime.owner(x_demo_session)))

    @app.post("/api/cases")
    async def create(
        body: NewCase,
        x_demo_session: str = Header(default=""),
        x_request_id: str = Header(default=""),
    ):
        actor = runtime.owner(x_demo_session)
        runtime.security.require_trip(runtime.catalog, body.trip_id)
        if not x_request_id or len(x_request_id) > 100:
            raise HTTPException(400, "A request ID is required for every change.")
        if not body.description.strip() or len(body.description.strip()) < 5:
            raise HTTPException(422, "Please describe what happened.")
        async with (
            runtime.service.admission.hold(actor),
            runtime.service.engine_locks.hold(actor + ":" + body.trip_id),
        ):
            payload = [
                "create",
                body.model_dump(exclude={"agent_mode"} if body.agent_mode is None else set()),
            ]
            fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            replay = runtime.store.replay(actor, x_request_id, fingerprint)
            if replay is not None:
                return runtime.case_response(replay)
            data, trace = await runtime.service.inspect(body.trip_id)
            existing = runtime.store.find_trip(actor, body.trip_id)
            candidate_id = existing["id"] if existing else "KU-" + uuid4().hex.upper()
            expected_revision = existing.get("revision", 0) if existing else 0
            context = existing or dict(id=candidate_id, owner=actor, trip_id=body.trip_id)
            context = dict(context)
            if body.agent_mode is not None:
                context["agent_mode"] = body.agent_mode
            analysis = await runtime.service.analyze(body.trip_id, body.description.strip(), context)

            def apply(db):
                record = runtime.service.create(
                    db,
                    actor,
                    data[0],
                    body.description.strip(),
                    body.topic,
                    trace,
                    case_id=candidate_id,
                )
                if record["id"] != candidate_id or record.get("revision", 0) != expected_revision:
                    raise HTTPException(409, "The case changed during analysis. Refresh and retry.")
                runtime.service.attach_analysis(record, analysis)
                if analysis["handoff"]:
                    runtime.service.handoff(record)
                runtime.store.save(db, record)
                return record

            record = runtime.write(actor, x_request_id, payload, apply)
            await runtime.projections.flush()
            return runtime.case_response(record)

    @app.post("/api/cases/{case_id}/actions")
    async def action(
        case_id: str,
        body: Action,
        x_demo_session: str = Header(default=""),
        x_request_id: str = Header(default=""),
        if_match: str = Header(default=""),
    ):
        actor = runtime.owner(x_demo_session)
        if not x_request_id or len(x_request_id) > 100:
            raise HTTPException(400, "A request ID is required for every change.")
        if body.agent_mode is not None and body.action != "message":
            raise HTTPException(422, "Agent mode can only be selected for a message.")
        if body.action in ("message", "evidence") and (not body.text.strip()):
            raise HTTPException(422, "Please enter a message.")
        async with (
            runtime.service.admission.hold(actor, enabled=body.action == "message"),
            runtime.service.engine_locks.hold(actor + ":" + case_id) if body.action == "message" else nullcontext(),
        ):
            payload = [
                case_id,
                body.model_dump(exclude={"agent_mode"} if body.agent_mode is None else set()),
            ]
            fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            replay = runtime.store.replay(actor, x_request_id, fingerprint)
            if replay is not None:
                return runtime.case_response(replay)
            snapshot = runtime.store.get(case_id, actor)
            runtime.check_revision(snapshot, if_match)
            if body.action == "message":
                runtime.service.check_rider_budget(snapshot)
            analysis_context = dict(snapshot)
            if body.agent_mode is not None:
                analysis_context["agent_mode"] = body.agent_mode
            analysis = (
                await runtime.service.analyze(snapshot["trip_id"], body.text.strip(), analysis_context)
                if body.action == "message"
                else None
            )

            def apply(db):
                record = runtime.store.load(db, case_id, actor)
                runtime.check_revision(record, if_match)
                if analysis and record.get("revision", 0) != snapshot.get("revision", 0):
                    raise HTTPException(409, "The case changed during analysis. Refresh and retry.")
                runtime.service.act(record, body.action, body.text.strip())
                if analysis:
                    runtime.service.attach_analysis(record, analysis)
                    if analysis["handoff"]:
                        runtime.service.handoff(record)
                runtime.store.save(db, record)
                return record

            record = runtime.write(actor, x_request_id, payload, apply)
            await runtime.projections.flush()
            return runtime.case_response(record)

    @app.post("/api/cases/{case_id}/attachments")
    async def attachment(
        case_id: str,
        file: UploadFile = File(...),
        x_demo_session: str = Header(default=""),
        x_request_id: str = Header(default=""),
        if_match: str = Header(default=""),
    ):
        actor = runtime.owner(x_demo_session)
        raw = await file.read(3 * 1024 * 1024 + 1)
        if len(raw) > 3 * 1024 * 1024:
            raise HTTPException(413, "Attachments must be 3 MB or smaller.")
        filename = (file.filename or "attachment").replace("\\", "/").rsplit("/", 1)[-1]
        filename = "".join((c for c in filename if ord(c) >= 32 and ord(c) != 127))[:100] or "attachment"
        kind = file.content_type
        valid = (
            kind == "image/png"
            and raw.startswith(b"\x89PNG\r\n\x1a\n")
            or (kind == "image/jpeg" and raw.startswith(b"\xff\xd8\xff"))
            or (kind == "application/pdf" and raw.startswith(b"%PDF-"))
        )
        if not valid:
            raise HTTPException(415, "Upload a PNG, JPEG, or PDF file.")

        def apply(db):
            record = runtime.store.load(db, case_id, actor)
            runtime.check_revision(record, if_match)
            runtime.service.act(record, "evidence", "Attachment: " + filename, filename)
            evidence = record["evidence"][-1]
            evidence.update(runtime.store.put_attachment(db, case_id, evidence["id"], kind, raw))
            runtime.store.save(db, record)
            return runtime.public(record)

        return runtime.case_response(
            runtime.write(
                actor,
                x_request_id,
                [case_id, filename, hashlib.sha256(raw).hexdigest()],
                apply,
            )
        )

    @app.get("/api/cases/{case_id}/attachments/{evidence_id}")
    def download(
        case_id: str,
        evidence_id: str,
        x_demo_session: str = Header(default=""),
        x_demo_agent: str = Header(default=""),
    ):
        principal = principal_context.get()
        specialist = runtime.security.production and principal and ("specialist" in principal.roles)
        if specialist or (not runtime.security.production and x_demo_agent):
            runtime.staff(x_demo_agent)
            record = runtime.store.get(case_id)
            if runtime.security.production and (not record["handoff"]):
                raise HTTPException(404, "Attachment not found.")
        else:
            record = runtime.store.get(case_id, runtime.owner(x_demo_session))
        item = next(
            (e for e in record["evidence"] if e["id"] == evidence_id and e.get("filename")),
            None,
        )
        if not item:
            raise HTTPException(404, "Attachment not found.")
        stored = runtime.store.attachment(case_id, evidence_id)
        if stored is None:
            raise HTTPException(
                503,
                "The attachment record is unavailable. Request workspace assistance.",
            )

        disposition = "attachment; filename*=UTF-8''" + quote(item["filename"], safe="")
        return Response(
            stored["data"],
            media_type=stored["content_type"],
            headers={
                "Content-Disposition": disposition,
                "X-Content-Type-Options": "nosniff",
                "ETag": '"' + stored["sha256"] + '"',
            },
        )
