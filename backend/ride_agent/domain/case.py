"""Ride-support state transitions, task skills and guarded tools."""

import asyncio
import os
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from ride_agent.agent.concurrency import KeyedLocks
from ride_agent.agent.pipeline import RideEngine
from ride_agent.agent.skills import SkillManager
from ride_agent.domain.catalog import RideCatalog, charge_breakdown
from ride_agent.integrations.settings import ModelSettings
from ride_agent.ops.admission import AnalysisAdmission
from ride_agent.ops.configuration import ConfigurationGate
from ride_agent.persistence.case_index import find_trip
from ride_agent.persistence.store import event, message, now
from ride_agent.retrieval.policy_index import MCPToolManager, Tool

MAX_RIDER_MESSAGES = 50
MAX_EVIDENCE_ITEMS = 20


def money(cents):
    return f"${cents / 100:.2f}"


class RideSupport:
    def __init__(self, store, catalog=None, model_settings=None):

        self.model_settings = model_settings or ModelSettings.from_environment()
        self.store = store
        self.catalog = catalog or RideCatalog()

        skill_root = Path(store.path).with_suffix(".skills")
        if not skill_root.exists():
            shutil.copytree(Path(__file__).parent.parent / "skills" / "ride_resolution", skill_root)

        if os.name == "posix":
            skill_root.chmod(448)
        self.skill_file = skill_root / "SKILL.md"
        self.skills = SkillManager(str(skill_root))
        self.skills.load()
        self.tools = MCPToolManager(api_key="local-demo-unused")
        for name, handler in [
            ("get_trip", self.trip_tool),
            ("get_charge_breakdown", self.charge_tool),
            ("search_demo_policy", self.policy_tool),
        ]:
            self.tools.register(
                Tool(
                    name=name,
                    description=name.replace("_", " "),
                    handler=handler,
                    schema={
                        "type": "object",
                        "properties": {"trip_id": {"type": "string"}},
                        "required": ["trip_id"],
                    },
                    cache_ttl=30,
                    timeout_s=3,
                )
            )

        self.configuration_gate = ConfigurationGate()

        self.admission = AnalysisAdmission()
        self.engine = RideEngine(self)
        self.engine_locks = KeyedLocks()

    async def analyze(self, trip_id, text, case):
        try:
            async with asyncio.timeout(self.admission.timeout_seconds):
                return await self.engine.run(trip_id, text, case)
        except TimeoutError:
            self.admission.timeouts += 1
            raise HTTPException(
                503,
                ("The analysis deadline was reached. Retry the same operation; no result was committed."),
                headers={"Retry-After": "2"},
            ) from None

    def attach_analysis(self, case, analysis):
        case["agent_mode"] = analysis.get("agent_mode", "single")
        effects = analysis.pop("_memory_effects", [])
        if effects:
            case.setdefault("_pending_projections", []).append(dict(id=analysis["id"], effects=effects))
        if analysis["status"] != "success" and case.get("proposal") and (not case.get("adjustment")):
            case["proposal"] = None
            case["status"] = "needs_review"
        case.setdefault("engine_runs", []).append(analysis)
        case["engine_runs"] = case["engine_runs"][-30:]
        case["agents"] = [analysis.get("roles", {}).get(name, name) for name in analysis.get("agents", [])]
        message(case, "assistant", analysis["response"])
        case["messages"][-1].update(analysis_id=analysis["id"], model_mode=analysis["mode"])
        snapshot = analysis.pop("_memory_snapshot", None)
        if snapshot is not None:
            snapshot["cursor"] = len(case["messages"])
            case["memory_snapshot"] = snapshot
        event(
            case,
            "analysis",
            "Evidence-based analysis completed" if analysis["status"] == "success" else "Analysis needs attention",
            (
                "Intent: "
                f"{analysis['intent']}"
                ". Roles executed: "
                f"{', '.join(analysis['agents']) or 'none'}"
                ". Retrieved sources: "
                f"{len(analysis['citations'])}"
                ". Mode: "
                f"{analysis['mode']}"
                "."
            ),
        )

    def trip(self, trip_id):
        try:
            return self.catalog.trip(trip_id)
        except KeyError:
            raise HTTPException(404, "Trip not found.")

    async def trip_tool(self, params, context):
        return self.trip(params["trip_id"])

    async def charge_tool(self, params, context):
        trip = self.trip(params["trip_id"])
        return charge_breakdown(trip)

    async def policy_tool(self, params, context):
        return self.catalog.policy(self.trip(params["trip_id"]))

    async def inspect(self, trip_id):
        names = ["get_trip", "get_charge_breakdown", "search_demo_policy"]
        results = await asyncio.gather(*(self.tools.call(name, {"trip_id": trip_id}) for name in names))
        if not all((r.success for r in results)):
            raise HTTPException(
                503,
                ("Trip review is temporarily unavailable. Please retry with the same request ID."),
            )
        trace = [
            dict(
                tool=r.tool_name,
                success=r.success,
                cached=r.cached,
                latency_ms=round(r.latency_ms, 2),
            )
            for r in results
        ]
        return ([r.data for r in results], trace)

    def create(self, db, owner, trip, description, topic, trace, case_id=None):

        existing = find_trip(db, owner, trip["id"])
        if existing:
            if description != existing["description"]:
                self.check_rider_budget(existing)
                message(existing, "rider", description)
                event(existing, "appeal", "Additional appeal saved", description, "rider")
                if existing["status"] == "resolved":
                    self.handoff(existing)
            return existing
        case = dict(
            id=case_id or "KU-" + uuid4().hex.upper(),
            owner=owner,
            trip_id=trip["id"],
            trip_issue=trip["issue"],
            topic=topic,
            title={
                "cancellation": "Cancellation fee review",
                "fare": "Unexpected fare",
                "payment": "Payment clarification",
            }[trip["issue"]],
            description=description,
            status="reviewing",
            created_at=now(),
            updated_at=now(),
            messages=[],
            events=[],
            evidence=[],
            trace=trace,
            adjustment=None,
            assigned_to=None,
            agents=["Trip investigator", "Fare specialist", "Resolution coordinator"],
            skill_names=[s.name for s in self.skills.skills],
            policy=self.catalog.policy(trip),
            facts=[],
            proposal=None,
            handoff=None,
        )
        event(
            case,
            "created",
            "Your case is open",
            "Your trip and original explanation are attached.",
        )
        message(case, "rider", description)
        if trip["issue"] == "cancellation":
            case["status"] = "needs_review"
            case["facts"] = [
                dict(
                    label="Trip record",
                    text="The trip was canceled before any ride took place.",
                    source="Trip service",
                ),
                dict(
                    label="Payment record",
                    text=f"A {money(trip['total'])} cancellation fee was settled.",
                    source="Billing service",
                ),
                dict(
                    label="Unresolved conflict",
                    text=("The demo arrival event and the rider's pickup account need review."),
                    source="Requires verification",
                ),
            ]
            response = (
                "I understand you are disputing this "
                f"{money(trip['total'])}"
                " cancellation fee. The trip record and your account need to "
                "be reviewed together. I will not treat a GPS arrival event "
                "as proof you found the driver. You can add evidence or "
                "request a human review "
                "below."
            )
            event(
                case,
                "review",
                "A pickup conflict needs review",
                "No automatic refund has been approved.",
            )
        elif trip["issue"] == "fare" and trip["eligibility"] == "adjust":
            case["status"] = "awaiting_confirmation"
            amount = trip["total"] - trip["estimated"]
            case["proposal"] = dict(
                amount=amount,
                reason=("The fictional billing record contains an unsupported fare difference."),
                policy_id=case["policy"]["id"],
            )
            case["facts"] = [
                dict(
                    label="Original estimate",
                    text=money(trip["estimated"]),
                    source="Trip service",
                ),
                dict(
                    label="Settled fare",
                    text=money(trip["total"]),
                    source="Billing service",
                ),
                dict(
                    label="Eligible difference",
                    text=money(amount),
                    source="Demo policy engine",
                ),
            ]
            response = (
                "Your estimate was "
                f"{money(trip['estimated'])}"
                " and your settled fare was "
                f"{money(trip['total'])}"
                ". This demo's billing review supports a "
                f"{money(amount)}"
                " adjustment. Nothing has been changed yet. Review the "
                "proposal and confirm if you would like to "
                "proceed."
            )
            event(
                case,
                "proposal",
                "An adjustment is ready to review",
                f"Your confirmation is required for {money(amount)}.",
            )
        elif trip["issue"] == "fare":
            case["status"] = "needs_review" if trip["eligibility"] == "review" else "awaiting_rider"
            case["facts"] = [
                dict(
                    label="Original estimate",
                    text=money(trip["estimated"]),
                    source="Trip service",
                ),
                dict(
                    label="Settled fare",
                    text=money(trip["total"]),
                    source="Billing service",
                ),
            ]
            response = (
                "The recorded fare does not support an automatic adjustment "
                "under the applicable policy. A specialist can review the "
                "evidence. No payment action was "
                "taken."
            )
            event(case, "review", "Fare evidence reviewed", response)
        else:
            charges = charge_breakdown(trip)
            duplicate = charges["settled_count"] > 1
            case["status"] = "needs_review" if duplicate else "awaiting_rider"
            case["facts"] = [
                dict(
                    label="Settled charge",
                    text=money(charges["settled"]),
                    source="Billing service",
                ),
                dict(
                    label="Settled record count",
                    text=str(charges["settled_count"]),
                    source="Billing service",
                ),
                dict(
                    label="Pending authorization",
                    text=money(charges["pending_authorization"]),
                    source="Billing service",
                ),
            ]
            response = (
                (
                    "The records contain multiple settled charges and require "
                    "specialist review. No automatic refund is "
                    "authorized."
                )
                if duplicate
                else (
                    "The records contain one settled charge. Pending "
                    "authorization is tracked separately and is not evidence of "
                    "another settled "
                    "charge."
                )
            )
            event(case, "review", "Payment records checked", response)
        if topic != trip["issue"]:
            case["status"] = "needs_review"
            case["proposal"] = None
            response = (
                "I saved your explanation and linked the trip. This request "
                "needs a closer review; I will not assume it is only a fare "
                "issue. Add any relevant details or request human "
                "review."
            )
        message(case, "system", response)
        return case

    def handoff(self, case):
        if case["status"] in ("waiting_for_agent", "in_review"):
            return
        if case["status"] == "resolved":
            case["proposal"] = None
        case["status"] = "waiting_for_agent"
        case["assigned_to"] = None
        case["handoff"] = dict(
            summary=case["description"],
            evidence_count=len(case["evidence"]),
            open_question=("Review the rider's account against trip and payment evidence."),
            prior_adjustment=case["adjustment"],
        )
        event(
            case,
            "handoff",
            "Human review requested",
            (
                "The full conversation, evidence, and prior actions are "
                "attached. A demo agent has not accepted the case "
                "yet."
            ),
            "rider",
        )
        message(
            case,
            "assistant",
            (
                "Your case is in the demo review queue. Your explanation, "
                "evidence, and previous steps are attached. You do not need "
                "to start over. A simulated specialist can pick it up in the "
                "Agent desk."
            ),
        )

    @staticmethod
    def check_rider_budget(case):
        if sum((m["role"] == "rider" for m in case["messages"])) >= MAX_RIDER_MESSAGES:
            raise HTTPException(
                409,
                ("This case has reached its chat limit. Request human review to continue with its history intact."),
            )

    def validate_adjustment(self, case):
        trip = self.trip(case["trip_id"])
        policy = self.catalog.policy(trip)
        proposal = case.get("proposal") or {}
        amount = proposal.get("amount")
        if type(amount) is not int or amount <= 0 or amount > trip["total"]:
            raise HTTPException(
                409,
                ("The recorded payment does not support this adjustment. Request a new review."),
            )
        if proposal.get("policy_id") != policy["id"] or any(
            (case["policy"].get(key) != policy.get(key) for key in ("id", "version", "rules"))
        ):
            raise HTTPException(
                409,
                "The policy release changed. Request a new review before confirming.",
            )
        if trip["issue"] == "fare" and (trip["eligibility"] != "adjust" or amount != trip["total"] - trip["estimated"]):
            raise HTTPException(
                409,
                "The fare evidence changed. Request a new review before confirming.",
            )
        if trip["issue"] == "cancellation" and amount != trip["total"]:
            raise HTTPException(
                409,
                ("The settled cancellation fee changed. Request a new review before confirming."),
            )
        if trip["issue"] not in ("fare", "cancellation"):
            raise HTTPException(
                409,
                ("This payment issue requires specialist review rather than an automatic adjustment."),
            )
        return amount

    def act(self, case, action, text="", filename=None):
        if action == "handoff":
            self.handoff(case)
        elif action == "message":
            self.check_rider_budget(case)
            message(case, "rider", text)
            lower = text.lower()
            if any((word in lower for word in ["human", "person", "agent", "appeal", "not resolved"])):
                self.handoff(case)
            else:
                response = "I added this to the same case. Your earlier explanation and trip details are preserved."
                if any(
                    (
                        word in lower
                        for word in [
                            "wrong",
                            "incorrect",
                            "not what",
                            "different issue",
                        ]
                    )
                ):
                    case["description"] = text
                    case["proposal"] = None
                    if case["status"] not in ("waiting_for_agent", "in_review"):
                        case["status"] = "needs_review"
                    event(
                        case,
                        "correction",
                        "Your correction was recorded",
                        text,
                        "rider",
                    )
                    response = (
                        "Thank you for correcting me. I updated the case summary and "
                        "removed any unconfirmed proposal. The original record "
                        "remains in the timeline. You can request human "
                        "review."
                    )
                elif case["status"] == "awaiting_confirmation":
                    response += " Open case details to review and explicitly confirm your adjustment proposal."
                else:
                    response += " You can add evidence or request human review; no payment action has been taken."
                message(case, "system", response)
        elif action == "evidence":
            if len(case["evidence"]) >= MAX_EVIDENCE_ITEMS:
                raise HTTPException(
                    409,
                    ("This case already has twenty evidence items. Ask a specialist which evidence is needed."),
                )
            item = dict(
                id=uuid4().hex,
                text=text,
                filename=filename,
                source="Rider",
                at=now(),
                verified=False,
            )
            case["evidence"].append(item)
            event(case, "evidence", "Evidence added", text, "rider")
            message(
                case,
                "assistant",
                (
                    "Your evidence is attached to this case and will be visible "
                    "during review. It has not yet been independently "
                    "verified."
                ),
            )
        elif action == "confirm":
            if case["adjustment"]:
                return
            if case["status"] != "awaiting_confirmation" or not case["proposal"]:
                raise HTTPException(409, "There is no eligible adjustment awaiting confirmation.")
            self.validate_adjustment(case)
            case["adjustment"] = dict(
                id="ADJ-" + uuid4().hex.upper(),
                amount=case["proposal"]["amount"],
                status="simulated_complete",
                at=now(),
            )
            case["status"] = "resolved"
            event(
                case,
                "adjustment",
                "Demo adjustment recorded",
                f"{money(case['adjustment']['amount'])}. No real money was moved.",
                "rider",
            )
            message(
                case,
                "assistant",
                (
                    "The "
                    f"{money(case['adjustment']['amount'])}"
                    " demo adjustment is recorded. No real payment was made. You "
                    "can request another review if the outcome does not address "
                    "your concern."
                ),
            )
        elif action == "close":
            if case["status"] != "awaiting_rider":
                raise HTTPException(
                    409,
                    "Only an explanation awaiting your response can be marked helpful.",
                )
            case["status"] = "resolved"
            event(
                case,
                "resolved",
                "Explanation marked helpful",
                "The rider accepted the explanation. No adjustment was made.",
                "rider",
            )
        else:
            raise HTTPException(400, "Unknown action.")

    def staff_options(self, case, staff_id=None):
        if case["status"] == "waiting_for_agent":
            return ["accept"]
        if case["status"] != "in_review" or (staff_id and case.get("assigned_to") != staff_id):
            return []
        actions = ["reply", "resolve"]
        trip = self.trip(case["trip_id"])
        if trip["issue"] == "cancellation" and trip["total"] > 0 and (not case.get("adjustment")):
            actions.append("propose_adjustment")
        return actions

    def staff(self, case, action, text, staff_id=None):
        actor = staff_id or "agent"
        if staff_id and action != "accept" and (case.get("assigned_to") != staff_id):
            raise HTTPException(403, "Only the assigned specialist can update this case.")
        if action == "accept":
            if case["status"] != "waiting_for_agent":
                raise HTTPException(409, "This case is not waiting for an agent.")
            case["assigned_to"] = staff_id or "Alex · Demo specialist"
            case["status"] = "in_review"
            event(
                case,
                "assigned",
                f"{staff_id or 'Alex'} accepted your case",
                "The specialist has the full case history.",
                actor,
            )
            message(
                case,
                "agent",
                (
                    "Hi, I'm "
                    f"{staff_id}"
                    ", your support specialist. I have your trip, explanation, "
                    "and evidence in front of me. You do not need to repeat "
                    "them."
                )
                if staff_id
                else (
                    "Hi, I'm Alex, your simulated support specialist. I have "
                    "your trip, explanation, and evidence in front of me. You do "
                    "not need to repeat "
                    "them."
                ),
            )
        elif action == "propose_adjustment":
            if (
                case["status"] != "in_review"
                or case["adjustment"]
                or self.trip(case["trip_id"])["issue"] != "cancellation"
            ):
                raise HTTPException(
                    409,
                    ("This case is not eligible for a new specialist adjustment proposal."),
                )
            if not text.strip():
                raise HTTPException(422, "Explain the evidence behind your proposal.")
            trip = self.trip(case["trip_id"])
            amount = trip["total"]
            if amount <= 0:
                raise HTTPException(409, "There is no settled cancellation fee to adjust.")
            case["policy"] = self.catalog.policy(trip)
            case["proposal"] = dict(amount=amount, reason=text.strip(), policy_id=case["policy"]["id"])
            case["status"] = "awaiting_confirmation"
            event(
                case,
                "proposal",
                "Specialist offered a fee adjustment",
                text.strip(),
                actor,
            )
            message(
                case,
                "agent",
                (
                    "After reviewing the evidence, I am proposing a "
                    f"{money(amount)}"
                    " demo fee adjustment. Reason: "
                    f"{text.strip()}"
                    " Please review and confirm the proposal. No payment action "
                    "has been "
                    "taken."
                ),
            )
        elif action in ("reply", "resolve"):
            if case["status"] != "in_review":
                raise HTTPException(409, "Accept this case before replying or resolving it.")
            if not text.strip():
                raise HTTPException(422, "A written explanation is required.")
            message(case, "agent", text)
            event(
                case,
                "decision" if action == "resolve" else "reply",
                "Specialist decision" if action == "resolve" else "Specialist replied",
                text,
                actor,
            )
            if action == "resolve":
                case["status"] = "resolved"
        else:
            raise HTTPException(400, "Unknown staff action.")
