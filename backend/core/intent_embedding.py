"""意图匹配专用免费 BGE-M3 客户端；整次匹配在同一向量空间内完成。"""
import asyncio
import hashlib
import json
import math
import os
import time

import httpx


class IntentEmbedding:
    MODEL = 'BAAI/bge-m3'
    URL = 'https://api.siliconflow.cn/v1/embeddings'

    def __init__(self):
        self.key = os.getenv('SILICONFLOW_API_KEY', '').strip()
        self.provider = os.getenv('INTENT_EMBEDDING_PROVIDER', 'auto').strip().lower()
        if self.provider not in {'auto', 'local', 'siliconflow'}:
            raise ValueError('INTENT_EMBEDDING_PROVIDER 只支持 auto/local/siliconflow')
        self.enabled = self.provider != 'local' and bool(self.key)
        self.retry_at = 0.0
        self.last_error = None
        self.remote_templates = {}
        self.local_templates = {}
        self.lock = asyncio.Lock()

    def status(self):
        return {'configured_provider': self.provider, 'online_configured': self.enabled,
                'model': self.MODEL if self.enabled else 'local-ngram-256',
                'cooldown': time.monotonic() < self.retry_at, 'last_error': self.last_error}

    async def vectors(self, message, templates, local_embed):
        # 快照防止 learn 在远程请求期间改变模板顺序或数量。
        snapshot = {key: tuple(texts) for key, texts in templates.items()}
        fingerprint = hashlib.sha256(json.dumps([(str(k), v) for k, v in snapshot.items()], ensure_ascii=True).encode()).hexdigest()
        if self.enabled and time.monotonic() >= self.retry_at:
            try:
                async with asyncio.timeout(8):
                    async with httpx.AsyncClient(timeout=6, follow_redirects=False) as client:
                        async with self.lock:
                            if time.monotonic() < self.retry_at:
                                raise RuntimeError('cooldown')
                            if fingerprint not in self.remote_templates:
                                texts = [text for group in snapshot.values() for text in group]
                                vectors = []
                                for start in range(0, len(texts), 32):
                                    vectors.extend(await self._request(client, texts[start:start+32]))
                                if len({len(v) for v in vectors}) != 1:
                                    raise ValueError('template_dimension_mismatch')
                                groups, offset = {}, 0
                                for key, group in snapshot.items():
                                    groups[key] = vectors[offset:offset+len(group)]
                                    offset += len(group)
                                self.remote_templates = {fingerprint: groups}
                            groups = self.remote_templates[fingerprint]
                        query = (await self._request(client, [message]))[0]
                        if any(len(v) != len(query) for group in groups.values() for v in group):
                            self.remote_templates.clear()
                            raise ValueError('query_dimension_mismatch')
                        self.last_error = None
                        return groups, query, 'siliconflow/BAAI/bge-m3'
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                # 不记录异常全文，避免暴露请求内容或认证信息。
                self.last_error = (f'http_{ex.response.status_code}' if isinstance(ex, httpx.HTTPStatusError) else type(ex).__name__)
                self.retry_at = time.monotonic() + 60
        if fingerprint not in self.local_templates:
            self.local_templates = {fingerprint: {key: [local_embed(t) for t in group] for key, group in snapshot.items()}}
        return self.local_templates[fingerprint], local_embed(message), 'local-ngram'

    async def _request(self, client, texts):
        response = await client.post(self.URL, headers={'Authorization': 'Bearer ' + self.key},
                                     json={'model': self.MODEL, 'input': texts, 'encoding_format': 'float'})
        response.raise_for_status()
        rows = response.json().get('data')
        if not isinstance(rows, list) or len(rows) != len(texts):
            raise ValueError('invalid_embedding_count')
        result = [None] * len(texts)
        dimension = None
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('invalid_embedding_row')
            index, vector = row.get('index'), row.get('embedding')
            if type(index) is not int or not 0 <= index < len(texts) or result[index] is not None:
                raise ValueError('invalid_embedding_index')
            if not isinstance(vector, list) or not vector or any(type(x) not in (int, float) or not math.isfinite(x) for x in vector):
                raise ValueError('invalid_embedding_vector')
            dimension = dimension or len(vector)
            norm = math.sqrt(sum(x*x for x in vector))
            if len(vector) != dimension or not math.isfinite(norm) or norm == 0:
                raise ValueError('invalid_embedding_dimension_or_norm')
            result[index] = [x/norm for x in vector]
        return result
