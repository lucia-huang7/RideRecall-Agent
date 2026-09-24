"""Knowledge base."""

import asyncio
import hashlib
import json
import logging
import os
import threading
from typing import Any, Dict, List

import chromadb

from ride_agent.domain.seed import POLICIES

logger = logging.getLogger(__name__)


class KnowledgeBase:
    """Knowledge base."""

    COLLECTION_NAME = "knowledge_base"

    def __init__(
        self,
        chroma_host: str = "localhost",
        chroma_port: int = 8000,
        chroma_path: str = "./data/chroma",
        collection: Any = None,
    ):
        if collection is not None:
            self._write_lock = threading.RLock()
            self._use_server = False
            self._collection = collection
            return
        self._write_lock = threading.RLock()
        self._use_server = False
        try:
            self._client = chromadb.HttpClient(
                host=chroma_host,
                port=chroma_port,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )
            self._client.heartbeat()
            self._use_server = True
            logger.info(f"Knowledge ChromaDB connected: {chroma_host}:{chroma_port}")
        except Exception as ex:
            if os.getenv("LLM_AGENT_SYSTEM_ENV", "development").lower() == "production":
                raise RuntimeError(("Production knowledge ChromaDB unavailable; local fallback is forbidden")) from ex
            logger.info(f"Knowledge ChromaDB unavailable; using local mode: {chroma_path}")
            self._client = chromadb.PersistentClient(
                path=chroma_path, settings=chromadb.Settings(anonymized_telemetry=False)
            )
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"description": "Ride-support policy index"},
        )
        seed_demo = os.getenv("LLM_AGENT_SYSTEM_SEED_DEMO_DOCS", "true").lower() in {
            "1",
            "true",
            "yes",
        }
        if (
            os.getenv("LLM_AGENT_SYSTEM_ENV", "development").lower() != "production"
            and seed_demo
            and (self._collection.count() == 0)
        ):
            self._load_default_docs()

    def add_documents(self, documents: List[Dict[str, str]]) -> int:
        """Add documents."""
        prepared = {}
        for doc in documents:
            title, content = (doc.get("title", ""), doc.get("content", ""))
            if not isinstance(title, str) or not isinstance(content, str):
                raise ValueError("Document title and content must be strings")
            if not title.strip() or not content.strip():
                raise ValueError("Document title and content must not be empty")
            explicit_id = doc.get("document_id")
            if explicit_id is not None and (not isinstance(explicit_id, str) or not explicit_id.strip()):
                raise ValueError("document_id must be a nonempty string")
            identity = json.dumps(
                ["explicit", explicit_id] if explicit_id else ["title", title],
                ensure_ascii=True,
            )
            if not isinstance(identity, str):
                raise ValueError("document_id must be a string")
            prepared[identity] = (title, content, bool(doc.get("document_id")))
        count = 0
        with self._write_lock:
            for identity, (title, content, explicit) in prepared.items():
                document_id = hashlib.sha256(identity.encode()).hexdigest()
                chunks = self._chunk_text(content, chunk_size=500)
                version = hashlib.sha256(content.encode()).hexdigest()
                ids = [f"{document_id}:{i}" for i in range(len(chunks))]
                existing = self._collection.get(where={"document_id": document_id}, include=["metadatas"])
                old_ids = set(existing["ids"])
                kind, value = json.loads(identity)
                legacy_identity = value if explicit else f"title:{title}"
                if not explicit or not value.startswith("title:"):
                    legacy_id = hashlib.sha256(legacy_identity.encode()).hexdigest()
                    previous = self._collection.get(where={"document_id": legacy_id}, include=["metadatas"])
                    old_ids.update(
                        (
                            i
                            for i, meta in zip(previous["ids"], previous["metadatas"])
                            if (meta or {}).get("title") == title
                        )
                    )
                if not explicit:
                    legacy = self._collection.get(where={"title": title}, include=["metadatas"])
                    old_ids.update(
                        (
                            i
                            for i, meta in zip(legacy["ids"], legacy["metadatas"])
                            if not (meta or {}).get("document_id")
                        )
                    )
                self._collection.upsert(
                    ids=ids,
                    documents=chunks,
                    metadatas=[
                        {
                            "title": title,
                            "document_id": document_id,
                            "version": version,
                            "chunk_index": i,
                            "total_chunks": len(chunks),
                        }
                        for i in range(len(chunks))
                    ],
                )
                stale = old_ids - set(ids)
                if stale:
                    self._collection.delete(ids=list(stale))
                count += len(chunks)
        logger.info("Indexed %s knowledge chunks", count)
        return count

    async def add_documents_async(self, documents: List[Dict[str, str]]) -> int:
        """Add documents async."""
        return await asyncio.to_thread(self.add_documents, documents)

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Search."""
        if type(top_k) is not int or not 1 <= top_k <= 20:
            raise ValueError("top_k must be an integer between 1 and 20")
        count = self._collection.count()
        if not count:
            return []
        results = self._collection.query(query_texts=[query], n_results=min(top_k, count))
        items = []
        if results["documents"] and results["documents"][0]:
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                items.append(
                    {
                        "title": meta.get("title", ""),
                        "content": doc,
                        "score": round(1.0 - dist, 4),
                        "chunk": meta.get("chunk_index", 0),
                        "document_id": meta.get("document_id", ""),
                        "version": meta.get("version", ""),
                    }
                )
        return items

    async def search_async(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Search async."""
        return await asyncio.to_thread(self.search, query, top_k)

    @property
    def doc_count(self) -> int:
        return self._collection.count()

    async def doc_count_async(self) -> int:
        """Doc count async."""
        return await asyncio.to_thread(self._collection.count)

    async def search_handler(self, params: Dict[str, Any], context: Any) -> List[Dict]:
        """Search handler."""
        query = params.get("query", "")
        top_k = params.get("top_k", 5)
        return await self.search_async(query, top_k=top_k)

    def _chunk_text(self, text: str, chunk_size: int = 500) -> List[str]:
        """Chunk text."""
        if len(text) <= chunk_size:
            return [text] if text.strip() else []
        chunks = []
        current = ""
        sentences = text.replace("\n", "。").split("。")
        sentences = [sent[i : i + chunk_size] for sent in sentences for i in range(0, len(sent), chunk_size)]
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            if len(current) + len(sent) + 1 > chunk_size:
                if current:
                    chunks.append(current)
                current = sent
            else:
                current = f"{current}。{sent}" if current else sent
        if current:
            chunks.append(current)
        return chunks

    def _load_default_docs(self) -> None:

        self.add_documents([dict(document_id=p["id"], title=p["title"], content=p["text"]) for p in POLICIES.values()])
