"""Tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
    Union,
)

if TYPE_CHECKING:
    from ride_agent.agent.types import Request
AgentToolHandler = Callable[["Request", Dict[str, Any]], Union[Any, Awaitable[Any]]]


@dataclass(frozen=True)
class AgentToolSpec:
    """Agent tool spec."""

    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: AgentToolHandler
    allowed_agents: Optional[Tuple[str, ...]] = None
    retry_safe: bool = True


def make_tool(
    name: str,
    description: str,
    properties: Dict[str, Any],
    handler: AgentToolHandler,
    required: Optional[List[str]] = None,
) -> AgentToolSpec:
    """Make tool."""
    return AgentToolSpec(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        },
        handler=handler,
    )


def inspect_request_context(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """Inspect request context."""
    return {
        "intent": req.intent.value if req.intent else None,
        "intent_group": req.intent_group,
        "urgency": req.urgency.name if req.urgency else None,
        "intent_confidence": round(req.intent_confidence, 4),
        "entities": req.entities or {},
        "context_available": bool(req.context),
        "requested_focus": str(args.get("focus", "general"))[:40],
    }


def suggest_required_fields(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """Suggest required fields."""
    intent = req.intent.value if req.intent else "other"
    fields: List[str] = []
    if intent in {"order_status", "logistics"}:
        fields = ["Order ID or order time"]
    elif intent in {"account", "account_security"}:
        fields = ["Sign-in method or account identifier", "Incident time"]
    elif intent in {"complaint", "request"}:
        fields = ["Incident time", "Requested outcome"]
    elif intent == "other":
        fields = ["Specific issue to resolve"]
    return {
        "intent": intent,
        "required_fields": fields,
        "known_entities": req.entities or {},
    }


def lookup_error_code(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """Lookup error code."""
    code = str(args.get("error_code", "")).upper().strip()
    mapping = {
        "401": (
            "Authentication failed",
            [
                "Check token or API key expiry",
                "Check request timestamp and signature",
                "Check account sign-in status",
            ],
        ),
        "403": (
            "Insufficient permissions",
            [
                "Check account or plan permissions",
                "Check resource permissions and IP allowlist",
            ],
        ),
        "404": (
            "Resource or path not found",
            ["Check endpoint path and environment", "Check the resource identifier"],
        ),
        "500": (
            "Server processing failure",
            [
                "Record request_id and incident time",
                "Check dependencies, input format and server logs",
            ],
        ),
    }
    meaning, steps = mapping.get(
        code,
        (
            "Unrecognized error code",
            ["Provide the complete error, incident time and environment"],
        ),
    )
    return {
        "error_code": code,
        "meaning": meaning,
        "next_steps": steps,
        "server_log_checked": False,
    }


def build_diagnostic_plan(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """Build diagnostic plan."""
    environment = str(args.get("environment", "unknown"))[:80]
    reproduced = bool(args.get("reproduced", False))
    steps = [
        "Reproduce and record the complete error",
        "Check network, DNS, proxy and certificates",
        "Check version, configuration and permissions",
    ]
    if reproduced:
        steps.append("Reproduce with a minimal request and record request_id")
    return {
        "environment": environment,
        "reproduced": reproduced,
        "diagnostic_steps": steps,
    }


def check_billing_fields(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """Check billing fields."""
    fields = {
        "order_id": bool(req.entities.get("order_id")),
        "amount": bool(req.entities.get("amount")),
        "date": bool(req.entities.get("date")),
        "payment_channel": bool(args.get("payment_channel")),
    }
    return {
        "fields": fields,
        "missing_fields": [name for name, present in fields.items() if not present],
        "can_confirm_refund": False,
        "reason": ("This tool validates fields only; it does not access order or payment systems"),
    }


def compare_amounts(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """Compare amounts."""
    try:
        first = float(args["amount_a"])
        second = float(args["amount_b"])
    except (KeyError, TypeError, ValueError):
        return {"success": False, "error": "amount_a and amount_b must be numbers"}
    return {
        "success": True,
        "amount_a": first,
        "amount_b": second,
        "difference": round(first - second, 2),
        "interpretation": ("Arithmetic difference only; not a duplicate-charge or refund decision"),
    }


def create_handoff_summary(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """Create handoff summary."""
    return {
        "request_id": req.request_id,
        "reason": str(args.get("reason", "Specialist verification is needed"))[:120],
        "intent": req.intent.value if req.intent else "unknown",
        "urgency": req.urgency.name if req.urgency else "UNKNOWN",
        "entities": req.entities or {},
        "sensitive_data_required": False,
    }


def build_shared_rag_tools(tool_manager: Any) -> Dict[str, AgentToolSpec]:
    """Build shared rag tools."""

    async def search_knowledge_base(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
        query = str(args.get("query") or req.message or "").strip()
        top_k = args.get("top_k", 5)
        if not query:
            return {"success": False, "error": "query must not be empty", "results": []}
        if tool_manager is None:
            return {
                "success": False,
                "error": "Retrieval tools are not initialized",
                "results": [],
            }
        result = await tool_manager.search_with_rewrite("knowledge_search", query, top_k=top_k)
        if not getattr(result, "success", False):
            return {
                "success": False,
                "query": query,
                "error": getattr(result, "error", "Knowledge retrieval failed"),
                "results": [],
                "reranked": False,
            }
        return {
            "success": True,
            "query": query,
            "top_k": top_k,
            "results": result.data,
            "reranked": bool(getattr(result, "reranked", False)),
        }

    return {
        "search_knowledge_base": make_tool(
            "search_knowledge_base",
            ("Retrieve relevant evidence for support, technical, billing and handoff analysis."),
            {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 2000,
                    "description": "User question or search terms",
                },
                "top_k": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "Maximum result count",
                },
            },
            search_knowledge_base,
            required=["query"],
        )
    }


def general_tools() -> Dict[str, AgentToolSpec]:
    return {
        "inspect_request_context": make_tool(
            "inspect_request_context",
            ("Inspect intent, urgency, entities and available request context without external business access."),
            {"focus": {"type": "string", "description": "Issue focus"}},
            inspect_request_context,
        ),
        "suggest_required_fields": make_tool(
            "suggest_required_fields",
            "Identify only the missing fields needed for the next turn.",
            {},
            suggest_required_fields,
        ),
    }


def technical_tools() -> Dict[str, AgentToolSpec]:
    return {
        "lookup_error_code": make_tool(
            "lookup_error_code",
            ("Explain common HTTP errors and low-risk diagnostics without reading server logs."),
            {
                "error_code": {
                    "type": "string",
                    "description": "For example, 401, 403 or 500",
                }
            },
            lookup_error_code,
            required=["error_code"],
        ),
        "build_diagnostic_plan": make_tool(
            "build_diagnostic_plan",
            ("Suggest diagnostic steps from the environment and reproducibility, without changing configuration."),
            {
                "environment": {
                    "type": "string",
                    "description": "App, browser, server or Docker",
                },
                "reproduced": {
                    "type": "boolean",
                    "description": "Whether the issue is reproducible",
                },
            },
            build_diagnostic_plan,
            required=["environment", "reproduced"],
        ),
    }


def billing_tools() -> Dict[str, AgentToolSpec]:
    return {
        "check_billing_fields": make_tool(
            "check_billing_fields",
            ("Check billing verification fields without accessing order, payment or refund systems."),
            {
                "payment_channel": {
                    "type": "string",
                    "description": "Payment method, such as a card or digital wallet",
                }
            },
            check_billing_fields,
        ),
        "compare_amounts": make_tool(
            "compare_amounts",
            (
                "Calculate the difference between supplied amounts without "
                "determining duplicate charges or executing "
                "refunds."
            ),
            {
                "amount_a": {"type": "number", "description": "First amount"},
                "amount_b": {"type": "number", "description": "Second amount"},
            },
            compare_amounts,
            required=["amount_a", "amount_b"],
        ),
    }


def escalation_tools() -> Dict[str, AgentToolSpec]:
    return {
        "create_handoff_summary": make_tool(
            "create_handoff_summary",
            "Prepare a structured handoff summary without creating a real ticket.",
            {"reason": {"type": "string", "description": "Reason for escalation"}},
            create_handoff_summary,
        )
    }
