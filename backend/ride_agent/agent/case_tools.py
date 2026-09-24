"""Request-scoped read-only tool registry with explicit evidence collectors."""

from ride_agent.agent.tools import AgentToolSpec


class CaseTools:
    def __init__(self, engine, trip_id, topic, claim_context, case):
        self.engine = engine
        self.trip_id = trip_id
        self.topic = topic
        self.claim_context = claim_context
        self.case = case
        self.citations = {}
        self.retrievals = []

    def specifications(self):
        specs = {
            "get_case_context": AgentToolSpec(
                "get_case_context",
                ("Read case-scoped rider claims, corrections and evidence; claims are not verified facts"),
                {"type": "object", "properties": {}, "additionalProperties": False},
                self.context_tool,
            ),
            "get_trip": AgentToolSpec(
                "get_trip",
                "Read the linked trip facts",
                {"type": "object", "properties": {}, "additionalProperties": False},
                self.trip_tool,
                allowed_agents=("general", "technical", "escalation"),
            ),
            "get_charge_breakdown": AgentToolSpec(
                "get_charge_breakdown",
                "Read settled and pending payment records",
                {"type": "object", "properties": {}, "additionalProperties": False},
                self.charge_tool,
                allowed_agents=("billing",),
            ),
            "search_knowledge_base": AgentToolSpec(
                "search_knowledge_base",
                "Retrieve relevant scoped policy evidence; required for policy claims",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 1, "maxLength": 2000},
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 5},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                self.policy_tool,
            ),
        }
        return specs

    async def trip_tool(self, req, args):
        result = await self.engine.support.tools.call("get_trip", {"trip_id": self.trip_id})
        if not result.success:
            raise RuntimeError("Trip facts unavailable")
        return result.data

    async def charge_tool(self, req, args):
        result = await self.engine.support.tools.call("get_charge_breakdown", {"trip_id": self.trip_id})
        if not result.success:
            raise RuntimeError("Charge facts unavailable")
        return result.data

    async def policy_tool(self, req, args):
        scoped_query = f"{self.topic} policy: {args['query']}"
        result = await self.engine.rag_tools.search_with_rewrite(
            "ride_policy_search",
            scoped_query,
            top_k=args.get("top_k", 2),
            context={"topic": self.topic},
        )
        coverage = False
        try:
            anchors = await self.engine.required_policy_chunks(self.topic) if result.success else []
            chosen = {(d["document_id"], d["chunk"]): d for d in anchors}
            for item in result.data or []:
                chosen.setdefault((item["document_id"], item["chunk"]), item)
            items = list(chosen.values())[: max(args.get("top_k", 2), len(anchors))]
            coverage = bool(anchors)
        except Exception:
            items = []
        success = result.success and coverage
        for item in items:
            self.citations[item["document_id"], item["chunk"]] = item
        self.retrievals.append(
            dict(
                query=args["query"],
                success=success,
                reranked=result.reranked,
                documents=len(items),
                scope=self.topic,
                required_policy_covered=coverage,
            )
        )
        return dict(
            success=success,
            results=items,
            error=None if success else "Required policy evidence is unavailable; specialist review is needed",
        )

    async def context_tool(self, req, args):
        return dict(
            **self.claim_context,
            evidence=[
                {k: v for k, v in item.items() if k != "content"} for item in (self.case or {}).get("evidence", [])
            ],
        )
