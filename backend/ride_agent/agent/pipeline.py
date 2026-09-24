"""Explicit case-analysis steps with scoped evidence and durable context."""

import asyncio
import hashlib
import json
import math
import os
import time
from pathlib import Path

import chromadb
from anthropic import AsyncAnthropic
from fastapi import HTTPException

from ride_agent.agent.agents import RoleAgent
from ride_agent.agent.case_tools import CaseTools
from ride_agent.agent.composer import ResponseComposer
from ride_agent.agent.intent import IntentCategory, IntentRecognizer
from ride_agent.agent.intent_embedding import IntentEmbedding
from ride_agent.agent.memory import CaseMemory
from ride_agent.agent.orchestrator import AgentOrchestrator
from ride_agent.agent.types import AgentType, Request
from ride_agent.domain.claims import ClaimLedger
from ride_agent.integrations.model_provider import LocalModel
from ride_agent.ops.configuration import status
from ride_agent.ops.operations import RideOperations
from ride_agent.retrieval.knowledge_base import KnowledgeBase
from ride_agent.retrieval.policy_index import MCPToolManager, Tool


class LocalHashEmbedding:
    "Deterministic local vectors; actual cosine retrieval, not a neural embedding model."

    def __call__(self, input):
        vectors = []
        for text in input:
            vector = IntentRecognizer._local_embedding(text)
            norm = math.sqrt(sum((v * v for v in vector))) or 1
            vectors.append([v / norm for v in vector])
        return vectors


class RideEngine:
    def __init__(self, support):
        self.support = support
        settings = support.model_settings
        self.mode = settings.mode
        self.model = settings.model
        if self.mode == "live":
            self.client = AsyncAnthropic(
                api_key=settings.api_key,
                base_url=settings.base_url,
                timeout=12,
                max_retries=0,
            )
        else:
            self.client = LocalModel()
        embedding = IntentEmbedding()
        embedding.provider, embedding.enabled, embedding.key = ("local", False, "")
        self.recognizer = IntentRecognizer(api_key="unused", client=self.client, model=self.model, embedding=embedding)
        self.rag_tools = MCPToolManager(api_key="unused", client=self.client, model=self.model)
        path = str(Path(support.store.path).with_suffix(".vectors"))
        Path(path).mkdir(parents=True, exist_ok=True, mode=448)
        if os.name == "posix":
            Path(path).chmod(448)
        self.chroma = chromadb.PersistentClient(
            path=path,
            settings=chromadb.Settings(
                anonymized_telemetry=False,
                chroma_product_telemetry_impl="ride_agent.ops.telemetry.LocalTelemetry",
                chroma_telemetry_impl="ride_agent.ops.telemetry.LocalTelemetry",
            ),
        )
        self.hash_embedding = LocalHashEmbedding()
        self.knowledge = {}
        self.documents = {}
        for topic, policy in support.catalog.policies.items():
            collection = self.chroma.get_or_create_collection(
                "ride_" + topic + "_v1",
                embedding_function=LocalHashEmbedding(),
                metadata={"hnsw:space": "cosine"},
            )
            kb = KnowledgeBase(collection=collection)
            documents = [
                dict(
                    document_id=policy["id"],
                    title=policy["title"],
                    content=(f"{policy['title']}. {policy['region']}. {policy['version']}. {policy['text']}"),
                ),
                dict(
                    document_id=topic + "-evidence",
                    title="Evidence and uncertainty",
                    content=(
                        "A rider statement and a recorded event are separate "
                        "sources. Preserve conflicting pickup and payment evidence. "
                        "Ask only for missing information; do not assume that GPS "
                        "arrival proves a successful "
                        "pickup."
                    ),
                ),
                dict(
                    document_id=topic + "-handoff",
                    title="Human review and handoff",
                    content=(
                        "A request for human review preserves the original "
                        "explanation, all evidence, and prior actions. A specialist "
                        "has not accepted the case until an acceptance event is "
                        "recorded."
                    ),
                ),
                dict(
                    document_id=topic + "-payments",
                    title="Payment execution boundary",
                    content=(
                        "Analysis is read-only. An adjustment requires a valid "
                        "server-side proposal and explicit rider confirmation. No "
                        "real money moves in this "
                        "demonstration."
                    ),
                ),
            ]
            if kb.doc_count == 0:
                kb.add_documents(documents)
            self.knowledge[topic] = kb
            self.documents[topic] = documents
        self.rag_tools.register(
            Tool(
                name="ride_policy_search",
                description="Retrieve scoped English ride policy",
                handler=self.retrieve,
                schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                    },
                    "required": ["query"],
                },
                cache_ttl=60,
                timeout_s=5,
            )
        )
        self.monitor_pool = {
            kind: [RoleAgent(self.client, self.model, support.skills, kind=kind)] for kind in AgentType
        }
        self.monitor_orchestrator = AgentOrchestrator(
            api_key="unused",
            client=self.client,
            intent_recognizer=self.recognizer,
            composer=ResponseComposer(self.client, self.model, support.skills),
            agent_pool=self.monitor_pool,
            skill_manager=support.skills,
        )

        self.operations = RideOperations(self, self.monitor_orchestrator)

    def protected_policy_ids(self, topic):
        return {doc["document_id"] for doc in self.documents[topic]}

    def validate_authoritative_policies(self):
        for topic, documents in self.documents.items():
            for document in documents:
                identity = hashlib.sha256(
                    json.dumps(["explicit", document["document_id"]], ensure_ascii=True).encode()
                ).hexdigest()
                rows = self.knowledge[topic]._collection.get(
                    where={"document_id": identity}, include=["metadatas", "documents"]
                )
                expected = hashlib.sha256(document["content"].encode()).hexdigest()
                chunks = self.knowledge[topic]._chunk_text(document["content"], chunk_size=500)
                expected_rows = {identity + ":" + str(i): (text, i) for i, text in enumerate(chunks)}
                actual = {
                    key: (text, meta.get("chunk_index"))
                    for key, text, meta in zip(rows["ids"], rows["documents"], rows["metadatas"])
                }
                if actual != expected_rows or any((meta.get("version") != expected for meta in rows["metadatas"])):
                    raise RuntimeError(
                        (
                            "Authoritative policy index differs from the configured "
                            "release. Restore the index or use a reviewed "
                            "release."
                        )
                    )

    def policy_sources(self, topic):
        with self.support.store.connect() as db:
            saved = {
                row[0]: json.loads(row[1])
                for row in db.execute(
                    "SELECT document_id,data FROM policy_sources WHERE topic=?",
                    (topic,),
                )
            }
        versions = {
            item.get("version") for item in self.knowledge[topic]._collection.get(include=["metadatas"])["metadatas"]
        }
        for doc in self.documents[topic]:
            if doc["document_id"] not in saved and hashlib.sha256(doc["content"].encode()).hexdigest() in versions:
                saved[doc["document_id"]] = doc
        return [saved[key] for key in sorted(saved)]

    async def required_policy_chunks(self, topic):
        "Read current indexed anchors; never substitute seed prose for missing evidence."
        identities = [self.support.catalog.policies[topic]["id"], topic + "-payments"]
        chunks = []
        for identity in identities:
            document_id = hashlib.sha256(json.dumps(["explicit", identity], ensure_ascii=True).encode()).hexdigest()
            records = await asyncio.to_thread(
                self.knowledge[topic]._collection.get,
                where={"document_id": document_id},
                include=["documents", "metadatas"],
            )
            if not records["ids"]:
                raise RuntimeError("Required policy evidence is missing from the active index")
            for content, meta in sorted(
                zip(records["documents"], records["metadatas"]),
                key=lambda item: item[1]["chunk_index"],
            ):
                chunks.append(
                    dict(
                        title=meta["title"],
                        content=content,
                        document_id=meta["document_id"],
                        chunk=meta["chunk_index"],
                        version=meta["version"],
                        score=1.0,
                        required_policy=True,
                    )
                )
        if len(chunks) > 5:
            raise RuntimeError(("Required policy exceeds the evidence budget; specialist review is needed"))
        return chunks

    async def retrieve(self, params, context):
        return await self.knowledge[context["topic"]].search_async(params["query"], params.get("top_k", 5))

    async def run(self, trip_id, text, case=None):

        async with self.support.configuration_gate.read():
            if status(self.support.store)["pending"]:
                raise HTTPException(
                    503,
                    ("A committed configuration update is still applying. Retry after workspace recovery."),
                )
            return await self._run(trip_id, text, case)

    async def _run(self, trip_id, text, case=None):
        memory, memory_context, claim_context, topic = await self.load_context(trip_id, text, case)
        intent, history = await self.recognize_intent(text, memory_context)
        tools = self.build_tools(trip_id, topic, claim_context, case)
        specs = tools.specifications()
        agent_mode, skills, pool, orchestrator = self.build_orchestrator(trip_id, case, specs)
        req = self.build_request(trip_id, text, case, memory_context, claim_context, history, intent)
        start = time.monotonic()
        result = await self.orchestrate(orchestrator, req, agent_mode, intent, tools.retrievals, skills)
        if isinstance(result, dict):
            return result
        analysis = self.validate(
            result,
            agent_mode,
            intent,
            tools.citations,
            tools.retrievals,
            pool,
            skills,
            start,
        )
        return await self.persist(memory, text, analysis, intent)

    async def close(self):
        await self.operations.close()
        await self.client.close()

    async def load_context(self, trip_id, text, case):

        memory = CaseMemory(self, case, trip_id)
        memory_context = await memory.prepare(text)
        claim_context = getattr(memory_context, "claims", None)
        if claim_context is None:
            claim_context = ClaimLedger(
                [
                    dict(role="rider", content=m.content)
                    for m in memory_context.recent_messages
                    if m.role.value == "user"
                ]
                + [dict(role="rider", content=text)]
            ).context(text)
        trip = self.support.trip(trip_id)
        topic = self.support.catalog.scope(trip)
        return (memory, memory_context, claim_context, topic)

    async def recognize_intent(self, text, memory_context):
        history = [dict(role=m.role.value, content=m.content) for m in memory_context.recent_messages[-8:]]
        return await self.recognizer.recognize(text, history=history), history

    def build_tools(self, trip_id, topic, claim_context, case):
        return CaseTools(self, trip_id, topic, claim_context, case)

    def build_orchestrator(self, trip_id, case, specs):
        agent_mode = (case or {}).get("agent_mode", "single")
        if agent_mode not in ("single", "multi"):
            raise ValueError("Agent mode must be single or multi")
        skills = self.support.skills
        pool = {
            kind: [
                RoleAgent(
                    self.client,
                    self.model,
                    skills,
                    generalist=agent_mode == "single" and kind == AgentType.GENERAL,
                    kind=kind,
                )
            ]
            for kind in AgentType
        }
        for kind in AgentType:
            pool[kind][0].stats = self.monitor_pool[kind][0].stats
        orchestrator = AgentOrchestrator(
            api_key="unused",
            client=self.client,
            intent_recognizer=self.recognizer,
            composer=ResponseComposer(self.client, self.model, skills),
            agent_pool=pool,
            skill_manager=skills,
        )
        orchestrator.execution_mode = agent_mode
        specs.update(self.operations.scoped_tools(trip_id))
        orchestrator.set_shared_tools(specs)
        self.monitor_orchestrator.set_shared_tools(specs)
        return (agent_mode, skills, pool, orchestrator)

    def build_request(self, trip_id, text, case, memory_context, claim_context, history, intent):
        req = Request(
            message=text,
            user_id=(case or {}).get("owner", "demo"),
            conv_id=(case or {}).get("id", trip_id),
            history=history,
            context=json.dumps(
                dict(
                    active_rider_account=claim_context,
                    conversation_state=memory_context.state,
                    trip_id=trip_id,
                    case_status=(case or {}).get("status", "new"),
                    adjustment=(case or {}).get("adjustment"),
                    evidence=[{k: v for k, v in e.items() if k != "content"} for e in (case or {}).get("evidence", [])],
                )
            ),
            intent=intent.intent,
            intent_group=intent.intent_group,
            urgency=intent.urgency,
            intent_confidence=intent.confidence,
            entities=intent.entities,
        )
        return req

    def validate(self, result, agent_mode, intent, citations, retrievals, pool, skills, start):
        mcp_connected = any((c.state == "connected" for c in self.operations.bridge.connections.values()))
        if (
            not mcp_connected
            or not citations
            or any((not row["success"] for row in retrievals))
            or any(
                (
                    t.get("source") == "external_mcp" and (not t.get("result_success", t.get("success")))
                    for t in result.tool_traces
                )
            )
        ):
            if result.status == "success":
                result.status = "partial"
        content = result.response
        analysis = dict(
            id=result.request_id,
            agent_mode=agent_mode,
            memory_mode="durable",
            mode=self.mode,
            model=self.model,
            status=result.status,
            response=content,
            intent=intent.intent.value,
            source_scores=intent.source_scores,
            embedding_source=intent.embedding_source,
            agents=[a.value for a in result.agent_types],
            planned_agents=[result.primary_agent.value] + [a.value for a in result.supporting_agents],
            tools=result.tool_traces,
            citations=list(citations.values()),
            retrievals=retrievals,
            routing_reason=result.routing_reason,
            latency_ms=round((time.monotonic() - start) * 1000, 2),
            handoff=intent.intent in (IntentCategory.HUMAN_HANDOFF, IntentCategory.ESCALATION),
            roles={a.value: pool[a][0].profile.role for a in AgentType},
            skills=[s.name for s in skills.skills],
        )
        analysis["mcp_status"] = "connected" if mcp_connected else "unavailable"
        self.operations.monitor._prom["requests_total"].inc()
        self.operations.monitor._prom["chat_latency_ms"].labels(status=analysis["status"]).observe(
            analysis["latency_ms"]
        )
        return analysis

    async def orchestrate(self, orchestrator, req, agent_mode, intent, retrievals, skills):
        try:
            async with asyncio.timeout(25):
                result = await orchestrator.run(req)
        except TimeoutError:
            return dict(
                id=req.request_id,
                agent_mode=agent_mode,
                memory_mode="durable",
                mode=self.mode,
                model=self.model,
                status="failed",
                response=(
                    "The analysis timed out. No payment action was taken. Your case remains available for human review."
                ),
                intent=intent.intent.value,
                source_scores=intent.source_scores,
                agents=[],
                tools=[],
                citations=[],
                retrievals=retrievals,
                routing_reason="Analysis exceeded its time budget.",
                latency_ms=25000,
                handoff=False,
                skills=[s.name for s in skills.skills],
                roles={},
                planned_agents=[],
                embedding_source=intent.embedding_source,
            )
        return result

    async def persist(self, memory, text, analysis, intent):
        return await memory.finish(text, analysis, intent)
