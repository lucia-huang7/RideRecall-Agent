"""Runnable integration checks for the configured ride catalog."""

import json
from datetime import datetime, timezone
from uuid import uuid4

from ride_agent.evaluation.evaluator import IntentEvaluator, IntentTestCase
from ride_agent.ops.diagnostics import DiagnosticCases, select_trip

SCENARIOS = [
    dict(
        name="Pickup and fee dispute",
        issue="cancellation",
        text=("The driver never arrived but I was charged a cancellation fee. Please refund it."),
        intent="refund",
        roles={"technical", "billing"},
    ),
    dict(
        name="Unexpected fare",
        issue="fare",
        text="My fare is higher than the estimate. Please review the charge.",
        intent="refund",
        roles={"billing"},
    ),
    dict(
        name="Pending authorization",
        issue="payment",
        text="Is this pending authorization a duplicate payment?",
        intent="payment_issue",
        roles={"billing"},
    ),
    dict(
        name="Human review",
        issue=None,
        text="Please connect me to a human representative.",
        intent="human_handoff",
        roles={"escalation"},
    ),
    dict(
        name="App and billing compound issue",
        issue=None,
        text="The app crashed with error 500 and I was charged a cancellation fee.",
        intent="technical_crash",
        roles={"technical", "billing"},
    ),
]


async def evaluate_engine(engine):
    intent_report = await IntentEvaluator(engine.recognizer).evaluate(
        [IntentTestCase(message=s["text"], expected_intent=s["intent"]) for s in SCENARIOS]
    )
    rows = []
    for scenario in SCENARIOS:
        trip = select_trip(engine.support.catalog, scenario["issue"])
        if trip is None:
            rows.append(
                dict(
                    name=scenario["name"],
                    status="not_applicable",
                    passed=False,
                    checks={},
                    intent=scenario["intent"],
                    agents=[],
                    sources=0,
                    reason="No configured trip supports this record-specific fixture.",
                )
            )
            continue
        run = await engine.run(trip["id"], scenario["text"])
        checks = dict(
            intent=run["intent"] == scenario["intent"],
            roles=run["agents"] == ["general"]
            if run.get("agent_mode") == "single"
            else scenario["roles"].issubset(run["agents"]),
            retrieval=bool(run["citations"]),
            complete=run["status"] == "success",
        )
        rows.append(
            dict(
                trip_id=trip["id"],
                name=scenario["name"],
                passed=all(checks.values()),
                checks=checks,
                intent=run["intent"],
                agents=run["agents"],
                sources=len(run["citations"]),
                latency_ms=run["latency_ms"],
            )
        )
    rows.extend(await evaluate_memory(engine))
    applicable = [row for row in rows if row.get("status") != "not_applicable"]
    return dict(
        dataset_id=engine.support.catalog.dataset_id,
        skipped=len(rows) - len(applicable),
        id=uuid4().hex,
        created_at=datetime.now(timezone.utc).isoformat(),
        suite="ride-core-memory-v2",
        mode=engine.mode,
        total=len(applicable),
        passed=sum((r["passed"] for r in applicable)),
        cases=rows,
        intent_metrics=intent_report,
        caveat=(
            "These are integration fixtures, not a real-world accuracy "
            "benchmark. In demo mode the model branch is simulated; "
            "local vectors, voting, retrieval, routing, and tool "
            "execution are "
            "real."
        ),
    )


async def evaluate_memory(engine):
    """Exercise compression, correction, handoff and isolation across real turns."""

    fixtures = DiagnosticCases(engine)
    rows = []
    primary = select_trip(engine.support.catalog, "cancellation") or select_trip(engine.support.catalog)
    alternate = next(
        (trip for trip in engine.support.catalog.trips if trip["id"] != primary["id"]),
        primary,
    )
    case = dict(id=uuid4().hex, owner=uuid4().hex, trip_id=primary["id"], messages=[])

    async def turn(text, record=case):
        return await fixtures.turn(record, text)

    def check(name, checks, run):
        rows.append(
            dict(
                name=name,
                passed=all(checks.values()),
                checks=checks,
                intent=run["intent"],
                agents=run["agents"],
                sources=len(run["citations"]),
            )
        )

    try:
        await turn(("The driver never arrived. My pickup landmark was CEDAR-17. I dispute $6.50."))
        run = await turn("Correction: my disputed amount is $5.25.")
        check(
            "Corrections update tracked amounts",
            dict(amount=run["memory"]["state"]["entities"]["amount"] == ["$5.25"]),
            run,
        )
        for i in range(9):
            run = await turn(f"Keep this case open while I check evidence, update {i}.")
        check(
            "Long conversations compact durable context",
            dict(
                summary=bool(run["memory"]["summary"]),
                episodes=run["memory"]["episodic_count"] > 0,
                bounded=run["memory"]["recent_count"] < 15,
            ),
            run,
        )
        run = await turn("What pickup landmark did I already provide?")
        check(
            "Earlier claims remain in the actual answer",
            dict(
                answer="CEDAR-17" in run["response"],
                amount=run["memory"]["state"]["entities"]["amount"] == ["$5.25"],
            ),
            run,
        )
        run = await turn(("Connect me to a human representative without asking me to repeat my account."))
        check(
            "Handoff receives the existing account",
            dict(
                role=run["agents"] == ["general"]
                if run.get("agent_mode") == "single"
                else "escalation" in run["agents"],
                account="CEDAR-17" in run["response"],
            ),
            run,
        )
        other = dict(id=uuid4().hex, owner=case["owner"], trip_id=alternate["id"], messages=[])
        run = await turn("What landmark did I provide for this trip?", other)
        check(
            "Another case cannot retrieve these claims",
            dict(
                answer="CEDAR-17" not in run["response"],
                episodes=run["memory"]["episodic_count"] == 0,
            ),
            run,
        )
        restored = json.loads(json.dumps(case))
        run = await turn("Please retain my original pickup landmark.", restored)
        check(
            "Serialized snapshots restore memory",
            dict(
                answer="CEDAR-17" in run["response"],
                amount=run["memory"]["state"]["entities"]["amount"] == ["$5.25"],
            ),
            run,
        )
    finally:
        await fixtures.close()
    return rows
