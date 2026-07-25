"""No-key ride support API. Run with uvicorn mobility.app:app --port 8010."""
import hashlib
import asyncio
import time
import json
import os
import re
from uuid import uuid4
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Header, HTTPException, UploadFile, Query
from fastapi.responses import JSONResponse
from mobility.security import Security, principal_context, staff_receipt_owner
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from mobility.catalog import RideCatalog
from mobility.service import RideSupport
from mobility.store import Store


class NewCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trip_id: str = Field(pattern=r"^trip-[a-z0-9-]+$",max_length=100)
    description: str = Field(min_length=5, max_length=4000)
    topic: Literal["cancellation", "fare", "payment", "other"] = "other"


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["message", "handoff", "confirm", "close", "evidence"]
    text: str = Field(default="", max_length=4000)


class PolicyDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str = Field(min_length=1,max_length=100)
    document_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=12000)


class MCPProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trip_id: str = Field(pattern=r"^trip-[a-z0-9-]+$",max_length=100)


class SkillDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=20, max_length=12000)


class StaffAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["accept", "reply", "resolve", "propose_adjustment"]
    text: str = Field(default="", max_length=4000)


def create_app(db_path=None, catalog_path=None, *, security=None):
    security = security or Security()
    catalog = RideCatalog(catalog_path or os.getenv("KINDER_UBER_RIDE_CATALOG_PATH"))
    security.validate_catalog(catalog)
    from mobility.settings import ModelSettings
    model_settings=ModelSettings.from_environment()
    database_path=db_path or os.getenv('KINDER_UBER_RIDE_DB')
    if security.production and not database_path:
        raise ValueError('Production requires an explicit KINDER_UBER_RIDE_DB')
    database_path=database_path or str(Path(__file__).resolve().parents[2]/'.runtime/rides.sqlite3')
    from mobility.http_observation import HttpObservation, configure_logging
    observation=HttpObservation()
    store=service=projections=configuration=None

    @asynccontextmanager
    async def lifespan(app):
        nonlocal store, service, projections, configuration
        from mobility.runtime_lock import embedded_writer_lock
        from mobility.projections import ProjectionWorker
        configure_logging()
        with embedded_writer_lock(database_path):
            store=Store(database_path)
            security.validate_stored_cases(store,catalog)
            service=RideSupport(store,catalog,model_settings=model_settings)
            projections=ProjectionWorker(store,service.engine.chroma,service.engine.hash_embedding)
            from mobility.configuration import ConfigurationWorker
            configuration=ConfigurationWorker(service)
            app.state.configuration=configuration
            app.state.store=store
            app.state.service=service
            app.state.projections=projections
            try:
                if security.production:service.engine.validate_authoritative_policies()
                await configuration.start()
                await service.engine.operations.start()
                await projections.start()
                app.state.started=True
                yield
            finally:
                app.state.started=False
                await configuration.close()
                await projections.close()
                await service.tools._client.close()
                await service.engine.close()

    app = FastAPI(title="kinder-uber Ride Support", version="0.5.0", lifespan=lifespan,
        docs_url=None if security.production else "/docs",
        redoc_url=None if security.production else "/redoc",
        openapi_url=None if security.production else "/openapi.json")
    from mobility.http_limits import RequestBodyLimit
    app.add_middleware(RequestBodyLimit)
    app.state.observation = observation
    app.state.security = security
    app.state.started = False
    app.state.store = store
    app.state.service = service
    app.state.projections = projections
    app.state.configuration = configuration

    import sqlite3
    @app.exception_handler(sqlite3.DatabaseError)
    async def unavailable_database(request, error):
        request.state.diagnostic_error=error
        return JSONResponse({'detail':'Persistence is unavailable. Retry the same request ID after recovery.'},status_code=503,headers={'Retry-After':'2'})

    from fastapi.exceptions import RequestValidationError
    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        issues=[dict(location=list(item['loc']), message=item['msg'], type=item['type']) for item in error.errors()[:20]]
        return JSONResponse({'detail':'Please check the request fields.', 'errors':issues}, status_code=422)

    @app.middleware("http")
    async def local_api_prefix(request, call_next):
        began=time.monotonic();correlation_id=uuid4().hex;context_token=None;response=None;failure=None;cancelled=False
        request.state.correlation_id=correlation_id
        observation.active.inc()
        try:
            if request.scope['path'].startswith('/ride-api/'):
                request.scope['path']=request.scope['path'][len('/ride-api'):]
                request.scope['raw_path']=request.scope['path'].encode()
            try:
                if security.production and request.scope['path'].startswith('/api/'):
                    principal=security.authenticate(request.headers.get('authorization',''))
                    security.authorize(request.scope['path'],request.method,principal)
                    context_token=principal_context.set(principal)
                elif not security.production:
                    security.check_demo_peer(request.client.host if request.client else '')
                    if len(request.headers.getlist('host'))!=1 or len(request.headers.getlist('origin'))>1:
                        raise HTTPException(403, 'Demo requests require a loopback host and origin.')
                    security.check_demo_authority(request.headers.get('host',''),request.headers.get('origin'),test_client=bool(request.client and request.client.host=='testclient'))
                response=await call_next(request)
            except HTTPException as error:
                response=JSONResponse({'detail':error.detail},status_code=error.status_code,headers=error.headers)
            except asyncio.CancelledError:
                cancelled=True
                raise
            except Exception as error:
                failure=error
                response=JSONResponse({'detail':'An unexpected service error occurred. Retry safely or contact the workspace operator.'},status_code=500)
            response.headers['X-Correlation-ID']=correlation_id
            response.headers['X-Content-Type-Options']='nosniff'
            response.headers['Referrer-Policy']='no-referrer'
            response.headers['X-Frame-Options']='DENY'
            response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=()'
            if security.production:
                response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
            if request.scope['path'].startswith('/api/'):
                response.headers['Cache-Control']='no-store'
            return response
        finally:
            if context_token is not None:principal_context.reset(context_token)
            route=getattr(request.scope.get('route'),'path',None)
            status=response.status_code if response is not None else 499 if cancelled else 500
            observation.finish(correlation_id,request.method,route,status,time.monotonic()-began,failure or getattr(request.state,'diagnostic_error',None))

    def owner(value):
        if security.production:
            principal = principal_context.get()
            if principal is None:
                raise HTTPException(401, 'A valid access token is required.')
            return principal.subject
        if not value or len(value) > 100 or not all(c.isalnum() or c in "-_" for c in value):
            raise HTTPException(401, "A local demo session is required.")
        return value

    def staff(token):
        if security.production:
            principal = principal_context.get()
            if principal is None or not principal.roles.intersection({"specialist", "operator"}):
                raise HTTPException(403, "Staff access is required.")
            return
        # Deliberately public demo credentials, not production authentication.
        if token != "local-demo-agent":
            raise HTTPException(403, "Open the local Agent desk to use this demo-only endpoint.")

    def check_revision(record, value):
        if not value:
            if security.production:
                raise HTTPException(428, 'Refresh this case before making a change.')
            return
        match=re.fullmatch(r'"([0-9]{1,12})"',value)
        if not match:
            raise HTTPException(400, 'If-Match must contain the quoted case revision.')
        if int(match.group(1))!=record.get('revision',0):
            raise HTTPException(412, 'The case changed. Refresh and review the latest state before retrying.')

    def case_response(record):
        return JSONResponse(public(dict(record,revision=record.get('revision',0))),headers={'ETag': '"'+str(record.get('revision',0))+'"'})

    def write(actor, request_id, data, fn):
        if not request_id or len(request_id) > 100:
            raise HTTPException(400, "A request ID is required for every change.")
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        return store.write(actor, request_id, fingerprint, fn)

    @app.get('/runtime-config')
    def runtime_config():
        return dict(auth_mode='production' if security.production else 'demo', model_mode=model_settings.mode, product='kinder-uber')

    @app.get('/api/session')
    def session():
        principal = principal_context.get()
        return dict(subject=principal.subject if principal else None,
                    roles=sorted(principal.roles) if principal else ['rider','specialist','operator'],
                    model_mode=model_settings.mode, auth_mode='production' if security.production else 'demo')

    @app.get('/livez')
    def liveness():
        return dict(status='ok')

    @app.get('/readyz')
    async def ready():
        from mobility.readiness import readiness
        data, status = await readiness(app)
        return JSONResponse(data, status_code=status, headers={'Cache-Control':'no-store'})

    @app.get("/health")
    def health():
        if not app.state.started:
            raise HTTPException(503, 'The application is starting or stopping.')
        if security.production:
            return dict(status='ok', auth_mode='production')
        return dict(status="ok", dataset_id=service.catalog.dataset_id, catalog_trips=len(service.catalog.trips), mode=service.engine.mode, model=service.engine.model, api_key_required=service.engine.mode == "live", pipeline="intent-orchestration-rag", retrieval="embedded-chroma-local-hash")

    @app.get("/api/agent/operations")
    async def operations(x_demo_agent: str = Header(default="")):
        staff(x_demo_agent)
        result=await service.engine.operations.status()
        result['memory_projection']=store.projection_status()
        from mobility.configuration import status
        result['configuration']=status(store)
        result['analysis_admission']=service.admission.status()
        if service.engine.mode=='demo':
            result['demo_model_calls']=dict(total=service.engine.client.total_calls,retained=len(service.engine.client.calls),retention_limit=512)
        return result

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
        return Response(service.engine.operations.metrics()+observation.render(), media_type="text/plain")

    @app.get("/api/agent/metrics-view")
    def metrics_view(x_demo_agent: str = Header(default="")):
        staff(x_demo_agent)
        return dict(text=(service.engine.operations.metrics()+observation.render()).decode())

    @app.post("/api/agent/mcp/reconnect")
    async def reconnect(x_demo_agent: str = Header(default="")):
        staff(x_demo_agent)
        return await service.engine.operations.reconnect()

    @app.get('/api/agent/mcp/catalog')
    def probe_catalog(x_demo_agent: str=Header(default='')):
        staff(x_demo_agent)
        return dict(dataset_id=catalog.dataset_id,trips=[dict(id=trip['id'],label=trip['id'] if security.production else trip['destination']+' · '+trip['id']) for trip in catalog.trips])

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
    async def reload_skills(x_demo_agent: str = Header(default=""), x_request_id: str = Header(default="")):
        staff(x_demo_agent)
        from mobility.configuration import status
        if status(store)['pending']:raise HTTPException(409,'A configuration release is still applying. Wait before reloading the runtime file.')
        # Reload is a validated durable release too, never an unreceipted side effect.
        return await update_skill(SkillDocument(content=service.skill_file.read_text()),x_demo_agent,x_request_id)

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
        cached=store.replay(staff_receipt_owner(),x_request_id,fingerprint)
        if cached is not None:return cached
        from mobility.configuration import enqueue
        def commit_skill(db):
            sequence=enqueue(db,dict(kind='skill',content=body.content))
            return dict(status='accepted',configuration_sequence=sequence,
                skills=[dict(name=item.name,description=item.description,agents=item.agents,enabled=item.enabled,content=item.content) for item in candidate_manager.skills],
                errors=[],editable_content=body.content)
        receipt=write(staff_receipt_owner(),x_request_id,payload,commit_skill)
        await configuration.flush()
        return receipt

    @app.get('/api/agent/knowledge')
    def knowledge_scopes(x_demo_agent: str=Header(default='')):
        staff(x_demo_agent)
        return dict(scopes=[dict(id=key,title=value['title'],version=value['version']) for key,value in catalog.policies.items()])

    @app.get("/api/agent/knowledge/{topic}")
    def knowledge(topic: str, x_demo_agent: str = Header(default="")):
        staff(x_demo_agent)
        if topic not in service.engine.knowledge:
            raise HTTPException(404, "Policy scope not found.")
        kb = service.engine.knowledge[topic]
        rows = kb._collection.get(include=["documents", "metadatas"])
        return dict(scope=topic, protected_document_ids=sorted(service.engine.protected_policy_ids(topic)) if security.production else [], count=kb.doc_count, documents=[dict(id=i, content=d, metadata=m) for i,d,m in zip(rows["ids"], rows["documents"], rows["metadatas"])], source_documents=service.engine.policy_sources(topic))

    @app.post("/api/agent/knowledge")
    async def update_knowledge(body: PolicyDocument, x_demo_agent: str = Header(default=""), x_request_id: str = Header(default="")):
        staff(x_demo_agent)
        if not body.content.strip() or not body.title.strip() or not body.document_id.strip():
            raise HTTPException(422, "Document fields must contain text.")
        if not x_request_id or len(x_request_id) > 100:
            raise HTTPException(400, "A request ID is required for every change.")
        if body.topic not in service.engine.knowledge:
            raise HTTPException(404,"Policy scope not found.")
        if security.production and body.document_id.strip() in service.engine.protected_policy_ids(body.topic):
            raise HTTPException(409,'Authoritative release documents are read-only. Publish a reviewed catalog release to change financial policy.')
        payload = ["knowledge", body.model_dump()]
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        cached=store.replay(staff_receipt_owner(),x_request_id,fingerprint)
        if cached is not None:return cached
        if re.search(r"[\u4e00-\u9fff]",body.title+body.content):
            raise HTTPException(422,'Use English for ride policy documents.')
        from mobility.configuration import enqueue
        count=len(service.engine.knowledge[body.topic]._chunk_text(body.content,chunk_size=500))
        def commit_policy(db):
            sequence=enqueue(db,dict(kind='policy',topic=body.topic,document=body.model_dump()))
            return dict(status='accepted',configuration_sequence=sequence,scope=body.topic,added_chunks=count,document_id=body.document_id)
        receipt=write(staff_receipt_owner(),x_request_id,payload,commit_policy)
        await configuration.flush()
        return receipt

    @app.get('/api/agent/configuration')
    def configuration_status(x_demo_agent: str=Header(default='')):
        staff(x_demo_agent)
        from mobility.configuration import status
        return status(store)

    @app.post("/api/agent/evaluations")
    async def evaluate(x_demo_agent: str = Header(default="")):
        staff(x_demo_agent)
        from mobility.evaluation import evaluate_engine
        return await evaluate_engine(service.engine)

    @app.get("/api/trips")
    def trips():
        records = service.catalog.list()
        if security.production:
            subject = principal_context.get().subject
            records = [t for t in records if t.get('rider_id') == subject]
        return records

    @app.get("/api/trips/{trip_id}")
    def trip(trip_id: str):
        security.require_trip(catalog, trip_id)
        return service.trip(trip_id)

    def list_response(data,path):
        from urllib.parse import urlencode
        headers={'X-Total-Count':str(data['total'])}
        if data['next_cursor']:
            headers['Link']='<'+path+'?'+urlencode(dict(cursor=data['next_cursor'],limit=data['limit']))+'>; rel="next"'
            headers['X-Next-Cursor']=data['next_cursor']
        return JSONResponse([public(record) for record in data['items']],headers=headers)

    @app.get('/api/cases')
    def cases(x_demo_session: str=Header(default=''),limit: int=Query(default=25,ge=1,le=100),cursor: str|None=Query(default=None,max_length=512)):
        return list_response(store.page(owner=owner(x_demo_session),limit=limit,cursor=cursor,full=True),'/api/cases')

    @app.get('/api/cases/index')
    def case_index(x_demo_session: str=Header(default=''),limit: int=Query(default=25,ge=1,le=100),cursor: str|None=Query(default=None,max_length=512),trip_id: str|None=Query(default=None,max_length=100,pattern=r'^trip-[a-z0-9-]+$')):
        if trip_id:security.require_trip(catalog,trip_id)
        return store.page(owner=owner(x_demo_session),limit=limit,cursor=cursor,trip_id=trip_id)

    @app.get("/api/cases/{case_id}")
    def case(case_id: str, x_demo_session: str = Header(default="")):
        return case_response(store.get(case_id, owner(x_demo_session)))

    @app.post("/api/cases")
    async def create(body: NewCase, x_demo_session: str = Header(default=""), x_request_id: str = Header(default="")):
        actor = owner(x_demo_session)
        security.require_trip(catalog, body.trip_id)
        if not x_request_id or len(x_request_id) > 100:
            raise HTTPException(400, "A request ID is required for every change.")
        if not body.description.strip() or len(body.description.strip()) < 5:
            raise HTTPException(422, "Please describe what happened.")
        async with service.admission.hold(actor), service.engine_locks.hold(actor + ":" + body.trip_id):
            payload = ["create", body.model_dump()]
            fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            replay = store.replay(actor, x_request_id, fingerprint)
            if replay is not None:
                return case_response(replay)
            data, trace = await service.inspect(body.trip_id)
            existing = store.find_trip(actor,body.trip_id)
            candidate_id=existing['id'] if existing else 'KU-'+uuid4().hex.upper()
            expected_revision=existing.get('revision',0) if existing else 0
            context=existing or dict(id=candidate_id,owner=actor,trip_id=body.trip_id)
            analysis = await service.analyze(body.trip_id, body.description.strip(), context)
            def apply(db):
                record = service.create(db, actor, data[0], body.description.strip(), body.topic, trace, case_id=candidate_id)
                if record['id']!=candidate_id or record.get('revision',0)!=expected_revision:
                    raise HTTPException(409, 'The case changed during analysis. Refresh and retry.')
                service.attach_analysis(record, analysis)
                if analysis["handoff"]:
                    service.handoff(record)
                store.save(db, record)
                return record
            record=write(actor, x_request_id, payload, apply)
            await projections.flush()
            return case_response(record)

    @app.post("/api/cases/{case_id}/actions")
    async def action(case_id: str, body: Action, x_demo_session: str = Header(default=""), x_request_id: str = Header(default=""), if_match: str = Header(default="")):
        actor = owner(x_demo_session)
        if not x_request_id or len(x_request_id) > 100:
            raise HTTPException(400, "A request ID is required for every change.")
        if body.action in ("message", "evidence") and not body.text.strip():
            raise HTTPException(422, "Please enter a message.")
        async with service.admission.hold(actor,enabled=body.action=="message"), (service.engine_locks.hold(actor + ":" + case_id) if body.action=="message" else nullcontext()):
            payload = [case_id, body.model_dump()]
            fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            replay = store.replay(actor, x_request_id, fingerprint)
            if replay is not None:
                return case_response(replay)
            snapshot = store.get(case_id, actor)
            check_revision(snapshot, if_match)
            if body.action=="message":service.check_rider_budget(snapshot)
            analysis = await service.analyze(snapshot["trip_id"], body.text.strip(), snapshot) if body.action == "message" else None
            def apply(db):
                record = store.load(db, case_id, actor)
                check_revision(record,if_match)
                if analysis and record.get('revision',0)!=snapshot.get('revision',0):
                    raise HTTPException(409, 'The case changed during analysis. Refresh and retry.')
                service.act(record, body.action, body.text.strip())
                if analysis:
                    service.attach_analysis(record, analysis)
                    if analysis["handoff"]:
                        service.handoff(record)
                store.save(db, record)
                return record
            record=write(actor, x_request_id, payload, apply)
            await projections.flush()
            return case_response(record)

    @app.post("/api/cases/{case_id}/attachments")
    async def attachment(case_id: str, file: UploadFile = File(...), x_demo_session: str = Header(default=""), x_request_id: str = Header(default=""), if_match: str = Header(default="")):
        actor = owner(x_demo_session)
        raw = await file.read(3 * 1024 * 1024 + 1)
        if len(raw) > 3 * 1024 * 1024:
            raise HTTPException(413, "Attachments must be 3 MB or smaller.")
        filename = (file.filename or 'attachment').replace('\\','/').rsplit('/',1)[-1]
        filename = ''.join(c for c in filename if ord(c)>=32 and ord(c)!=127)[:100] or 'attachment'
        kind = file.content_type
        valid = (kind == "image/png" and raw.startswith(b"\x89PNG\r\n\x1a\n")) or (kind == "image/jpeg" and raw.startswith(b"\xff\xd8\xff")) or (kind == "application/pdf" and raw.startswith(b"%PDF-"))
        if not valid:
            raise HTTPException(415, "Upload a PNG, JPEG, or PDF file.")
        def apply(db):
            record = store.load(db, case_id, actor)
            check_revision(record,if_match)
            service.act(record, "evidence", "Attachment: " + filename, filename)
            evidence=record['evidence'][-1]
            evidence.update(store.put_attachment(db,case_id,evidence['id'],kind,raw))
            store.save(db, record)
            return public(record)
        return case_response(write(actor, x_request_id, [case_id, filename, hashlib.sha256(raw).hexdigest()], apply))

    @app.get("/api/cases/{case_id}/attachments/{evidence_id}")
    def download(case_id: str, evidence_id: str, x_demo_session: str = Header(default=""), x_demo_agent: str = Header(default="")):
        principal = principal_context.get()
        specialist = security.production and principal and 'specialist' in principal.roles
        if specialist or (not security.production and x_demo_agent):
            staff(x_demo_agent)
            record = store.get(case_id)
            if security.production and not record['handoff']:
                raise HTTPException(404, 'Attachment not found.')
        else:
            record = store.get(case_id, owner(x_demo_session))
        item = next((e for e in record["evidence"] if e["id"] == evidence_id and e.get("filename")), None)
        if not item:
            raise HTTPException(404, "Attachment not found.")
        stored=store.attachment(case_id,evidence_id)
        if stored is None:
            raise HTTPException(503, 'The attachment record is unavailable. Request workspace assistance.')
        from urllib.parse import quote
        disposition="attachment; filename*=UTF-8''"+quote(item['filename'],safe='')
        return Response(stored['data'],media_type=stored['content_type'],headers={'Content-Disposition':disposition,'X-Content-Type-Options':'nosniff','ETag':'"'+stored['sha256']+'"'})

    @app.get('/api/agent/cases')
    def queue(x_demo_agent: str=Header(default=''),limit: int=Query(default=25,ge=1,le=100),cursor: str|None=Query(default=None,max_length=512)):
        staff(x_demo_agent)
        return list_response(store.page(handed_off=True,limit=limit,cursor=cursor,full=True),'/api/agent/cases')

    @app.get('/api/agent/cases/index')
    def queue_index(x_demo_agent: str=Header(default=''),limit: int=Query(default=25,ge=1,le=100),cursor: str|None=Query(default=None,max_length=512)):
        staff(x_demo_agent)
        return store.page(handed_off=True,limit=limit,cursor=cursor)

    @app.get('/api/agent/cases/{case_id}')
    def specialist_case(case_id: str,x_demo_agent: str=Header(default='')):
        staff(x_demo_agent)
        record=store.get(case_id)
        if not record.get('handoff'):raise HTTPException(404,'Case not found.')
        principal=principal_context.get()
        return dict(case=public(record),available_actions=service.staff_options(record,principal.subject if security.production else None))

    @app.post("/api/agent/cases/{case_id}/actions")
    def staff_action(case_id: str, body: StaffAction, x_demo_agent: str = Header(default=""), x_request_id: str = Header(default=""), if_match: str = Header(default="")):
        staff(x_demo_agent)
        def apply(db):
            record = store.load(db, case_id)
            if security.production and not record['handoff']:
                raise HTTPException(404, 'Case not found.')
            check_revision(record,if_match)
            service.staff(record, body.action, body.text.strip(), staff_id=principal_context.get().subject if security.production else None)
            if security.production and record['messages'] and record['messages'][-1]['role']=='agent':
                record['messages'][-1]['staff_subject']=principal_context.get().subject
            store.save(db, record)
            return public(record)
        return case_response(write(staff_receipt_owner(), x_request_id, [case_id, body.model_dump()], apply))

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
