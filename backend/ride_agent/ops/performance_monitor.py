"""Rolling performance statistics, anomaly detection and routing feedback."""

import asyncio
import logging
import statistics
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Deque, Dict, List, Optional

from prometheus_client import REGISTRY, Counter, Gauge, Histogram

logger = logging.getLogger(__name__)


class Severity(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class Alert:
    severity: Severity
    metric: str
    message: str
    value: float
    threshold: float
    ts: str = field(default_factory=lambda: datetime.now().isoformat())
    resolved: bool = False


@dataclass
class Suggestion:
    """Suggestion."""

    title: str
    detail: str
    action: str
    priority: int


class AnomalyDetector:
    """Anomaly detector."""

    def __init__(self, window: int = 60, sensitivity: float = 2.5):
        self._window = window
        self._sensitivity = sensitivity
        self._history: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=window))

    def record(self, metric: str, value: float) -> Optional[Dict[str, Any]]:
        """Record."""
        buf = self._history[metric]
        buf.append(value)
        if len(buf) < self._window // 2:
            return None
        mean = statistics.mean(buf)
        stdev = statistics.stdev(buf) if len(buf) > 1 else 0.0
        if stdev == 0:
            return None
        z = abs(value - mean) / stdev
        if z > self._sensitivity:
            return {
                "metric": metric,
                "value": value,
                "mean": mean,
                "z_score": round(z, 2),
                "severity": "high" if z > self._sensitivity * 1.5 else "medium",
            }
        return None


class PerformanceMonitor:
    """Performance monitor."""

    THRESHOLDS = {
        "agent_success_rate": (0.9, Severity.ERROR, "less_than"),
        "tool_success_rate": (0.95, Severity.WARNING, "less_than"),
        "agent_avg_ms": (3000, Severity.WARNING, "greater_than"),
        "tool_avg_ms": (5000, Severity.ERROR, "greater_than"),
    }

    def __init__(
        self,
        orchestrator,
        tool_manager,
        interval_s: float = 10.0,
        registry=None,
    ):
        self._orchestrator = orchestrator
        self._tool_manager = tool_manager
        self._interval = interval_s
        self._detector = AnomalyDetector()
        self._alerts: List[Alert] = []
        self._suggestions: List[Suggestion] = []
        self._active = False
        self._task: Optional[asyncio.Task] = None
        self._registry = registry if registry is not None else REGISTRY
        self._registered = True
        self._setup_prometheus()
        for kind, agents in self._orchestrator._pool.items():
            for i, agent in enumerate(agents):
                agent.stats.observer = self._prom["agent_latency_ms"].labels(agent=f"{kind.value}_{i}").observe

    def _setup_prometheus(self) -> None:
        self._prom = {
            "agent_success_rate": Gauge(
                "agent_success_rate",
                "Agent success rate",
                ["agent"],
                registry=self._registry,
            ),
            "agent_latency_ms": Histogram(
                "agent_latency_ms",
                "Agent latency in milliseconds",
                ["agent"],
                buckets=(
                    10,
                    50,
                    100,
                    250,
                    500,
                    1000,
                    2000,
                    3000,
                    5000,
                    10000,
                    20000,
                    30000,
                    50000,
                ),
                registry=self._registry,
            ),
            "chat_latency_ms": Histogram(
                "chat_latency_ms",
                "Chat HTTP request duration in milliseconds",
                ["status"],
                buckets=(
                    50,
                    100,
                    250,
                    500,
                    1000,
                    2000,
                    5000,
                    10000,
                    20000,
                    30000,
                    50000,
                ),
                registry=self._registry,
            ),
            "tool_success_rate": Gauge(
                "tool_success_rate",
                "Tool success rate",
                ["tool"],
                registry=self._registry,
            ),
            "requests_total": Counter("requests_total", "Total requests", registry=self._registry),
        }

    async def start(self) -> None:
        if self._active:
            return
        if not self._registered:
            for metric in self._prom.values():
                self._registry.register(metric)
            self._registered = True
        self._active = True
        self._task = asyncio.create_task(self._loop())
        logger.info(f"Monitor started; collection interval {self._interval}s")

    async def stop(self) -> None:
        self._active = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._registered:
            for metric in self._prom.values():
                self._registry.unregister(metric)
            self._registered = False

    async def _loop(self) -> None:
        while self._active:
            try:
                await self._collect()
            except Exception as ex:
                logger.error(f"Monitor collection failed: {ex}")
            await asyncio.sleep(self._interval)

    async def _collect(self) -> None:
        """Collect."""
        agent_stats = self._orchestrator.get_stats()
        tool_stats = self._tool_manager.get_stats()
        routing_penalties: Dict[str, float] = {}
        for agent_key, s in agent_stats.items():
            sr = s["success_rate"]
            ms = s["avg_ms"]
            for metric, value in [("agent_success_rate", sr), ("agent_avg_ms", ms)]:
                anomaly = self._detector.record(f"{metric}:{agent_key}", value)
                if anomaly:
                    logger.warning((f"Anomaly detected [{agent_key}] {metric}={value:.3f} z={anomaly['z_score']}"))
            self._check_threshold("agent_success_rate", sr, agent_key)
            self._check_threshold("agent_avg_ms", ms, agent_key)
            if "agent_success_rate" in self._prom:
                self._prom["agent_success_rate"].labels(agent=agent_key).set(sr)
            routing_penalties[agent_key] = (
                self._routing_penalty(s["recent_success_rate"], s["recent_avg_ms"])
                if s.get("recent_total", 0) >= 5
                else 0.0
            )
        for tool_name, s in tool_stats.items():
            sr = s["success_rate"]
            ms = s["avg_latency_ms"]
            cf = s["consecutive_fails"]
            self._check_threshold("tool_success_rate", sr, tool_name)
            self._check_threshold("tool_avg_ms", ms, tool_name)
            if "tool_success_rate" in self._prom:
                self._prom["tool_success_rate"].labels(tool=tool_name).set(sr)
            if cf >= 3:
                self._add_suggestion(
                    Suggestion(
                        title=f"Tool {tool_name} consecutive failures: {cf} times",
                        detail=(f"Success rate {sr:.1%}, mean latency {ms:.0f}ms, circuit state: {s['circuit_state']}"),
                        action=(
                            "1. Check tool dependencies.\n2. Inspect error logs.\n"
                            "3. Review timeout and fallback behavior."
                        ),
                        priority=9,
                    )
                )
        updater = getattr(self._orchestrator, "update_routing_penalties", None)
        if updater:
            updater(routing_penalties)
        self._generate_routing_suggestions(agent_stats)

    @staticmethod
    def _routing_penalty(success_rate: float, avg_ms: float) -> float:
        """Routing penalty."""
        penalty = 0.0
        if success_rate < 0.9:
            penalty += min(0.5, (0.9 - success_rate) * 2)
        if avg_ms > 3000:
            penalty += min(0.4, (avg_ms - 3000) / 10000)
        return min(penalty, 0.9)

    def _check_threshold(self, metric: str, value: float, label: str) -> None:
        if metric not in self.THRESHOLDS:
            return
        threshold, severity, operator = self.THRESHOLDS[metric]
        triggered = operator == "less_than" and value < threshold or (operator == "greater_than" and value > threshold)
        if triggered:
            alert = Alert(
                severity=severity,
                metric=f"{metric}:{label}",
                message=f"{label} / {metric} = {value:.3f}, threshold {threshold}",
                value=value,
                threshold=threshold,
            )
            self._alerts.append(alert)
            logger.warning(f"[{severity.value.upper()}] {alert.message}")

    def _generate_routing_suggestions(self, agent_stats: Dict[str, Any]) -> None:
        """Generate routing suggestions."""
        for agent_key, s in agent_stats.items():
            if s["success_rate"] < 0.85 and s["total"] > 10:
                self._add_suggestion(
                    Suggestion(
                        title=f"Agent {agent_key} has a low success rate",
                        detail=(f"Success rate {s['success_rate']:.1%}, routing score {s['routing_score']:.3f}"),
                        action=(
                            "Routing feedback has reduced this agent weight. Review its "
                            "system prompt, workload complexity and instance capacity "
                            "before changing "
                            "routing."
                        ),
                        priority=8,
                    )
                )

    def _add_suggestion(self, s: Suggestion) -> None:
        if not any((x.title == s.title for x in self._suggestions)):
            self._suggestions.append(s)
            logger.info(f"Suggestion [P{s.priority}]: {s.title}")

    def summary(self) -> Dict[str, Any]:
        """Summary."""
        return {
            "agent_stats": self._orchestrator.get_stats(),
            "tool_stats": self._tool_manager.get_stats(),
            "active_alerts": [asdict(a) for a in self._alerts if not a.resolved][-10:],
            "suggestions": [
                {"title": s.title, "action": s.action, "priority": s.priority}
                for s in sorted(self._suggestions, key=lambda x: -x.priority)[:5]
            ],
        }
