import asyncio
import logging
import os
import time
from collections import deque
from dataclasses import replace
from datetime import datetime
from typing import Any, Dict, List, Optional

from anthropic import AsyncAnthropic

from ride_agent.agent.agents import (
    BillingAgent,
    EscalationAgent,
    GeneralAgent,
    RoleAgent,
    TechnicalAgent,
)
from ride_agent.agent.composer import ResponseComposer
from ride_agent.agent.conversation_state import merge_state
from ride_agent.agent.intent import IntentCategory, IntentRecognizer, UrgencyLevel
from ride_agent.agent.redaction import redact
from ride_agent.agent.tools import (
    AgentToolSpec,
    build_shared_rag_tools,
)
from ride_agent.agent.types import (
    AgentResponse,
    AgentType,
    OrchestratorResult,
    Request,
    RoutingDecision,
    _env_int,
)

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """Agent orchestrator."""

    _INTENT_ROUTING: Dict[IntentCategory, AgentType] = {
        IntentCategory.TECHNICAL: AgentType.TECHNICAL,
        IntentCategory.TECHNICAL_LOGIN: AgentType.TECHNICAL,
        IntentCategory.TECHNICAL_CRASH: AgentType.TECHNICAL,
        IntentCategory.BILLING: AgentType.BILLING,
        IntentCategory.REFUND: AgentType.BILLING,
        IntentCategory.INVOICE: AgentType.BILLING,
        IntentCategory.PAYMENT_ISSUE: AgentType.BILLING,
        IntentCategory.ACCOUNT: AgentType.BILLING,
        IntentCategory.ACCOUNT_SECURITY: AgentType.BILLING,
        IntentCategory.ESCALATION: AgentType.ESCALATION,
        IntentCategory.HUMAN_HANDOFF: AgentType.ESCALATION,
    }
    MONITOR_FALLBACK_PENALTY = 0.5

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        model: str = "claude-3-5-sonnet-20241022",
        skill_manager: Optional[Any] = None,
        rag_tool_manager: Optional[Any] = None,
        client: Any = None,
        intent_recognizer: Any = None,
        composer: Any = None,
        agent_pool: Any = None,
    ):
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        client = client if client is not None else AsyncAnthropic(**kwargs)
        self._intent_recognizer = (
            intent_recognizer
            if intent_recognizer is not None
            else IntentRecognizer(api_key=api_key, base_url=base_url, model=model)
        )
        self._skill_manager = skill_manager
        self._composer = composer if composer is not None else ResponseComposer(client, model, skill_manager)
        self._shared_tools: Dict[str, AgentToolSpec] = {}
        self._recent_tool_traces = deque(maxlen=_env_int("LLM_AGENT_SYSTEM_TOOL_TRACE_MAX", 200))
        self._pool: Dict[AgentType, List[RoleAgent]] = {
            AgentType.GENERAL: [self._make_agent(GeneralAgent, client, model, skill_manager)],
            AgentType.TECHNICAL: [self._make_agent(TechnicalAgent, client, model, skill_manager)],
            AgentType.BILLING: [self._make_agent(BillingAgent, client, model, skill_manager)],
            AgentType.ESCALATION: [self._make_agent(EscalationAgent, client, model, skill_manager)],
        }
        if agent_pool is not None:
            self._pool = agent_pool
        self.set_shared_tools(build_shared_rag_tools(rag_tool_manager))

    @staticmethod
    def _make_agent(
        agent_cls: type[RoleAgent],
        client: AsyncAnthropic,
        default_model: str,
        skill_manager: Optional[Any],
    ) -> RoleAgent:
        """Make agent."""
        profile = agent_cls.profile
        env_name = f"LLM_AGENT_SYSTEM_{agent_cls.agent_type.value.upper()}_MODEL"
        model = os.getenv(env_name, "").strip() or profile.model
        configured_profile = replace(profile, model=model) if model else profile
        return agent_cls(client, default_model, skill_manager, profile=configured_profile)

    def set_skill_manager(self, skill_manager: Optional[Any]) -> None:
        """Set skill manager."""
        self._skill_manager = skill_manager
        self._composer._skill_manager = skill_manager
        for agents in self._pool.values():
            for agent in agents:
                agent._skill_manager = skill_manager

    def set_shared_tools(self, tools: Optional[Dict[str, AgentToolSpec]]) -> None:
        """Set shared tools."""
        self._shared_tools = dict(tools or {})
        for agents in self._pool.values():
            for agent in agents:
                agent.set_shared_tools(self._shared_tools)

    async def recognize_intent(self, message: str, history: Optional[List[Dict[str, str]]] = None):
        """Recognize intent."""
        return await self._intent_recognizer.recognize(message, history=history)

    def _record_tool_trace(self, result: OrchestratorResult) -> None:
        trace = {
            "request_id": result.request_id,
            "timestamp": datetime.now().isoformat(),
            "intent": result.intent.value if result.intent else None,
            "primary_agent": result.primary_agent.value if result.primary_agent else None,
            "supporting_agents": [agent.value for agent in result.supporting_agents],
            "tools_used": list(result.tools_used),
            "tool_calls": list(result.tool_traces),
            "escalated": result.escalated,
            "latency_ms": round(result.latency_ms, 1),
            "status": result.status,
            "retryable": result.retryable,
        }

        self._recent_tool_traces.append(redact(trace))

    def get_tool_trace(self, request_id: str) -> Optional[Dict[str, Any]]:
        for trace in reversed(self._recent_tool_traces):
            if trace.get("request_id") == request_id:
                return trace
        return None

    def get_recent_tool_traces(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self._recent_tool_traces:
            return []
        limit = max(1, min(int(limit or 20), len(self._recent_tool_traces)))
        return list(reversed(list(self._recent_tool_traces)[-limit:]))

    async def run(self, req: Request) -> OrchestratorResult:
        """Run."""
        t0 = time.monotonic()
        if req.intent is None:
            intent_result = await self._intent_recognizer.recognize(req.message, history=req.history)
            req.intent = intent_result.intent
            req.intent_group = intent_result.intent_group
            req.urgency = intent_result.urgency
            req.intent_confidence = intent_result.confidence
            req.entities = intent_result.entities
        if req.conversation_state is not None:
            req.conversation_state = merge_state(
                req.conversation_state,
                req.entities,
                req.intent_group,
                req.message,
                req.request_id,
            )
            req.entities = req.conversation_state["entities"]
        if self._needs_clarification(req):
            result = OrchestratorResult(
                request_id=req.request_id,
                response=(
                    "Please clarify whether this concerns trip records, a "
                    "charge, account access or a technical "
                    "problem."
                ),
                agent_type=AgentType.GENERAL,
                intent=req.intent,
                escalated=False,
                latency_ms=(time.monotonic() - t0) * 1000,
                agent_types=[AgentType.GENERAL],
                primary_agent=AgentType.GENERAL,
                routing_reason="Low-confidence OTHER intent; request clarification",
                routing_confidence=req.intent_confidence,
            )
            self._record_tool_trace(result)
            return result
        decision = self._route_decision(req)
        if decision.multi_agent:
            return await self.run_parallel(req, decision)
        response = await self._execute(req, decision.primary_agent)
        escalated = False
        if (
            response.escalate
            or req.urgency == UrgencyLevel.CRITICAL
            or req.intent in (IntentCategory.ESCALATION, IntentCategory.HUMAN_HANDOFF)
        ):
            escalated = True
            logger.warning(f"Request {req.request_id} triggered escalation: urgency={req.urgency}")
        result = OrchestratorResult(
            request_id=req.request_id,
            response=response.content,
            agent_type=response.agent_type,
            intent=req.intent,
            escalated=escalated,
            latency_ms=(time.monotonic() - t0) * 1000,
            agent_types=[response.agent_type],
            primary_agent=decision.primary_agent,
            supporting_agents=[],
            tools_used=list(response.tools_used),
            tool_traces=list(response.tool_traces),
            routing_reason=decision.reason,
            routing_confidence=decision.confidence,
            status=response.status if response.success else "failed",
            retryable=not response.success and all((attempt.get("retry_safe", False) for attempt in req.tool_attempts)),
            error_code=response.error_code if not response.success else None,
        )
        self._record_tool_trace(result)
        return result

    async def run_parallel(self, req: Request, decision: RoutingDecision) -> OrchestratorResult:
        """Run parallel."""
        t0 = time.monotonic()
        agent_types = decision.agent_types
        tasks = [self._execute(req, at) for at in agent_types]
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        valid_responses = [r for r in responses if isinstance(r, AgentResponse)]
        composed = await self._composer.compose_result(req, valid_responses)
        if len(valid_responses) != len(responses) and composed.status == "success":
            composed.status = "partial"
        escalated = any((isinstance(r, AgentResponse) and r.escalate for r in responses))
        tools_used = list(
            dict.fromkeys((tool_name for response in valid_responses for tool_name in response.tools_used))
        )
        tool_traces = [trace for response in valid_responses for trace in response.tool_traces]
        result = OrchestratorResult(
            request_id=req.request_id,
            response=composed.content,
            agent_type=decision.primary_agent,
            intent=req.intent,
            escalated=escalated,
            latency_ms=(time.monotonic() - t0) * 1000,
            agent_types=[r.agent_type for r in responses if isinstance(r, AgentResponse) and r.success] or agent_types,
            primary_agent=decision.primary_agent,
            supporting_agents=decision.supporting_agents,
            tools_used=tools_used,
            tool_traces=tool_traces,
            routing_reason=decision.reason,
            routing_confidence=decision.confidence,
            status=composed.status,
            retryable=composed.status == "failed"
            and all((attempt.get("retry_safe", False) for attempt in req.tool_attempts)),
            error_code="agent_failed" if composed.status == "failed" else None,
        )
        self._record_tool_trace(result)
        return result

    def _route(self, intent: Optional[IntentCategory], urgency: Optional[UrgencyLevel]) -> AgentType:
        """Route."""
        if urgency == UrgencyLevel.CRITICAL:
            return AgentType.ESCALATION
        if intent and intent in self._INTENT_ROUTING:
            target = self._INTENT_ROUTING[intent]
            if target in self._pool and self._pool[target]:
                return target
        return AgentType.GENERAL

    def _base_route_decision(self, req: Request) -> RoutingDecision:
        """Route decision."""
        if req.urgency == UrgencyLevel.CRITICAL:
            return RoutingDecision(
                primary_agent=AgentType.ESCALATION,
                reason="CRITICAL urgency requires escalation",
                confidence=1.0,
            )
        if req.intent in (IntentCategory.ESCALATION, IntentCategory.HUMAN_HANDOFF):
            return RoutingDecision(
                primary_agent=AgentType.ESCALATION,
                reason=(f"Intent {(req.intent.value if req.intent else 'unknown')} requires escalation"),
                confidence=max(req.intent_confidence, 0.8),
            )
        scores = self._domain_scores(req)
        degraded_agents = {
            agent_type
            for agent_type in (AgentType.TECHNICAL, AgentType.BILLING)
            if scores.get(agent_type, 0.0) > 0 and self._monitor_degraded(agent_type)
        }
        available_scores = {
            agent_type: score
            for agent_type, score in scores.items()
            if agent_type == AgentType.GENERAL or (self._pool.get(agent_type) and agent_type not in degraded_agents)
        }
        if not available_scores:
            return RoutingDecision(
                primary_agent=AgentType.GENERAL,
                reason="No specialist available; falling back to the general agent",
                confidence=0.1,
            )
        ordered = sorted(available_scores.items(), key=lambda item: item[1], reverse=True)
        primary_agent, primary_score = ordered[0]
        collaboration_targets = self._collaboration_targets(req)
        supporting_agents = [
            agent_type
            for agent_type in collaboration_targets
            if agent_type != primary_agent and agent_type in available_scores
        ]
        if not supporting_agents:
            supporting_agents = [
                agent_type
                for agent_type, score in ordered[1:]
                if agent_type != AgentType.GENERAL and score >= 0.45 and (score >= primary_score * 0.55)
            ]
        reason = self._routing_reason(req, available_scores, primary_agent, supporting_agents)
        if degraded_agents:
            degraded_text = ", ".join(sorted((agent.value for agent in degraded_agents)))
            reason += f", monitor_fallback=[{degraded_text}]"
        return RoutingDecision(
            primary_agent=primary_agent,
            supporting_agents=supporting_agents,
            reason=reason,
            confidence=round(min(primary_score, 1.0), 3),
        )

    def _base_domain_scores(self, req: Request) -> Dict[AgentType, float]:
        """Domain scores."""
        msg = req.message.lower()
        scores = {
            AgentType.GENERAL: 0.1,
            AgentType.TECHNICAL: 0.0,
            AgentType.BILLING: 0.0,
        }
        if req.intent in (
            IntentCategory.QUERY,
            IntentCategory.ORDER_STATUS,
            IntentCategory.LOGISTICS,
            IntentCategory.REQUEST,
            IntentCategory.COMPLAINT,
            IntentCategory.GREETING,
            IntentCategory.FEEDBACK,
            IntentCategory.OTHER,
        ):
            scores[AgentType.GENERAL] += 0.55
        if req.intent in (
            IntentCategory.TECHNICAL,
            IntentCategory.TECHNICAL_LOGIN,
            IntentCategory.TECHNICAL_CRASH,
        ):
            scores[AgentType.TECHNICAL] += 0.75
        if req.intent in (
            IntentCategory.BILLING,
            IntentCategory.ACCOUNT,
            IntentCategory.ACCOUNT_SECURITY,
            IntentCategory.REFUND,
            IntentCategory.INVOICE,
            IntentCategory.PAYMENT_ISSUE,
        ):
            scores[AgentType.BILLING] += 0.75
        technical_kws = [
            "崩溃",
            "报错",
            "error",
            "crash",
            "无法登录",
            "登录失败",
            "500",
            "401",
            "验证码",
        ]
        billing_kws = [
            "退款",
            "退货",
            "扣款",
            "发票",
            "账单",
            "支付",
            "订阅",
            "refund",
            "invoice",
            "多扣",
        ]
        general_kws = ["订单", "物流", "快递", "配送", "会员", "积分", "咨询", "帮助"]
        technical_hits = sum((1 for kw in technical_kws if kw in msg))
        billing_hits = sum((1 for kw in billing_kws if kw in msg))
        general_hits = sum((1 for kw in general_kws if kw in msg))
        scores[AgentType.TECHNICAL] += min(0.45, technical_hits * 0.18)
        scores[AgentType.BILLING] += min(0.45, billing_hits * 0.18)
        scores[AgentType.GENERAL] += min(0.35, general_hits * 0.12)
        entities = req.entities or {}
        if entities.get("error_code"):
            scores[AgentType.TECHNICAL] += 0.2
        if entities.get("amount"):
            scores[AgentType.BILLING] += 0.15
        if entities.get("order_id"):
            scores[AgentType.GENERAL] += 0.1
        return {agent_type: round(score, 3) for agent_type, score in scores.items()}

    @staticmethod
    def _routing_reason(
        req: Request,
        scores: Dict[AgentType, float],
        primary_agent: AgentType,
        supporting_agents: List[AgentType],
    ) -> str:
        score_text = ", ".join(
            (
                f"{agent_type.value}={score:.2f}"
                for agent_type, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)
            )
        )
        support_text = ", ".join((agent.value for agent in supporting_agents)) or "none"
        intent = req.intent.value if req.intent else "unknown"
        return (
            "intent="
            f"{intent}"
            ", group="
            f"{req.intent_group or 'unknown'}"
            ", primary="
            f"{primary_agent.value}"
            ", supporting="
            f"{support_text}"
            ", scores=["
            f"{score_text}"
            "]"
        )

    def _base_collaboration_targets(self, req: Request) -> List[AgentType]:
        """Collaboration targets."""
        msg = req.message.lower()
        targets: List[AgentType] = []
        technical_kws = [
            "崩溃",
            "报错",
            "error",
            "crash",
            "无法登录",
            "登录失败",
            "500",
            "401",
        ]
        billing_kws = [
            "退款",
            "扣款",
            "发票",
            "账单",
            "支付",
            "订阅",
            "refund",
            "invoice",
        ]
        if req.intent in (
            IntentCategory.TECHNICAL,
            IntentCategory.TECHNICAL_LOGIN,
            IntentCategory.TECHNICAL_CRASH,
        ) or any((kw in msg for kw in technical_kws)):
            targets.append(AgentType.TECHNICAL)
        if req.intent in (
            IntentCategory.BILLING,
            IntentCategory.ACCOUNT,
            IntentCategory.ACCOUNT_SECURITY,
            IntentCategory.REFUND,
            IntentCategory.INVOICE,
            IntentCategory.PAYMENT_ISSUE,
        ) or any((kw in msg for kw in billing_kws)):
            targets.append(AgentType.BILLING)
        deduped = list(dict.fromkeys(targets))
        return [agent_type for agent_type in deduped if self._pool.get(agent_type)]

    @staticmethod
    def _needs_clarification(req: Request) -> bool:
        """Needs clarification."""
        if req.intent != IntentCategory.OTHER:
            return False
        text = (req.message or "").strip()
        if len(text) <= 2:
            return False
        return req.intent_confidence < 0.5

    def _best_agent(self, agent_type: AgentType) -> Optional[RoleAgent]:
        """Best agent."""
        agents = self._pool.get(agent_type, [])
        if not agents:
            return None
        eligible = [
            a for a in agents if agent_type not in (AgentType.TECHNICAL, AgentType.BILLING) or a.stats.available()
        ]
        return max(eligible, key=lambda a: a.stats.routing_score()) if eligible else None

    def _monitor_degraded(self, agent_type: AgentType) -> bool:
        """Monitor degraded."""
        if agent_type in (AgentType.GENERAL, AgentType.ESCALATION):
            return False
        agents = self._pool.get(agent_type, [])
        if not agents:
            return False
        return all((not agent.stats.available() for agent in agents))

    async def _execute(self, req: Request, agent_type: AgentType) -> AgentResponse:
        """Execute."""
        agent = self._best_agent(agent_type)
        if agent is None:
            agent = self._best_agent(AgentType.GENERAL)
        if agent is None:
            return AgentResponse(
                agent_type=AgentType.GENERAL,
                content="Service temporarily unavailable; retry later.",
                success=False,
                status="failed",
                error_code="agent_unavailable",
            )
        response = await agent.handle(req)
        if not response.success and any((not attempt.get("retry_safe", False) for attempt in req.tool_attempts)):
            return response
        if not response.success and agent_type not in (
            AgentType.GENERAL,
            AgentType.ESCALATION,
        ):
            logger.warning(f"{agent_type.value} failed; falling back to the general agent")
            fallback = self._best_agent(AgentType.GENERAL)
            if fallback:
                original = response
                response = await fallback.handle(req)
                response.tools_used = list(dict.fromkeys(original.tools_used + response.tools_used))
                response.tool_traces = (
                    original.tool_traces
                    + [
                        {
                            "event": "agent_fallback",
                            "from_agent": original.agent_type.value,
                            "to_agent": response.agent_type.value,
                            "success": response.success,
                        }
                    ]
                    + response.tool_traces
                )
        return response

    def get_stats(self) -> Dict[str, Any]:
        result = {}
        for agent_type, agents in self._pool.items():
            for i, agent in enumerate(agents):
                key = f"{agent_type.value}_{i}"
                recent_n, recent_sr, recent_ms = agent.stats.recent()
                result[key] = {
                    "recent_total": recent_n,
                    "recent_success_rate": recent_sr,
                    "recent_avg_ms": recent_ms,
                    "recovery_state": "probing"
                    if agent.stats.probe_running
                    else "cooldown"
                    if agent.stats.retry_at
                    else "healthy",
                    "total": agent.stats.total,
                    "success_rate": round(agent.stats.success_rate, 3),
                    "avg_ms": round(agent.stats.avg_ms, 1),
                    "monitor_penalty": round(agent.stats.monitor_penalty, 3),
                    "routing_score": round(agent.stats.routing_score(), 3),
                    "role": agent.profile.role,
                    "workflow": list(agent.profile.workflow),
                    "tool_scope": list(agent.get_tools()),
                    "available_tools": list(agent.get_tools()),
                    "model": agent._model,
                }
        return result

    def update_routing_penalties(self, penalties: Dict[str, float]) -> None:
        """Update routing penalties."""
        for agent_type, agents in self._pool.items():
            for i, agent in enumerate(agents):
                key = f"{agent_type.value}_{i}"
                penalty = penalties.get(key, 0.0)
                agent.stats.set_penalty(min(max(penalty, 0.0), 0.9))

    def _domain_scores(self, req):
        scores = self._base_domain_scores(req)
        text = req.message.lower()
        if any((s in text for s in ["driver", "pickup", "picked", "arrival", "map", "trip record"])):
            scores[AgentType.TECHNICAL] += 0.85
        if any((s in text for s in ["fare", "fee", "charged", "charge", "payment", "refund"])):
            scores[AgentType.BILLING] += 0.65
        return scores

    def _collaboration_targets(self, req):
        targets = self._base_collaboration_targets(req)
        text = req.message.lower()
        if any((word in text for word in ("driver", "pickup", "arrival", "crash", "login", "error"))):
            targets.append(AgentType.TECHNICAL)
        if any((word in text for word in ("fare", "fee", "charged", "charge", "refund", "payment"))):
            targets.append(AgentType.BILLING)
        return list(dict.fromkeys(targets))

    def _route_decision(self, req):
        if getattr(self, "execution_mode", "multi") == "single":
            return RoutingDecision(
                AgentType.GENERAL,
                reason=("Single support assistant with shared read-only tools and durable case context."),
                confidence=1.0,
            )
        decision = self._base_route_decision(req)
        decision.reason += (
            " Primary: "
            f"{decision.primary_agent.value}"
            "; supporting: "
            f"{', '.join((a.value for a in decision.supporting_agents)) or 'none'}"
            ". Evidence-driven ride domain scores and intent "
            "fusion."
        )
        return decision
