"""A learned example must enter the active ride embedding branch and clear stale cache."""

import asyncio
from copy import deepcopy

import ride_agent.agent.intent as intent_module
from ride_agent.agent.intent import IntentCategory, IntentRecognizer
from ride_agent.agent.intent_embedding import IntentEmbedding
from ride_agent.integrations.model_provider import LocalModel


def test_learning_updates_active_templates_and_invalidates_cached_context(monkeypatch):
    monkeypatch.setattr(intent_module, "TEMPLATES", deepcopy(intent_module.TEMPLATES))
    embedding = IntentEmbedding()
    embedding.provider, embedding.enabled, embedding.key = "local", False, ""
    recognizer = IntentRecognizer(api_key="unused", client=LocalModel(), model="demo", embedding=embedding)
    recognizer._cache["stale"] = object()
    recognizer._cache_expiries["stale"] = 999999999
    example = "Please reverse the unsupported cancellation assessment on my journey"
    recognizer.learn(example, IntentCategory.REFUND)
    assert example in intent_module.TEMPLATES["refund"]
    assert not recognizer._cache and not recognizer._cache_expiries
    result = asyncio.run(recognizer._embedding_recognize(example))
    assert result["intent"] == IntentCategory.REFUND
    assert result["confidence"] > 0.99
