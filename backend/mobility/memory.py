"""Original memory algorithms with transactional case snapshots and embedded Redis semantics."""
import hashlib
import json

from fakeredis.aioredis import FakeRedis

from core.conversation_state import merge_state
from memory.conversation_memory import MemoryManager, MsgRole
from mobility.providers import response
from mobility.claims import ClaimLedger


def memory_prompt(task, **data):
    return json.dumps(dict(task="memory_" + task, **data))


class MemoryModel:
    """English offline summarization contract; original compression and storage still execute."""
    def __init__(self):
        self.messages = self

    async def create(self, **kwargs):
        data = json.loads(kwargs["messages"][-1]["content"])
        if data["task"] == "memory_profile":
            # No personal traits or preferences are inferred from complaints.
            return response(json.dumps(dict(preferences=[], entities={}, scope="case-only")))
        # Only structured rider records can enter a source-aware summary.
        records = data.get("messages", [])
        if records:
            text = "\n".join(m["content"] for m in records if m["role"] == "user")
        else:
            text = str(data.get("text", "")) if data["task"] == "memory_merge" else ""
        previous = str(data.get("previous", ""))
        return response((previous + "\n" + text).strip()[:800])

    async def close(self):
        pass


class SourceMemoryManager(MemoryManager):
    def _summary_prompt(self, text, messages):
        return memory_prompt("summary", messages=[dict(role=m.role.value, content=m.content) for m in messages])


class CaseMemory:
    def __init__(self, engine, case, trip_id):
        self.case = case or {}
        self.trip_id = trip_id
        self.uid = hashlib.sha256(json.dumps([self.case.get("owner", "fixture"), self.case.get("id", "new"), trip_id]).encode()).hexdigest()
        self.cid = self.uid
        self.redis = FakeRedis(decode_responses=True)
        self.manager = SourceMemoryManager(client=MemoryModel(), redis_client=self.redis,
            episodic=engine.chroma.get_or_create_collection("ride_memory_episodes_v1", embedding_function=engine.hash_embedding),
            profile=engine.chroma.get_or_create_collection("ride_memory_profiles_v1", embedding_function=engine.hash_embedding),
            prompt_builder=memory_prompt, strict_scope=True)
        self.saved = self.case.get("memory_snapshot", {})

    async def prepare(self, query):
        m, uid, cid = self.manager, self.uid, self.cid
        if self.saved.get("working"):
            await self.redis.rpush(m._wm_key(uid, cid), *self.saved["working"])
        for key, value in [(m._summary_key(uid, cid), self.saved.get("summary", "")), (m._state_key(uid, cid), json.dumps(self.saved.get("state", {})))]:
            await self.redis.set(key, value)
        for message in self.case.get("messages", [])[self.saved.get("cursor", 0):]:
            await m.add_message(uid, cid, MsgRole.USER if message["role"] == "rider" else MsgRole.ASSISTANT, message["content"])
        self.context = await m.get_context(uid, cid, query=query)
        # Rebuild from transactional source records, including old pre-ledger cases.
        # This is O(case history), not a distributed or constant-time memory store.
        self.ledger = ClaimLedger(self.case.get("messages", []) + [dict(role="rider", content=query)])
        self.context.claims = self.ledger.context(query)
        # Summary is a bounded overview. The separate source-backed claims survive truncation.
        self.context.summary = self.context.claims["account"][:800]
        self.context.relevant_history = []  # Raw historical quotes must not resurrect withdrawn claims.
        return self.context

    async def finish(self, text, result, intent):
        m, uid, cid = self.manager, self.uid, self.cid
        entities = dict(intent.entities)
        # A message cannot change the linked trip or establish a verified payment amount.
        entities["order_id"] = [self.case.get("trip_id", "") or self.trip_id]
        state = merge_state(self.context.state, entities, intent.intent_group, text, result["id"])
        await m.commit_turn(uid, cid, result["id"], text,
            dict(status=result["status"], response=result["response"], request_id=result["id"]), state=state)
        await m.maintain(uid, cid)
        context = await m.get_context(uid, cid, query=text)
        snapshot = dict(working=await self.redis.lrange(m._wm_key(uid, cid), 0, -1), summary=context.summary, state=context.state, claim_ledger=dict(**self.context.claims, versions=self.ledger.facts))
        result["memory"] = dict(summary=self.context.claims["account"][:800], recent_count=len(context.recent_messages), episodic_count=len(context.relevant_history), profile=context.user_profile, state=context.state, scope="This case only", backend="Embedded Redis-compatible adapter + SQLite snapshot + Chroma", mode="Source-backed active claims + bounded source-aware summary", context_used=True, claims=self.context.claims)
        result["_memory_snapshot"] = snapshot
        result["methods"] += ["MemoryManager.get_context", "MemoryManager.commit_turn", "MemoryManager.maintain", "conversation_state.merge_state"]
        await self.redis.aclose()
        return result
