"""Operations endpoints; installed on an isolated application instance."""

import hashlib
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import Header, HTTPException
from fastapi.responses import Response

from ride_agent.agent.skills import SkillManager
from ride_agent.api.schemas import MCPProbe, PolicyDocument, SkillDocument
from ride_agent.api.security import staff_receipt_owner
from ride_agent.evaluation.checks import evaluate_engine
from ride_agent.ops.configuration import enqueue, status


def install(app, runtime):

    @app.get("/api/agent/operations")
    async def operations(x_demo_agent: str = Header(default="")):
        runtime.staff(x_demo_agent)
        result = await runtime.service.engine.operations.status()
        result["memory_projection"] = runtime.store.projection_status()

        result["configuration"] = status(runtime.store)
        result["analysis_admission"] = runtime.service.admission.status()
        if runtime.service.engine.mode == "demo":
            result["demo_model_calls"] = dict(
                total=runtime.service.engine.client.total_calls,
                retained=len(runtime.service.engine.client.calls),
                retention_limit=512,
            )
        return result

    @app.post("/api/agent/quality/jobs")
    async def start_quality(x_demo_agent: str = Header(default="")):
        runtime.staff(x_demo_agent)
        quality = runtime.service.engine.operations.quality
        try:
            return quality.jobs.submit(quality.run)
        except RuntimeError:
            raise HTTPException(429, "The evaluation queue is full. Wait for a running job to finish.")

    @app.get("/api/agent/quality/jobs/{job_id}")
    def quality_job(job_id: str, x_demo_agent: str = Header(default="")):
        runtime.staff(x_demo_agent)
        job = runtime.service.engine.operations.quality.public_job(job_id)
        if job is None:
            raise HTTPException(
                404,
                ("Evaluation job not found. Jobs reset on restart; completed reports remain in history."),
            )
        return job

    @app.get("/api/agent/quality/history")
    def quality_history(x_demo_agent: str = Header(default="")):
        runtime.staff(x_demo_agent)
        return runtime.service.engine.operations.quality.history()

    @app.get("/api/agent/metrics")
    def metrics(x_demo_agent: str = Header(default="")):
        runtime.staff(x_demo_agent)
        return Response(
            runtime.service.engine.operations.metrics() + runtime.observation.render(),
            media_type="text/plain",
        )

    @app.get("/api/agent/metrics-view")
    def metrics_view(x_demo_agent: str = Header(default="")):
        runtime.staff(x_demo_agent)
        return dict(text=(runtime.service.engine.operations.metrics() + runtime.observation.render()).decode())

    @app.post("/api/agent/mcp/reconnect")
    async def reconnect(x_demo_agent: str = Header(default="")):
        runtime.staff(x_demo_agent)
        return await runtime.service.engine.operations.reconnect()

    @app.get("/api/agent/mcp/catalog")
    def probe_catalog(x_demo_agent: str = Header(default="")):
        runtime.staff(x_demo_agent)
        return dict(
            dataset_id=runtime.catalog.dataset_id,
            trips=[
                dict(
                    id=trip["id"],
                    label=trip["id"] if runtime.security.production else trip["destination"] + " · " + trip["id"],
                )
                for trip in runtime.catalog.trips
            ],
        )

    @app.post("/api/agent/mcp/probe")
    async def probe(body: MCPProbe, x_demo_agent: str = Header(default="")):
        runtime.staff(x_demo_agent)
        result = await runtime.service.engine.rag_tools.call("mcp_rides_read_trip_record", {"trip_id": body.trip_id})
        if not result.success:
            raise HTTPException(
                503,
                ("The independent MCP service could not be verified. Reconnect and try again."),
            )
        return dict(
            source="external_mcp",
            tool="read_trip_record",
            success=True,
            data=result.data,
            latency_ms=round(result.latency_ms, 2),
        )

    @app.get("/api/agent/skills")
    def skills(x_demo_agent: str = Header(default="")):
        runtime.staff(x_demo_agent)
        return dict(
            skills=[
                dict(
                    name=s.name,
                    description=s.description,
                    agents=s.agents,
                    enabled=s.enabled,
                    content=s.content,
                )
                for s in runtime.service.skills.skills
            ],
            errors=["A Skill could not be loaded; review the saved document." for _ in runtime.service.skills.errors],
            editable_content=runtime.service.skill_file.read_text(),
        )

    @app.post("/api/agent/skills/reload")
    async def reload_skills(x_demo_agent: str = Header(default=""), x_request_id: str = Header(default="")):
        runtime.staff(x_demo_agent)

        if status(runtime.store)["pending"]:
            raise HTTPException(
                409,
                ("A configuration release is still applying. Wait before reloading the runtime file."),
            )
        return await update_skill(
            SkillDocument(content=runtime.service.skill_file.read_text()),
            x_demo_agent,
            x_request_id,
        )

    @app.post("/api/agent/skills")
    async def update_skill(
        body: SkillDocument,
        x_demo_agent: str = Header(default=""),
        x_request_id: str = Header(default=""),
    ):
        runtime.staff(x_demo_agent)
        if not x_request_id or len(x_request_id) > 100:
            raise HTTPException(400, "A request ID is required for every change.")

        if re.search("[\\u4e00-\\u9fff]", body.content):
            raise HTTPException(422, "Use English for the ride Skill.")

        with TemporaryDirectory() as directory:
            candidate = Path(directory) / "SKILL.md"
            candidate.write_text(body.content)
            candidate_manager = SkillManager(directory)
            candidate_manager.load()
            if (
                candidate_manager.errors
                or not candidate_manager.skills
                or any(
                    (
                        not set(s.agents) <= {"general", "technical", "billing", "escalation"}
                        for s in candidate_manager.skills
                    )
                )
                or (not re.search("^# +\\S", body.content, re.M))
            ):
                raise HTTPException(422, "Provide a valid Skill document with a heading and content.")
        payload = ["skill", body.content]
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        cached = runtime.store.replay(staff_receipt_owner(), x_request_id, fingerprint)
        if cached is not None:
            return cached

        def commit_skill(db):
            sequence = enqueue(db, dict(kind="skill", content=body.content))
            return dict(
                status="accepted",
                configuration_sequence=sequence,
                skills=[
                    dict(
                        name=item.name,
                        description=item.description,
                        agents=item.agents,
                        enabled=item.enabled,
                        content=item.content,
                    )
                    for item in candidate_manager.skills
                ],
                errors=[],
                editable_content=body.content,
            )

        receipt = runtime.write(staff_receipt_owner(), x_request_id, payload, commit_skill)
        await runtime.configuration.flush()
        return receipt

    @app.get("/api/agent/knowledge")
    def knowledge_scopes(x_demo_agent: str = Header(default="")):
        runtime.staff(x_demo_agent)
        return dict(
            scopes=[
                dict(id=key, title=value["title"], version=value["version"])
                for key, value in runtime.catalog.policies.items()
            ]
        )

    @app.get("/api/agent/knowledge/{topic}")
    def knowledge(topic: str, x_demo_agent: str = Header(default="")):
        runtime.staff(x_demo_agent)
        if topic not in runtime.service.engine.knowledge:
            raise HTTPException(404, "Policy scope not found.")
        kb = runtime.service.engine.knowledge[topic]
        rows = kb._collection.get(include=["documents", "metadatas"])
        return dict(
            scope=topic,
            protected_document_ids=sorted(runtime.service.engine.protected_policy_ids(topic))
            if runtime.security.production
            else [],
            count=kb.doc_count,
            documents=[
                dict(id=i, content=d, metadata=m) for i, d, m in zip(rows["ids"], rows["documents"], rows["metadatas"])
            ],
            source_documents=runtime.service.engine.policy_sources(topic),
        )

    @app.post("/api/agent/knowledge")
    async def update_knowledge(
        body: PolicyDocument,
        x_demo_agent: str = Header(default=""),
        x_request_id: str = Header(default=""),
    ):
        runtime.staff(x_demo_agent)
        if not body.content.strip() or not body.title.strip() or (not body.document_id.strip()):
            raise HTTPException(422, "Document fields must contain text.")
        if not x_request_id or len(x_request_id) > 100:
            raise HTTPException(400, "A request ID is required for every change.")
        if body.topic not in runtime.service.engine.knowledge:
            raise HTTPException(404, "Policy scope not found.")
        if runtime.security.production and body.document_id.strip() in runtime.service.engine.protected_policy_ids(
            body.topic
        ):
            raise HTTPException(
                409,
                (
                    "Authoritative release documents are read-only. Publish a "
                    "reviewed catalog release to change financial "
                    "policy."
                ),
            )
        payload = ["knowledge", body.model_dump()]
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        cached = runtime.store.replay(staff_receipt_owner(), x_request_id, fingerprint)
        if cached is not None:
            return cached
        if re.search("[\\u4e00-\\u9fff]", body.title + body.content):
            raise HTTPException(422, "Use English for ride policy documents.")

        count = len(runtime.service.engine.knowledge[body.topic]._chunk_text(body.content, chunk_size=500))

        def commit_policy(db):
            sequence = enqueue(db, dict(kind="policy", topic=body.topic, document=body.model_dump()))
            return dict(
                status="accepted",
                configuration_sequence=sequence,
                scope=body.topic,
                added_chunks=count,
                document_id=body.document_id,
            )

        receipt = runtime.write(staff_receipt_owner(), x_request_id, payload, commit_policy)
        await runtime.configuration.flush()
        return receipt

    @app.get("/api/agent/configuration")
    def configuration_status(x_demo_agent: str = Header(default="")):
        runtime.staff(x_demo_agent)

        return status(runtime.store)

    @app.post("/api/agent/evaluations")
    async def evaluate(x_demo_agent: str = Header(default="")):
        runtime.staff(x_demo_agent)

        return await evaluate_engine(runtime.service.engine)
