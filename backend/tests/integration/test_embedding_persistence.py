"""Persisted local embeddings keep their configuration and require no provider."""

import math

import chromadb
import numpy as np
import pytest
from ride_agent.agent.intent import IntentRecognizer
from ride_agent.agent.pipeline import LocalHashEmbedding


def test_local_embedding_configuration_preserves_existing_vectors():
    embedding = LocalHashEmbedding()
    restored = LocalHashEmbedding.build_from_config(embedding.get_config())
    texts = ["A canceled ride was charged twice.", "My pickup entrance is Cedar."]
    expected = []
    for text in texts:
        vector = IntentRecognizer._local_embedding(text)
        norm = math.sqrt(sum(value * value for value in vector)) or 1
        expected.append([value / norm for value in vector])
    np.testing.assert_allclose(restored(texts), expected, rtol=1e-6, atol=1e-8)
    assert restored.name() == embedding.name()
    assert restored.default_space() == "l2"
    assert not restored.is_legacy()
    with pytest.raises(ValueError, match="Unsupported"):
        LocalHashEmbedding.build_from_config({"dimensions": 128})


def test_persisted_collection_reopens_with_the_same_local_configuration(tmp_path):
    path = str(tmp_path / "vectors")
    client = chromadb.PersistentClient(path=path)
    collection = client.get_or_create_collection("local-policies", embedding_function=LocalHashEmbedding())
    collection.upsert(ids=["cancellation"], documents=["Review the cancellation fee using recorded evidence."])
    reopened = chromadb.PersistentClient(path=path).get_collection(
        "local-policies", embedding_function=LocalHashEmbedding()
    )
    assert reopened.configuration["hnsw"]["space"] == "l2"
    assert reopened.count() == 1
    assert reopened.query(query_texts=["cancellation fee"], n_results=1)["ids"] == [["cancellation"]]
    assert reopened.configuration["embedding_function"].get_config() == {"dimensions": 256}
