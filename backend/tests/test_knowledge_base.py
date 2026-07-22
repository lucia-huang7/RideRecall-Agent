import sys
import types
import threading

try:
    import chromadb
except ModuleNotFoundError:
    sys.modules['chromadb'] = types.ModuleType('chromadb')

from tooling.knowledge_base import KnowledgeBase


class FakeCollection:
    def __init__(self):
        self.rows = {}
        self.calls = []

    def upsert(self, **kwargs):
        self.calls.append(kwargs)
        for i, doc, meta in zip(kwargs['ids'], kwargs['documents'], kwargs['metadatas']):
            self.rows[i] = (doc, meta)

    def get(self, where, include):
        rows = [(i, d, m) for i, (d, m) in self.rows.items() if all(m.get(k) == v for k, v in where.items())]
        return {'ids': [r[0] for r in rows], 'metadatas': [r[2] for r in rows]}

    def delete(self, ids):
        for i in ids:
            self.rows.pop(i, None)


def make_kb():
    kb = KnowledgeBase.__new__(KnowledgeBase)
    kb._write_lock = threading.RLock()
    kb._collection = FakeCollection()
    return kb


def test_repeated_document_import_is_idempotent():
    kb = make_kb()
    doc = {'title': '退款政策', 'content': '7 天内可以申请退款。'}
    assert kb.add_documents([doc, doc]) == 1
    assert kb.add_documents([doc]) == 1
    assert len(kb._collection.rows) == 1


def test_shorter_document_replaces_chunks_and_legacy_data():
    kb = make_kb()
    kb._collection.rows['legacy'] = ('旧内容', {'title': '政策'})
    kb.add_documents([{'title': '政策', 'content': ('旧规则' * 100 + '。') * 5}])
    assert len(kb._collection.rows) > 1
    assert 'legacy' not in kb._collection.rows
    kb.add_documents([{'title': '政策', 'content': '新的政策'}])
    assert len(kb._collection.rows) == 1
    assert next(iter(kb._collection.rows.values()))[0] == '新的政策'


def test_explicit_ids_allow_documents_with_same_title():
    kb = make_kb()
    kb.add_documents([{'document_id': 'a', 'title': '政策', 'content': '甲'},
                      {'document_id': 'b', 'title': '政策', 'content': '乙'}])
    assert len(kb._collection.rows) == 2
    kb.add_documents([{'document_id': 'a', 'title': '改名', 'content': '新甲'}])
    assert len(kb._collection.rows) == 2
    assert {d for d, m in kb._collection.rows.values()} == {'新甲', '乙'}


def test_long_unpunctuated_text_respects_chunk_size():
    kb = make_kb()
    chunks = kb._chunk_text('长' * 1201, chunk_size=500)
    assert [len(c) for c in chunks] == [500, 500, 201]
    assert ''.join(chunks) == '长' * 1201
