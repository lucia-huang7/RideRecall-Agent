"""English model contracts with a deterministic, network-free Messages adapter."""
import asyncio
import json
import re
import time
from types import SimpleNamespace
from uuid import uuid4


TEMPLATES = {
    "refund": ["The driver never arrived but I was charged a cancellation fee", "My fare is higher than the estimate", "Please refund the unsupported charge"],
    "payment_issue": ["I was charged twice", "Is this pending authorization a duplicate payment?"],
    "technical_login": ["I cannot log in to the app", "Login fails with error 401"],
    "technical_crash": ["The app crashed", "The pickup map has an error"],
    "human_handoff": ["I want a human agent", "Please connect me to a support representative"],
    "query": ["What is the policy?", "Please keep my explanation attached", "What happens next?"],
    "greeting": ["Hello", "Hi there"],
}


def classify(text):
    value = text.lower()
    if re.search(r"\b(human|representative|supervisor)\b", value) and not re.search(r"\b(no|not|don't|do not)\s+(want|need)\b", value):
        return "human_handoff"
    if any(s in value for s in ["log in", "login", "sign in", "401"]):
        return "technical_login"
    if any(s in value for s in ["crash", "app error", "map error", "500"]):
        return "technical_crash"
    if any(s in value for s in ["twice", "duplicate", "authorization", "pending", "payment"]):
        return "payment_issue"
    if any(s in value for s in ["fee", "fare", "refund", "charged", "charge", "driver", "pickup", "picked"]):
        return "refund"
    if re.fullmatch(r"(hi|hello|hey)[.! ]*", value):
        return "greeting"
    return "query"


def response(text=None, tools=None):
    return SimpleNamespace(content=tools if tools is not None else [{"type": "text", "text": text}], stop_reason="tool_use" if tools is not None else "end_turn")


class LocalMessages:
    def __init__(self, owner):
        self.owner = owner

    async def create(self, **kwargs):
        system = kwargs.get("system", "")
        messages = kwargs.get("messages", [])
        prompt = messages[-1].get("content", "") if messages else ""
        phase = "agent" if "RIDE_AGENT:" in system else "utility"
        if isinstance(prompt, str):
            try:
                payload = json.loads(prompt)
                phase = payload.get("task", phase)
            except (ValueError, TypeError):
                payload = {}
        else:
            payload = {}
        stamp = dict(id=uuid4().hex, phase=phase, started=time.monotonic())
        self.owner.calls.append(stamp)
        # An asynchronous boundary exercises the original parallel dispatch path.
        await asyncio.sleep(0)
        if phase == "intent":
            output = response(json.dumps(dict(intent=classify(payload["message"]), confidence=.96, reasoning="Deterministic English ride classifier; this is not an LLM prediction.")))
        elif phase == "rewrite":
            query = payload["query"]
            output = response(json.dumps([query, query + " eligibility evidence", query + " human review payment boundaries"]))
        elif phase == "rerank":
            terms = set(re.findall(r"[a-z]+", payload["query"].lower()))
            ranked = sorted(range(len(payload["items"])), key=lambda i: 3 * len(terms.intersection(re.findall(r"[a-z]+", payload["items"][i]["title"].lower()))) + len(terms.intersection(re.findall(r"[a-z]+", payload["items"][i]["content"].lower()))), reverse=True)
            output = response(json.dumps(ranked))
        elif phase == "compose":
            texts = list(dict.fromkeys(r["content"] for r in payload["responses"]))
            sentences = list(dict.fromkeys(sentence for text in texts for sentence in re.split(r"(?<=[.!?])\s+", text)))
            output = response(" ".join(sentences))
        else:
            role = system.split("RIDE_AGENT:", 1)[-1].split("]", 1)[0]
            stamp["agent"] = role
            last_results = prompt if isinstance(prompt, list) else []
            if last_results and all(b.get("type") == "tool_result" for b in last_results):
                data = {}
                for block in last_results:
                    try:
                        data[block["tool_use_id"].split(":", 1)[-1]] = json.loads(block["content"])
                    except (ValueError, KeyError):
                        pass
                output = response(self.answer(role, data))
            else:
                names = [t["name"] for t in kwargs.get("tools", [])]
                query = next((m["content"] for m in reversed(messages) if m.get("role") == "user" and isinstance(m.get("content"), str)), "ride policy")
                chosen = [n for n in names if n in ["get_trip", "get_charge_breakdown", "search_knowledge_base", "get_case_context"] or n.startswith("mcp_rides_")]
                output = response(tools=[dict(type="tool_use", id=role + ":" + n, name=n, input={"query": query[:1500], "top_k": 2} if n == "search_knowledge_base" else {}) for n in chosen])
        stamp["finished"] = time.monotonic()
        return output

    @staticmethod
    def answer(role, data):
        evidence = data.get("search_knowledge_base", {}).get("results", [])
        citations = ""
        policy = " ".join(f"{d['content']} [{d['document_id'][:8]}:{d['chunk']}]" for d in evidence) if evidence else "No matching policy was retrieved; human review is needed."
        trip = data.get("get_trip", {})
        charges = data.get("get_charge_breakdown", {})
        if role == "billing":
            settled = charges.get("settled")
            estimate = charges.get("estimated")
            text = f"I checked the payment records: settled fare ${settled / 100:.2f}" if type(settled) is int else "The payment records could not be verified"
            if estimate:
                text += f", original estimate ${estimate / 100:.2f}"
            text += f". Settled record count: {charges.get('settled_count', 'unverified')}; pending authorization: ${charges.get('pending_authorization',0)/100:.2f}. " + policy
        elif role == "technical":
            text = "I reviewed the recorded trip events. "
            if trip.get("arrival_disputed"):
                text += "The arrival event conflicts with the pickup account. A recorded GPS arrival is not proof that you found the driver. "
            else:
                text += "The linked trip is " + trip.get("status", "not verified").lower() + ". "
            text += policy
        elif role == "escalation":
            text = "Your request for human review has been identified. The full conversation and evidence will remain on this case. " + policy
        else:
            text = "I used the linked trip and retrieved policy to review your question. " + policy
        context = data.get("get_case_context", {})
        claims = context.get("account", "")
        if claims.strip():
            text += " Saved rider account (unverified): " + claims.strip() + ". "
        if context.get("requires_clarification"):
            text += " Please clarify the corrected field or narrow the requested history; the account cannot be treated as complete. "
        remote = data.get("mcp_rides_read_trip_record", {})
        if remote.get("success"):
            record = remote.get("data", {}).get("structured", {})
            text += " Independent MCP record checked: " + str(record.get("trip_id", "linked trip")) + ". "
        elif remote:
            text += " The independent MCP service could not be verified; human review may be needed. "
        notes = [item.get("text", "") for item in context.get("evidence", [])]
        if notes:
            text += " Attached rider evidence (unverified): " + "; ".join(notes) + ". "
        return text + (" " + citations if citations else "") + " No payment action was performed by this analysis. Use the case controls for the next step."


class LocalModel:
    """Same Messages interface as a live client; only model outputs are simulated."""
    def __init__(self):
        self.calls = []
        self.messages = LocalMessages(self)

    async def close(self):
        return None
