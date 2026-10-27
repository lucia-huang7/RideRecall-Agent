"""Bounded conversation memory, source-aware summaries and persistent projections."""

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from redis.exceptions import WatchError

from ride_agent.agent.concurrency import redis_lease
from ride_agent.agent.llm_utils import extract_text_content

logger = logging.getLogger(__name__)


class MsgRole(Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass
class Message:
    role: MsgRole
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryContext:
    """Memory context."""

    recent_messages: List[Message]
    relevant_history: List[str]
    user_profile: Dict[str, Any]
    summary: str
    state: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def _clean(text: str) -> str:
        """Clean."""
        return text.encode("utf-8", errors="ignore").decode("utf-8")

    def to_prompt_text(self) -> str:
        """To prompt text."""
        parts = []
        if self.summary:
            parts.append(f"[Conversation summary]\n{self._clean(self.summary)}")
        if self.relevant_history:
            parts.append("[Relevant history]\n" + "\n".join((f"- {self._clean(h)}" for h in self.relevant_history[:3])))
        if self.user_profile:
            parts.append(f"[User profile]\n{json.dumps(self.user_profile, ensure_ascii=True)}")
        if self.recent_messages:
            parts.append("[Recent messages]")
            for m in self.recent_messages[-8:]:
                parts.append(f"{m.role.value}: {self._clean(m.content)}")
        return "\n\n".join(parts)


class MemoryManager:
    """Memory manager."""

    WORKING_MAX = 20
    COMPRESS_AT = 15
    HISTORY_TOP_K = 5
    SUMMARY_MAX_CHARS = 800
    PROFILE_DOC_PREFIX = "user_profile:"

    def __init__(
        self,
        *,
        client: Any,
        redis_client: Any,
        episodic: Any,
        profile: Any,
        model: str = "local-memory",
        prompt_builder: Any = None,
        strict_scope: bool = False,
    ):
        self._client = client
        self._prompt_builder = prompt_builder
        self._strict_scope = strict_scope
        self._model = model
        self._redis = redis_client
        self._episodic, self._profile = episodic, profile

    async def commit_turn(self, user_id, conv_id, request_id, message, response, *, state=None):
        """Append working messages and state atomically; durable receipts belong to the case store."""

        def raw(role, content):
            return json.dumps(
                {
                    "role": role,
                    "content": self._safe_text(content),
                    "ts": datetime.now().isoformat(),
                    "metadata": {"request_id": request_id},
                }
            )

        key = self._wm_key(user_id, conv_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            if response.get("status") != "failed":
                pipe.lpush(key, raw("user", message), raw("assistant", response["response"]))
                pipe.expire(key, 86400)
                if state is not None:
                    pipe.setex(self._state_key(user_id, conv_id), 86400, json.dumps(state))
            await pipe.execute()

    async def maintain(self, user_id, conv_id):
        await asyncio.wait_for(self._compress(user_id, conv_id), 30)
        await self.update_profile(user_id, conv_id)

    async def add_message(
        self,
        user_id: str,
        conv_id: str,
        role: MsgRole,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add message."""
        user_id = self._safe_text(user_id)
        conv_id = self._safe_text(conv_id)
        clean_metadata = {self._safe_text(k): self._safe_metadata_value(v) for k, v in (metadata or {}).items()}
        msg = Message(role=role, content=self._safe_text(content), metadata=clean_metadata)
        key = self._wm_key(user_id, conv_id)
        await self._redis.lpush(
            key,
            json.dumps(
                {
                    "role": msg.role.value,
                    "content": msg.content,
                    "ts": msg.timestamp.isoformat(),
                    "metadata": msg.metadata,
                }
            ),
        )
        await self._redis.expire(key, 86400)
        if await self._redis.llen(key) >= self.COMPRESS_AT:
            await self._compress(user_id, conv_id)

    async def update_profile(self, user_id: str, conv_id: str) -> None:
        digest = hashlib.sha256(self._safe_text(user_id).encode()).hexdigest()
        async with redis_lease(self._redis, f"lock:profile:{digest}"):
            await self._update_profile(user_id, conv_id)

    async def _update_profile(self, user_id: str, conv_id: str) -> None:
        """Update profile."""
        user_id = self._safe_text(user_id)
        conv_id = self._safe_text(conv_id)
        messages = await self._get_working_memory(user_id, conv_id)
        if not messages:
            return
        current_profile = await self._get_profile(user_id)
        text = self._safe_text("\n".join((f"{m.role.value}: {m.content}" for m in messages[-10:])))
        profile_ctx = json.dumps(current_profile, ensure_ascii=False) if current_profile else "{}"
        prompt = (
            "Extract or update user preferences and key entities from "
            "this conversation and prior profile. Return "
            "JSON.\nConversation:\n"
            f"{text}"
            "\n\nExisting profile:\n"
            f"{profile_ctx}"
            '\n\nReturn: {"preferences": ["..."], "entities": {"product": '
            '[], "issue_type": '
            "[]}}"
        )
        if getattr(self, "_prompt_builder", None):
            prompt = self._prompt_builder("profile", text=text, previous=current_profile)
        prompt = self._safe_text(prompt)
        try:
            resp = await asyncio.wait_for(
                self._client.messages.create(
                    model=self._model,
                    max_tokens=512,
                    temperature=0.0,
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=15,
            )
            raw = extract_text_content(resp.content)
            s, e = (raw.find("{"), raw.rfind("}") + 1)
            profile_data = json.loads(raw[s:e])
            doc_id = self._profile_doc_id(user_id)
            doc_text = self._safe_text(json.dumps(profile_data, ensure_ascii=False))
            await asyncio.to_thread(
                self._profile.upsert,
                ids=[doc_id],
                documents=[doc_text],
                metadatas=[
                    {
                        "user_id": user_id,
                        "conv_id": conv_id,
                        "updated_at": datetime.now().isoformat(),
                    }
                ],
            )
            logger.info(f"User profile updated: {user_id}")
        except Exception as ex:
            logger.warning("Profile update failed (%s)", type(ex).__name__)

    async def get_context(self, user_id: str, conv_id: str, query: str = "") -> MemoryContext:
        """Get context."""
        user_id = self._safe_text(user_id)
        conv_id = self._safe_text(conv_id)
        query = self._safe_text(query)
        recent = await self._get_working_memory(user_id, conv_id)
        history = await self._search_episodic(user_id, conv_id, query or (recent[-1].content if recent else ""))
        profile = await self._get_profile(user_id)
        summary = await self._redis.get(self._summary_key(user_id, conv_id)) or ""
        state_raw = await self._redis.get(self._state_key(user_id, conv_id))
        return MemoryContext(
            recent_messages=recent,
            relevant_history=history,
            user_profile=profile,
            summary=summary,
            state=json.loads(state_raw) if state_raw else {},
        )

    async def _compress(self, user_id: str, conv_id: str) -> None:
        """Compress."""
        key = self._wm_key(user_id, conv_id)
        skey = self._summary_key(user_id, conv_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.lrange(key, 0, -1)
            pipe.get(skey)
            snapshot, old_summary = await pipe.execute()
        if len(snapshot) < self.COMPRESS_AT:
            return
        old_summary = old_summary or ""
        messages = self._messages_from_raw(snapshot)
        to_compress = messages[:-5]
        text = self._safe_text("\n".join((f"{m.role.value}: {m.content}" for m in to_compress)))
        prompt = self._safe_text((f"Summarize the key facts in the following conversation in 2-3 sentences:\n{text}"))
        if getattr(self, "_prompt_builder", None):
            prompt = self._summary_prompt(text, to_compress)
        try:
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                temperature=0.0,
                messages=[{"role": "user", "content": prompt}],
            )
            summary = self._safe_text(extract_text_content(resp.content)).strip()
            if not summary or getattr(resp, "stop_reason", None) == "max_tokens":
                logger.warning("Summary empty or truncated; preserving source messages")
                return
        except Exception as ex:
            logger.warning("Summary failed; preserving source messages: %s", type(ex).__name__)
            return
        new_summary = await self._merge_summary(old_summary, summary)
        for _ in range(3):
            async with self._redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(key, skey)
                    current = await pipe.lrange(key, 0, -1)
                    current_summary = await pipe.get(skey) or ""
                    if (
                        len(current) < len(snapshot)
                        or current[-len(snapshot) :] != snapshot
                        or current_summary != old_summary
                    ):
                        return
                    keep_count = len(current) - len(snapshot) + 5
                    pipe.multi()
                    pipe.setex(skey, 86400, new_summary)
                    pipe.ltrim(key, 0, keep_count - 1)
                    pipe.expire(key, 86400)
                    await pipe.execute()
                    logger.info("Working-memory compression complete: %s/%s", user_id, conv_id)
                    await self._store_episodic(user_id, conv_id, text, summary)
                    return
                except WatchError:
                    continue
        logger.info("Concurrent conversation changes detected; deferring compression")

    def _summary_prompt(self, text, messages):
        return self._prompt_builder("summary", text=text)

    @staticmethod
    def _messages_from_raw(raws: List[str]) -> List[Message]:
        msgs = []
        for raw in reversed(raws):
            d = json.loads(raw)
            msgs.append(
                Message(
                    role=MsgRole(d["role"]),
                    content=d["content"],
                    timestamp=datetime.fromisoformat(d["ts"]),
                    metadata=d.get("metadata", {}),
                )
            )
        return msgs

    async def _get_working_memory(self, user_id: str, conv_id: str) -> List[Message]:
        raws = await self._redis.lrange(self._wm_key(user_id, conv_id), 0, self.WORKING_MAX - 1)
        return self._messages_from_raw(raws)

    async def _search_episodic(self, user_id: str, conv_id: str, query: str) -> List[str]:
        """Search episodic."""
        query_text = self._safe_text(query).strip()
        if not query_text:
            return []
        try:
            results = await self._query_episodic(
                query_text,
                n_results=self.HISTORY_TOP_K,
                where={
                    "$and": [
                        {"user_id": self._safe_text(user_id)},
                        {"conv_id": self._safe_text(conv_id)},
                    ]
                },
            )
            docs = self._extract_docs(results)
            if not getattr(self, "_strict_scope", False) and len(docs) < self.HISTORY_TOP_K:
                fallback = await self._query_episodic(
                    query_text,
                    n_results=self.HISTORY_TOP_K,
                    where={"user_id": self._safe_text(user_id)},
                )
                docs.extend(self._extract_docs(fallback))
            return self._dedupe_texts(docs)[: self.HISTORY_TOP_K]
        except Exception as ex:
            logger.warning("Episodic retrieval failed (%s)", type(ex).__name__)
            return []

    async def _store_episodic(self, user_id: str, conv_id: str, text: str, summary: str) -> None:
        """Store episodic."""
        try:
            user_id = self._safe_text(user_id)
            conv_id = self._safe_text(conv_id)
            text = self._safe_text(text)
            summary = self._safe_text(summary)
            doc_id = hashlib.md5(f"{user_id}{conv_id}{time.time()}".encode()).hexdigest()
            await asyncio.to_thread(
                self._episodic.add,
                ids=[doc_id],
                documents=[summary],
                metadatas=[
                    {
                        "user_id": user_id,
                        "conv_id": conv_id,
                        "ts": datetime.now().isoformat(),
                        "full_text": self._safe_text(text[:500]),
                    }
                ],
            )
        except Exception as ex:
            logger.warning("Episodic persistence failed (%s)", type(ex).__name__)

    async def _get_profile(self, user_id: str) -> Dict[str, Any]:
        """Get profile."""
        try:
            doc_id = self._profile_doc_id(user_id)
            direct = await asyncio.to_thread(self._profile.get, ids=[doc_id])
            if direct.get("documents"):
                return json.loads(direct["documents"][0])
            results = await asyncio.to_thread(self._profile.get, where={"user_id": user_id})
            return self._latest_profile_from_results(results)
        except Exception:
            pass
        return {}

    @classmethod
    def _conversation_id(cls, user_id: str, conv_id: str) -> str:
        return hashlib.sha256(
            json.dumps([cls._safe_text(user_id), cls._safe_text(conv_id)], ensure_ascii=True).encode()
        ).hexdigest()

    @classmethod
    def _wm_key(cls, user_id: str, conv_id: str) -> str:
        return f"wm_v2:{cls._conversation_id(user_id, conv_id)}"

    @classmethod
    def _summary_key(cls, user_id: str, conv_id: str) -> str:
        return f"summary_v2:{cls._conversation_id(user_id, conv_id)}"

    @classmethod
    def _state_key(cls, user_id: str, conv_id: str) -> str:
        return f"state_v1:{cls._conversation_id(user_id, conv_id)}"

    @classmethod
    def _profile_doc_id(cls, user_id: str) -> str:
        return f"{cls.PROFILE_DOC_PREFIX}{user_id}"

    @staticmethod
    def _safe_text(value: Any) -> str:
        """Safe text."""
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        return value.encode("utf-8", errors="ignore").decode("utf-8")

    @classmethod
    def _safe_metadata_value(cls, value: Any) -> Any:
        """Safe metadata value."""
        if isinstance(value, str):
            return cls._safe_text(value)
        if isinstance(value, dict):
            return {cls._safe_text(k): cls._safe_metadata_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [cls._safe_metadata_value(v) for v in value]
        return value

    async def _query_episodic(self, query_text: str, n_results: int, where: Dict[str, Any]) -> Dict[str, Any]:
        return await asyncio.to_thread(
            self._episodic.query,
            query_texts=[query_text],
            n_results=n_results,
            where=where,
        )

    @staticmethod
    def _extract_docs(results: Dict[str, Any]) -> List[str]:
        docs = results.get("documents") or []
        if not docs:
            return []
        first = docs[0] if isinstance(docs[0], list) else docs
        return [doc for doc in first if isinstance(doc, str) and doc.strip()]

    @staticmethod
    def _dedupe_texts(values: List[str]) -> List[str]:
        seen = set()
        deduped: List[str] = []
        for value in values:
            text = value.strip()
            if not text or text in seen:
                continue
            seen.add(text)
            deduped.append(text)
        return deduped

    async def _merge_summary(self, old_summary: str, new_summary: str) -> str:
        old_summary = self._safe_text(old_summary).strip()
        new_summary = self._safe_text(new_summary).strip()
        if not old_summary:
            return new_summary[: self.SUMMARY_MAX_CHARS]
        if not new_summary:
            return old_summary[: self.SUMMARY_MAX_CHARS]
        prompt = self._safe_text(
            (
                "Merge the following two conversation summaries into one, "
                "with at most "
                f"{self.SUMMARY_MAX_CHARS}"
                " characters. Preserve preferences, key entities, pending "
                "work, constraints and unresolved issues. Return only the "
                "summary.\n\nPrevious "
                "summary:\n"
                f"{old_summary}"
                "\n\nNew summary:\n"
                f"{new_summary}"
                "\n"
            )
        )
        if getattr(self, "_prompt_builder", None):
            prompt = self._prompt_builder("merge", previous=old_summary, text=new_summary)
        try:
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=256,
                temperature=0.0,
                messages=[{"role": "user", "content": prompt}],
            )
            merged = self._safe_text(extract_text_content(resp.content)).strip()
            if merged:
                return merged[: self.SUMMARY_MAX_CHARS]
        except Exception as ex:
            logger.warning(
                "Summary merge failed (%s); retaining bounded source context",
                type(ex).__name__,
            )
        merged = self._safe_text(f"{old_summary}\n{new_summary}").strip()
        return merged[-self.SUMMARY_MAX_CHARS :]

    @staticmethod
    def _latest_profile_from_results(results: Dict[str, Any]) -> Dict[str, Any]:
        documents = results.get("documents") or []
        metadatas = results.get("metadatas") or []
        if not documents:
            return {}
        candidates: List[tuple[str, Dict[str, Any], str]] = []
        for idx, doc in enumerate(documents):
            if not doc:
                continue
            metadata = metadatas[idx] if idx < len(metadatas) and isinstance(metadatas[idx], dict) else {}
            ts = str(metadata.get("updated_at") or metadata.get("ts") or "")
            candidates.append((ts, metadata, doc))
        if not candidates:
            return {}
        candidates.sort(key=lambda item: item[0], reverse=True)
        latest_doc = candidates[0][2]
        try:
            return json.loads(latest_doc)
        except Exception:
            return {}
