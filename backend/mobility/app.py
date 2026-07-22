"""No-key ride support API. Run with uvicorn mobility.app:app --port 8010."""
import base64
import hashlib
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from mobility.catalog import RideCatalog
from mobility.service import RideSupport
from mobility.store import Store


class NewCase(BaseModel):
    trip_id: str
    description: str = Field(min_length=5, max_length=4000)
    topic: Literal["cancellation", "fare", "payment", "other"] = "other"


class Action(BaseModel):
    action: Literal["message", "handoff", "confirm", "close", "evidence"]
    text: str = Field(default="", max_length=4000)


class PolicyDocument(BaseModel):
    topic: str = Field(min_length=1,max_length=100)
    document_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=12000)


class MCPProbe(BaseModel):
    trip_id: str = Field(pattern=r"^trip-[a-z0-9-]+$",max_length=100)


class SkillDocument(BaseModel):
    content: str = Field(min_length=20, max_length=12000)


class StaffAction(BaseModel):
    action: Literal["accept", "reply", "resolve", "propose_adjustment"]
    text: str = Field(default="", max_length=4000)


def create_app(db_path=None, catalog_path=None):
    store = Store(db_path or os.getenv("KINDER_UBER_RIDE_DB", str(Path(__file__).resolve().parents[2] / ".runtime/rides.sqlite3")))
    service = RideSupport(store, RideCatalog(catalog_path or os.getenv("KINDER_UBER_RIDE_CATALOG_PATH")))

    @asynccontextmanager
    async def lifespan(app):
        try:
            await service.engine.operations.start()
            yield
        finally:
            await service.tools._client.close()
            await service.engine.close()

    app = FastAPI(title="KinderUber Ride Support", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.state.service = service

    @app.middleware("http")
    async def local_api_prefix(request, call_next):
        if request.scope["path"].startswith("/ride-api/"):
            request.scope["path"] = request.scope["path"][len("/ride-api"):]
            request.scope["raw_path"] = request.scope["path"].encode()
        return await call_next(request)

    def owner(value):
        if not value or len(value) > 100 or not all(c.isalnum() or c in "-_" for c in value):
            raise HTTPException(401, "A local demo session is required.")
        return value

    def staff(token):
        # Deliberately public demo credentials, not production authentication.
        if token != "local-demo-agent":
            raise HTTPException(403, "Open the local Agent desk to use this demo-only endpoint.")

    def write(actor, request_id, data, fn):
        if not request_id or len(request_id) > 100:
            raise HTTPException(400, "A request ID is required for every change.")
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        return store.write(actor, request_id, fingerprint, fn)

    @app.get("/health")
    def health():
        return dict(status="ok", dataset_id=service.catalog.dataset_id, catalog_trips=len(service.catalog.trips), mode=service.engine.mode, model=service.engine.model, api_key_required=service.engine.mode == "live", pipeline="original-intent-orchestrator-rag", retrieval="embedded-chroma-local-hash")

    @app.get("/api/agent/operations")
    async def operations(x_demo_agent: str = Header(default="")):
        staff(x_demo_agent)
        return await service.engine.operations.status()

    @app.post("/api/agent/quality/jobs")
    async def start_quality(x_demo_agent: str = Header(default="")):
        staff(x_demo_agent)
        quality = service.engine.operations.quality
        try:
            return quality.jobs.submit(quality.run)
        except RuntimeError:
            raise HTTPException(429, "The evaluation queue is full. Wait for a running job to finish.")

    @app.get("/api/agent/quality/jobs/{job_id}")
    def quality_job(job_id: str, x_demo_agent: str = Header(default="")):
        staff(x_demo_agent)
        job = service.engine.operations.quality.public_job(job_id)
        if job is None:
            raise HTTPException(404, "Evaluation job not found. Jobs reset on restart; completed reports remain in history.")
        return job

    @app.get("/api/agent/quality/history")
    def quality_history(x_demo_agent: str = Header(default="")):
        staff(x_demo_agent)
        return service.engine.operations.quality.history()

    @app.get("/api/agent/metrics")
    def metrics(x_demo_agent: str = Header(default="")):
        staff(x_demo_agent)
        return Response(service.engine.operations.metrics(), media_type="text/plain")

    @app.get("/api/agent/metrics-view")
    def metrics_view(x_demo_agent: str = Header(default="")):
        staff(x_demo_agent)
        return dict(text=service.engine.operations.metrics().decode())

    @app.post("/api/agent/mcp/reconnect")
    async def reconnect(x_demo_agent: str = Header(default="")):
        staff(x_demo_agent)
        return await service.engine.operations.reconnect()

    @app.post("/api/agent/mcp/probe")
    async def probe(body: MCPProbe, x_demo_agent: str = Header(default="")):
        staff(x_demo_agent)
        result = await service.engine.rag_tools.call("mcp_rides_read_trip_record", {"trip_id": body.trip_id})
        if not result.success:
            raise HTTPException(503, "The independent MCP service could not be verified. Reconnect and try again.")
        return dict(source="external_mcp", tool="read_trip_record", success=True, data=result.data, latency_ms=round(result.latency_ms, 2))

    @app.get("/api/agent/skills")
    def skills(x_demo_agent: str = Header(default="")):
        staff(x_demo_agent)
        return dict(skills=[dict(name=s.name, description=s.description, agents=s.agents, enabled=s.enabled, content=s.content) for s in service.skills.skills], errors=["A Skill could not be loaded; review the saved document." for _ in service.skills.errors], editable_content=service.skill_file.read_text())

    @app.post("/api/agent/skills/reload")
    async def reload_skills(x_demo_agent: str = Header(default="")):
        staff(x_demo_agent)
        service.skills.reload()
        return skills(x_demo_agent)

    @app.post("/api/agent/skills")
    async def update_skill(body: SkillDocument, x_demo_agent: str = Header(default=""), x_request_id: str = Header(default="")):
        staff(x_demo_agent)
        if not x_request_id or len(x_request_id) > 100:
            raise HTTPException(400, "A request ID is required for every change.")
        import re
        if re.search(r"[\u4e00-\u9fff]", body.content):
            raise HTTPException(422, "Use English for the ride Skill.")
        from core.skill_loader import SkillManager
        from tempfile import TemporaryDirectory
        # Validate before replacing the active file; no partial reload on a malformed Skill.
        with TemporaryDirectory() as directory:
            candidate = Path(directory) / "SKILL.md"
            candidate.write_text(body.content)
            candidate_manager = SkillManager(directory)
            candidate_manager.load()
            if candidate_manager.errors or not candidate_manager.skills or any(not set(s.agents) <= {"general", "technical", "billing", "escalation"} for s in candidate_manager.skills) or not re.search(r"^# +\S", body.content, re.M):
                raise HTTPException(422, "Provide a valid Skill document with a heading and content.")
        payload = ["skill", body.content]
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        cached = store.replay("agent", x_request_id, fingerprint)
        if cached is not None:
            return cached
        temporary = service.skill_file.with_suffix(".pending")
        temporary.write_text(body.content)
        temporary.replace(service.skill_file)
        service.skills.reload()
        return write("agent", x_request_id, payload, lambda db: skills(x_demo_agent))

    @app.get("/api/agent/knowledge/{topic}")
    def knowledge(topic: str, x_demo_agent: str = Header(default="")):
        staff(x_demo_agent)
        if topic not in service.engine.knowledge:
            raise HTTPException(404, "Policy scope not found.")
        kb = service.engine.knowledge[topic]
        rows = kb._collection.get(include=["documents", "metadatas"])
        return dict(scope=topic, count=kb.doc_count, documents=[dict(id=i, content=d, metadata=m) for i,d,m in zip(rows["ids"], rows["documents"], rows["metadatas"])], source_documents=service.engine.policy_sources(topic))

    @app.post("/api/agent/knowledge")
    async def update_knowledge(body: PolicyDocument, x_demo_agent: str = Header(default=""), x_request_id: str = Header(default="")):
        staff(x_demo_agent)
        if not body.content.strip() or not body.title.strip() or not body.document_id.strip():
            raise HTTPException(422, "Document fields must contain text.")
        if not x_request_id or len(x_request_id) > 100:
            raise HTTPException(400, "A request ID is required for every change.")
        if body.topic not in service.engine.knowledge:
            raise HTTPException(404,"Policy scope not found.")
        payload = ["knowledge", body.model_dump()]
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        cached = store.replay("agent", x_request_id, fingerprint)
        if cached is not None:
            return cached
        import re
        if re.search(r"[\u4e00-\u9fff]", body.title + body.content):
            raise HTTPException(422, "Use English for ride policy documents.")
        chunks = await service.engine.knowledge[body.topic].add_documents_async([dict(document_id=body.document_id, title=body.title, content=body.content)])
        service.engine.save_policy_source(body.topic, body.model_dump())
        service.engine.rag_tools.invalidate_cache("ride_policy_search")
        return write("agent", x_request_id, payload, lambda db: dict(scope=body.topic, added_chunks=chunks, document_id=body.document_id))

    @app.post("/api/agent/evaluations")
    async def evaluate(x_demo_agent: str = Header(default="")):
        staff(x_demo_agent)
        from mobility.evaluation import evaluate_engine
        return await evaluate_engine(service.engine)

    @app.get("/api/trips")
    def trips():
        return service.catalog.list()

    @app.get("/api/trips/{trip_id}")
    def trip(trip_id: str):
        return service.trip(trip_id)

    @app.get("/api/cases")
    def cases(x_demo_session: str = Header(default="")):
        return [public(c) for c in store.list(owner(x_demo_session))]

    @app.get("/api/cases/{case_id}")
    def case(case_id: str, x_demo_session: str = Header(default="")):
        return public(store.get(case_id, owner(x_demo_session)))

    @app.post("/api/cases")
    async def create(body: NewCase, x_demo_session: str = Header(default=""), x_request_id: str = Header(default="")):
        actor = owner(x_demo_session)
        if not x_request_id or len(x_request_id) > 100:
            raise HTTPException(400, "A request ID is required for every change.")
        if not body.description.strip() or len(body.description.strip()) < 5:
            raise HTTPException(422, "Please describe what happened.")
        async with service.engine_locks.hold(actor + ":" + body.trip_id):
            payload = ["create", body.model_dump()]
            fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            replay = store.replay(actor, x_request_id, fingerprint)
            if replay is not None:
                return public(replay)
            data, trace = await service.inspect(body.trip_id)
            existing = next((c for c in store.list(actor) if c["trip_id"] == body.trip_id and (c["status"] != "resolved" or c["adjustment"])), None)
            analysis = await service.engine.run(body.trip_id, body.description.strip(), existing or dict(owner=actor, trip_id=body.trip_id))
            def apply(db):
                record = service.create(db, actor, data[0], body.description.strip(), body.topic, trace)
                service.attach_analysis(record, analysis)
                if analysis["handoff"]:
                    service.handoff(record)
                store.save(db, record)
                return record
            return public(write(actor, x_request_id, payload, apply))

    @app.post("/api/cases/{case_id}/actions")
    async def action(case_id: str, body: Action, x_demo_session: str = Header(default=""), x_request_id: str = Header(default="")):
        actor = owner(x_demo_session)
        if not x_request_id or len(x_request_id) > 100:
            raise HTTPException(400, "A request ID is required for every change.")
        if body.action in ("message", "evidence") and not body.text.strip():
            raise HTTPException(422, "Please enter a message.")
        async with service.engine_locks.hold(actor + ":" + case_id):
            payload = [case_id, body.model_dump()]
            fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            replay = store.replay(actor, x_request_id, fingerprint)
            if replay is not None:
                return public(replay)
            snapshot = store.get(case_id, actor)
            analysis = await service.engine.run(snapshot["trip_id"], body.text.strip(), snapshot) if body.action == "message" else None
            def apply(db):
                record = store.load(db, case_id, actor)
                service.act(record, body.action, body.text.strip())
                if analysis:
                    service.attach_analysis(record, analysis)
                    if analysis["handoff"]:
                        service.handoff(record)
                store.save(db, record)
                return record
            return public(write(actor, x_request_id, payload, apply))

    @app.post("/api/cases/{case_id}/attachments")
    async def attachment(case_id: str, file: UploadFile = File(...), x_demo_session: str = Header(default=""), x_request_id: str = Header(default="")):
        actor = owner(x_demo_session)
        raw = await file.read(3 * 1024 * 1024 + 1)
        if len(raw) > 3 * 1024 * 1024:
            raise HTTPException(413, "Attachments must be 3 MB or smaller.")
        filename = Path(file.filename or "attachment").name[:100]
        kind = file.content_type
        valid = (kind == "image/png" and raw.startswith(b"\x89PNG\r\n\x1a\n")) or (kind == "image/jpeg" and raw.startswith(b"\xff\xd8\xff")) or (kind == "application/pdf" and raw.startswith(b"%PDF-"))
        if not valid:
            raise HTTPException(415, "Upload a PNG, JPEG, or PDF file.")
        def apply(db):
            record = store.load(db, case_id, actor)
            service.act(record, "evidence", "Attachment: " + filename, filename)
            record["evidence"][-1].update(content=base64.b64encode(raw).decode(), content_type=kind)
            store.save(db, record)
            return public(record)
        return write(actor, x_request_id, [case_id, filename, hashlib.sha256(raw).hexdigest()], apply)

    @app.get("/api/cases/{case_id}/attachments/{evidence_id}")
    def download(case_id: str, evidence_id: str, x_demo_session: str = Header(default=""), x_demo_agent: str = Header(default="")):
        if x_demo_agent:
            staff(x_demo_agent)
            record = store.get(case_id)
        else:
            record = store.get(case_id, owner(x_demo_session))
        item = next((e for e in record["evidence"] if e["id"] == evidence_id and e.get("content")), None)
        if not item:
            raise HTTPException(404, "Attachment not found.")
        return Response(base64.b64decode(item["content"]), media_type=item["content_type"], headers={"Content-Disposition": "attachment", "X-Content-Type-Options": "nosniff"})

    @app.get("/api/agent/cases")
    def queue(x_demo_agent: str = Header(default="")):
        staff(x_demo_agent)
        return [public(c) for c in store.list() if c["handoff"]]

    @app.post("/api/agent/cases/{case_id}/actions")
    def staff_action(case_id: str, body: StaffAction, x_demo_agent: str = Header(default=""), x_request_id: str = Header(default="")):
        staff(x_demo_agent)
        def apply(db):
            record = store.load(db, case_id)
            service.staff(record, body.action, body.text.strip())
            store.save(db, record)
            return public(record)
        return write("agent", x_request_id, [case_id, body.model_dump()], apply)

    # The original FastAPI application remains available as api.main:app.
    # This entry point never reads .env; live provider access is explicitly opt-in.
    from fastapi.staticfiles import StaticFiles
    frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app


def public(case):
    """Do not repeat attachment payloads in list and conversation responses."""
    return {**case, "evidence": [{k: v for k, v in e.items() if k != "content"} for e in case["evidence"]]}


app = create_app()
