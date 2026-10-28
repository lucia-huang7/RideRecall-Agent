"""Intent voting, caching, entity extraction and urgency classification."""

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from anthropic import AsyncAnthropic

from ride_agent.agent.intent_embedding import IntentEmbedding
from ride_agent.agent.llm_utils import extract_text_content
from ride_agent.integrations.model_provider import TEMPLATES, classify

logger = logging.getLogger(__name__)


class IntentCategory(Enum):
    QUERY = "query"
    COMPLAINT = "complaint"
    REQUEST = "request"
    GREETING = "greeting"
    ESCALATION = "escalation"
    TECHNICAL = "technical"
    BILLING = "billing"
    ACCOUNT = "account"
    FEEDBACK = "feedback"
    ORDER_STATUS = "order_status"
    LOGISTICS = "logistics"
    REFUND = "refund"
    INVOICE = "invoice"
    PAYMENT_ISSUE = "payment_issue"
    ACCOUNT_SECURITY = "account_security"
    TECHNICAL_LOGIN = "technical_login"
    TECHNICAL_CRASH = "technical_crash"
    HUMAN_HANDOFF = "human_handoff"
    OTHER = "other"


class UrgencyLevel(Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class IntentResult:
    intent: IntentCategory
    confidence: float
    urgency: UrgencyLevel
    intent_group: str
    entities: Dict[str, List[str]]
    reasoning: str
    latency_ms: float
    source_scores: Dict[str, float] = field(default_factory=dict)
    embedding_source: str = "local-ngram"


_SPECIFIC_INTENTS = {
    IntentCategory.ORDER_STATUS,
    IntentCategory.LOGISTICS,
    IntentCategory.REFUND,
    IntentCategory.INVOICE,
    IntentCategory.PAYMENT_ISSUE,
    IntentCategory.ACCOUNT_SECURITY,
    IntentCategory.TECHNICAL_LOGIN,
    IntentCategory.TECHNICAL_CRASH,
    IntentCategory.HUMAN_HANDOFF,
}
_GENERIC_INTENTS = {
    IntentCategory.QUERY,
    IntentCategory.BILLING,
    IntentCategory.TECHNICAL,
    IntentCategory.ACCOUNT,
    IntentCategory.ESCALATION,
}
_INTENT_GROUPS: Dict[IntentCategory, IntentCategory] = {
    IntentCategory.ORDER_STATUS: IntentCategory.QUERY,
    IntentCategory.LOGISTICS: IntentCategory.QUERY,
    IntentCategory.REFUND: IntentCategory.BILLING,
    IntentCategory.INVOICE: IntentCategory.BILLING,
    IntentCategory.PAYMENT_ISSUE: IntentCategory.BILLING,
    IntentCategory.ACCOUNT_SECURITY: IntentCategory.ACCOUNT,
    IntentCategory.TECHNICAL_LOGIN: IntentCategory.TECHNICAL,
    IntentCategory.TECHNICAL_CRASH: IntentCategory.TECHNICAL,
    IntentCategory.HUMAN_HANDOFF: IntentCategory.ESCALATION,
}
_URGENCY_KEYWORDS = {
    UrgencyLevel.CRITICAL: ["紧急", "emergency", "urgent", "asap", "立刻"],
    UrgencyLevel.HIGH: ["今天", "马上", "尽快", "hurry", "now"],
    UrgencyLevel.MEDIUM: ["这周", "soon", "快点"],
}


def _cosine(a: List[float], b: List[float]) -> float:
    """Cosine."""
    dot = sum((x * y for x, y in zip(a, b)))
    na = sum((x * x for x in a)) ** 0.5
    nb = sum((x * x for x in b)) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


class IntentRecognizer:
    """Intent recognizer."""

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        model: str = "claude-3-5-sonnet-20241022",
        confidence_threshold: float = 0.5,
        client: Any = None,
        embedding: Any = None,
    ):
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = client if client is not None else AsyncAnthropic(**kwargs)
        self.model = model
        self.threshold = confidence_threshold
        self._embedding = embedding if embedding is not None else IntentEmbedding()
        self._embedding_enabled = True
        self._cache: Dict[str, IntentResult] = {}
        self._cache_expiries: Dict[str, float] = {}
        self.cache_hits = 0
        self.cache_misses = 0

    async def recognize(self, message: str, history: Optional[List[Dict[str, str]]] = None) -> IntentResult:
        """Recognize."""
        key = self._cache_key(message, history)
        if key in self._cache and time.monotonic() < self._cache_expiries.get(key, 0):
            self.cache_hits += 1
            return self._cache[key]
        self.cache_misses += 1
        t0 = time.monotonic()
        llm_task = asyncio.create_task(self._llm_recognize(message, history))
        emb_task = asyncio.create_task(self._embedding_recognize(message)) if self._embedding_enabled else None
        pat = self._pattern_recognize(message)
        if emb_task:
            llm, emb = await asyncio.gather(llm_task, emb_task)
        else:
            llm = await llm_task
            emb = {"intent": IntentCategory.OTHER, "confidence": 0.0}
        intent, confidence, source_scores = self._vote(llm, emb, pat)
        entities = self._extract_entities(message)
        urgency = self._urgency(message, intent)
        result = IntentResult(
            intent=intent,
            confidence=confidence,
            urgency=urgency,
            intent_group=self._intent_group(intent),
            entities=entities,
            reasoning=llm.get("reasoning", ""),
            latency_ms=(time.monotonic() - t0) * 1000,
            source_scores=source_scores,
            embedding_source=emb.get("source", "local-ngram"),
        )
        if len(self._cache) >= 1000:
            for k in list(self._cache)[:500]:
                del self._cache[k]
                self._cache_expiries.pop(k, None)
        self._cache[key] = result
        self._cache_expiries[key] = time.monotonic() + (
            60 if result.embedding_source == "local-ngram" and self._embedding.enabled else 300
        )
        return result

    def learn(self, message: str, correct: IntentCategory) -> None:
        """Learn."""
        tpls = TEMPLATES.setdefault(correct.value, [])
        if message not in tpls:
            tpls.append(message)
            self._embedding.remote_templates.clear()
            self._embedding.local_templates.clear()
            self._cache.clear()
            self._cache_expiries.clear()
            logger.info("Intent example learned for category %s", correct.value)

    def _vote(self, llm: Dict, emb: Dict, pat: Dict) -> tuple[IntentCategory, float, Dict[str, float]]:
        """Vote."""
        cleaned = []
        for source in (llm, emb, pat):
            value = source.get("confidence")
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or (not math.isfinite(value))
                or (not 0 <= value <= 1)
            ):
                source = {
                    "intent": IntentCategory.OTHER,
                    "confidence": 0.0,
                    "failed": True,
                }
            cleaned.append(source)
        llm, emb, pat = cleaned
        source_scores = {
            "llm": float(llm.get("confidence", 0.0) or 0.0),
            "embedding": float(emb.get("confidence", 0.0) or 0.0),
            "pattern": float(pat.get("confidence", 0.0) or 0.0),
        }
        if llm.get("failed"):
            if emb.get("intent") != IntentCategory.OTHER and emb.get("confidence", 0.0) > 0:
                return (emb["intent"], source_scores["embedding"], source_scores)
            if pat.get("intent") != IntentCategory.OTHER and pat.get("confidence", 0.0) > 0:
                return (pat["intent"], source_scores["pattern"], source_scores)
            return (IntentCategory.OTHER, 0.0, source_scores)
        if self._embedding_enabled:
            weights = [(llm, 0.7), (emb, 0.2), (pat, 0.1)]
        else:
            weights = [(llm, 0.85), (pat, 0.15)]
        scores: Dict[IntentCategory, float] = {}
        group_scores: Dict[IntentCategory, float] = {}
        for result, w in weights:
            cat = result.get("intent", IntentCategory.OTHER)
            conf = result.get("confidence", 0.0)
            weighted_score = w * conf
            scores[cat] = scores.get(cat, 0.0) + weighted_score
            group = _INTENT_GROUPS.get(cat, cat)
            group_scores[group] = group_scores.get(group, 0.0) + weighted_score
        best_group = max(group_scores, key=group_scores.get)
        best_score = group_scores[best_group]
        best = max(
            (cat for cat in scores if _INTENT_GROUPS.get(cat, cat) == best_group),
            key=scores.get,
        )
        pat_intent = pat.get("intent", IntentCategory.OTHER)
        pat_conf = float(pat.get("confidence", 0.0) or 0.0)
        if (
            best in _GENERIC_INTENTS
            and pat_intent in _SPECIFIC_INTENTS
            and (_INTENT_GROUPS.get(pat_intent, pat_intent) == best_group)
            and (pat_conf >= 0.5)
            and (best_score < 0.8)
        ):
            source_scores["refined_by_pattern"] = pat_conf
            return (pat_intent, max(best_score, pat_conf), source_scores)
        if best_score < self.threshold:
            return (IntentCategory.OTHER, best_score, source_scores)
        return (best, best_score, source_scores)

    @staticmethod
    def _local_embedding(text: str, dims: int = 256) -> List[float]:
        """Local embedding."""
        normalized = text.lower().strip()
        vec = [0.0] * dims
        tokens = set()
        for n in (1, 2, 3):
            if len(normalized) >= n:
                tokens.update((normalized[i : i + n] for i in range(len(normalized) - n + 1)))
        if not tokens:
            tokens.add(normalized)
        for token in tokens:
            digest = hashlib.md5(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % dims
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        return vec

    def _urgency(self, message: str, intent: IntentCategory) -> UrgencyLevel:
        msg = message.lower()
        msg = re.sub(
            "(?:不(?:是|用|需要)?|无需|无须|非|并不|没那么|暂不)\\s*(?:紧急|立刻|马上|尽快|快点)",
            "",
            msg,
        )
        msg = re.sub(
            ("\\b(?:not|isn't|is not|no)\\s+(?:an?\\s+)?(?:emergency|urgent|asap|now)\\b"),
            "",
            msg,
        )
        for level, kws in _URGENCY_KEYWORDS.items():
            if any(
                (re.search(f"(?<![a-z]){re.escape(kw)}(?![a-z])", msg) if kw.isascii() else kw in msg for kw in kws)
            ):
                return level
        if intent in (IntentCategory.ESCALATION, IntentCategory.HUMAN_HANDOFF):
            return UrgencyLevel.HIGH
        if intent == IntentCategory.COMPLAINT:
            return UrgencyLevel.MEDIUM
        return UrgencyLevel.LOW

    def _cache_key(self, message: str, history: Optional[List[Dict[str, str]]] = None) -> str:
        payload = {"message": self._clean_text(message)}
        if history:
            payload["history"] = [
                {
                    "role": self._clean_text(item.get("role", ""))[:20],
                    "content": self._clean_text(item.get("content", "")),
                }
                for item in history[-3:]
            ]
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _unique(values: List[str]) -> List[str]:
        return list(dict.fromkeys((value.strip() for value in values if value and value.strip())))

    @staticmethod
    def _best_pattern_match(message: str, patterns: Dict[IntentCategory, List[str]]) -> tuple[IntentCategory, float]:
        best_cat, best_score = (IntentCategory.OTHER, 0.0)
        for cat, kws in patterns.items():
            hits = sum((1 for kw in kws if kw in message))
            if not hits:
                continue
            score = min(1.0, 0.5 + 0.25 * (hits - 1))
            if score > best_score:
                best_score, best_cat = (score, cat)
        return (best_cat, best_score)

    @staticmethod
    def _intent_group(intent: IntentCategory) -> str:
        return _INTENT_GROUPS.get(intent, intent).value

    @staticmethod
    def _clean_text(value: Any) -> str:
        """Clean text."""
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        return value.encode("utf-8", errors="ignore").decode("utf-8")

    @property
    def cache_stats(self) -> Dict[str, Any]:
        total = self.cache_hits + self.cache_misses
        return {
            "size": len(self._cache),
            "hits": self.cache_hits,
            "misses": self.cache_misses,
            "hit_rate": self.cache_hits / total if total else 0.0,
        }

    async def _llm_recognize(self, message, history):
        try:
            answer = await asyncio.wait_for(
                self.client.messages.create(
                    model=self.model,
                    max_tokens=350,
                    temperature=0,
                    system=(
                        "Classify a ride support request. Return only JSON with "
                        "intent, confidence (0 to 1), and reasoning. Allowed "
                        "intents: "
                    )
                    + ", ".join(TEMPLATES),
                    messages=[
                        {
                            "role": "user",
                            "content": json.dumps(
                                dict(
                                    task="intent",
                                    message=message,
                                    history=history or [],
                                    examples=TEMPLATES,
                                )
                            ),
                        }
                    ],
                ),
                timeout=max(0.1, float(os.getenv("RIDERECALL_AGENT_INTENT_TIMEOUT_S", "8"))),
            )
            data = json.loads(extract_text_content(answer.content))
            data["intent"] = IntentCategory(data["intent"])
            if (
                type(data.get("confidence")) not in (int, float)
                or not math.isfinite(data["confidence"])
                or (not 0 <= data["confidence"] <= 1)
            ):
                raise ValueError("Invalid intent confidence")
            return data
        except asyncio.CancelledError:
            raise
        except Exception:
            return dict(
                intent=IntentCategory.OTHER,
                confidence=0,
                failed=True,
                reasoning=("Model classification unavailable; local branches remain active."),
            )

    async def _embedding_recognize(self, message):
        templates = {IntentCategory(k): v for k, v in TEMPLATES.items()}
        vectors, query, source = await self._embedding.vectors(message, templates, self._local_embedding)
        scored = [(max((_cosine(query, v) for v in group)), intent) for intent, group in vectors.items()]
        score, intent = max(scored, key=lambda item: item[0])
        return dict(intent=intent, confidence=max(0, min(1, score)), source=source)

    def _pattern_recognize(self, message):
        return dict(intent=IntentCategory(classify(message)), confidence=0.8)

    def _extract_entities(self, message):
        return dict(
            order_id=re.findall("\\btrip-[a-z0-9-]+\\b", message),
            amount=re.findall("\\$\\d+(?:\\.\\d{1,2})?", message),
            error_code=re.findall("\\b[45]\\d\\d\\b", message),
            product=[],
            date=[],
        )
