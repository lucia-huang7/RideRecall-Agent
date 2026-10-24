"""Conversation state."""

import re


def merge_state(previous, entities, intent_group, message, request_id):
    previous = previous if isinstance(previous, dict) else {}
    domain = {"billing": "commerce", "query": "commerce"}.get(intent_group, intent_group)
    neutral = domain in {
        "greeting",
        "feedback",
        "other",
        "complaint",
        "request",
        "escalation",
    }
    old = previous.get("entities", {})
    sources = previous.get("sources", {})
    old = old if isinstance(old, dict) else {}
    sources = sources if isinstance(sources, dict) else {}
    changed_order = bool(entities.get("order_id") and old.get("order_id") and (entities["order_id"] != old["order_id"]))
    new_task = bool(
        re.search("(?:换个|新的|另一个)(?:问题|订单)|另一笔订单", message, re.I)
        or re.search("\\b(?:another|different|new) (?:trip|case|issue)\\b", message, re.I)
    )
    if neutral:
        domain = (
            ("commerce" if entities.get("order_id") else "general")
            if new_task
            else previous.get("domain", "commerce" if entities.get("order_id") else "general")
        )
    keep = not changed_order and (not new_task) and (neutral or domain == previous.get("domain"))
    merged = {key: list(value) for key, value in old.items() if isinstance(value, list)} if keep else {}
    origins = dict(sources) if keep else {}
    for key, value in entities.items():
        if value:
            merged[key] = list(value)
            origins[key] = request_id
        else:
            merged.setdefault(key, [])
    return {"domain": domain, "entities": merged, "sources": origins}
