"""Versioned document chunks and embedding-backed policy retrieval."""

import asyncio
import hashlib
import json
import logging
import threading
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class KnowledgeBase:
    def __init__(self, collection):
        self._write_lock = threading.RLock()
        self._collection = collection

    def add_documents(self, documents: List[Dict[str, str]]) -> int:
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
        return await asyncio.to_thread(self.add_documents, documents)

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
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
        return await asyncio.to_thread(self.search, query, top_k)

    @property
    def doc_count(self) -> int:
        return self._collection.count()

    def _chunk_text(self, text: str, chunk_size: int = 500) -> List[str]:
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
