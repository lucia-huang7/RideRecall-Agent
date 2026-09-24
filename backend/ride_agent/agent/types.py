import logging
import os
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from ride_agent.agent.intent import IntentCategory, UrgencyLevel

"Agent routing, bounded tool execution, fallbacks and request-local results."
logger = logging.getLogger(__name__)


class AgentType(Enum):
    GENERAL = "general"
    TECHNICAL = "technical"
    BILLING = "billing"
    ESCALATION = "escalation"


@dataclass(frozen=True)
class AgentProfile:
    role: str
    mission: str
    workflow: Tuple[str, ...]
    input_contract: Tuple[str, ...]
    output_contract: Tuple[str, ...]
    handoff_conditions: Tuple[str, ...] = ()
    tool_scope: Tuple[str, ...] = ()
    model: Optional[str] = None
    temperature: float = 0.2
    max_tokens: int = 1024


def _env_float(name: str, default: float) -> float:
    """Env float."""
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid floating-point configuration %s", name)
        return default


def _env_int(name: str, default: int) -> int:
    """Env int."""
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid integer configuration %s", name)
        return default


@dataclass
class AgentStats:
    """Agent stats."""

    total: int = 0
    success: int = 0
    total_ms: float = 0.0
    monitor_penalty: float = 0.0
    samples: Any = field(default_factory=lambda: deque(maxlen=50))
    retry_at: float = 0.0
    probe_running: bool = False
    probe_owner: Optional[str] = None
    observer: Any = field(default=None, repr=False)

    def recent(self):
        cutoff = time.monotonic() - 300
        while self.samples and self.samples[0][0] < cutoff:
            self.samples.popleft()
        n = len(self.samples)
        return (
            n,
            sum((x[1] for x in self.samples)) / n if n else 1.0,
            sum((x[2] for x in self.samples)) / n if n else 0.0,
        )

    def available(self):
        if not self.retry_at and self.monitor_penalty:
            self.set_penalty(self.monitor_penalty)
        return not self.probe_running and (not self.retry_at or time.monotonic() >= self.retry_at)

    def set_penalty(self, penalty):
        self.monitor_penalty = penalty
        threshold = min(max(_env_float("LLM_AGENT_SYSTEM_MONITOR_FALLBACK_PENALTY", 0.5), 0.01), 0.9)
        if penalty >= threshold and (not self.retry_at):
            self.retry_at = time.monotonic() + max(1, _env_float("LLM_AGENT_SYSTEM_MONITOR_COOLDOWN_SECONDS", 60))

    def record(self, success, ms, request_id=None):
        if self.probe_running and request_id == self.probe_owner:
            self.probe_running = False
            self.probe_owner = None
            if success:
                self.samples.clear()
                self.retry_at = 0.0
                self.monitor_penalty = 0.0
            else:
                self.retry_at = time.monotonic() + max(1, _env_float("LLM_AGENT_SYSTEM_MONITOR_COOLDOWN_SECONDS", 60))
        self.samples.append((time.monotonic(), success, ms))
        if self.observer:
            self.observer(ms)

    @property
    def success_rate(self) -> float:
        return self.success / self.total if self.total else 1.0

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.total if self.total else 0.0

    def routing_score(self) -> float:
        """Routing score."""
        latency_score = 1.0 / (1.0 + self.avg_ms / 1000)
        base_score = self.success_rate * 0.7 + latency_score * 0.3
        return base_score * max(0.0, 1.0 - self.monitor_penalty)


@dataclass
class AgentResponse:
    agent_type: AgentType
    content: str
    success: bool
    confidence: float = 1.0
    latency_ms: float = 0.0
    escalate: bool = False
    tools_used: List[str] = field(default_factory=list)
    tool_traces: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "success"
    error_code: Optional[str] = None


@dataclass
class AgentCallResult:
    """Agent call result."""

    content: str
    tools_used: List[str] = field(default_factory=list)
    tool_traces: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "success"


class AgentCallError(RuntimeError):
    """Agent call error."""

    def __init__(self, message: str, tools_used: List[str], tool_traces: List[Dict[str, Any]]):
        super().__init__(message)
        self.tools_used = list(tools_used)
        self.tool_traces = list(tool_traces)


@dataclass
class Request:
    message: str
    user_id: str
    conv_id: str
    context: str = ""
    history: Optional[List[Dict[str, str]]] = None
    entities: Dict[str, List[str]] = field(default_factory=dict)
    intent: Optional[IntentCategory] = None
    intent_group: Optional[str] = None
    urgency: Optional[UrgencyLevel] = None
    intent_confidence: float = 1.0
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tool_attempts: List[Dict[str, Any]] = field(default_factory=list)
    before_unsafe_tool: Any = field(default=None, repr=False)
    unsafe_tool_calls: set = field(default_factory=set, repr=False)
    conversation_state: Optional[Dict[str, Any]] = None


@dataclass
class OrchestratorResult:
    request_id: str
    response: str
    agent_type: AgentType
    intent: Optional[IntentCategory]
    escalated: bool = False
    latency_ms: float = 0.0
    agent_types: List[AgentType] = field(default_factory=list)
    primary_agent: Optional[AgentType] = None
    supporting_agents: List[AgentType] = field(default_factory=list)
    tools_used: List[str] = field(default_factory=list)
    tool_traces: List[Dict[str, Any]] = field(default_factory=list)
    routing_reason: str = ""
    routing_confidence: float = 0.0
    status: str = "success"
    retryable: bool = False
    error_code: Optional[str] = None


@dataclass
class RoutingDecision:
    """Routing decision."""

    primary_agent: AgentType
    supporting_agents: List[AgentType] = field(default_factory=list)
    reason: str = ""
    confidence: float = 0.0

    @property
    def agent_types(self) -> List[AgentType]:
        return [self.primary_agent] + self.supporting_agents

    @property
    def multi_agent(self) -> bool:
        return bool(self.supporting_agents)
