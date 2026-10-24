"""Reference state machine over event facts, independent of the application/parser.

This module imports no service, catalog business rule, claims resolver or model.
Its small policy specification is an evaluation oracle, not production authority.
"""


def financial_expectation(world):
    settled = sum((e["amount"] for e in world["events"] if e["type"] == "settled"))
    count = sum((e["type"] == "settled" for e in world["events"]))
    pending = sum((e["amount"] for e in world["events"] if e["type"] == "pending"))
    estimate = next((e["amount"] for e in world["events"] if e["type"] == "estimate"))
    if world["issue"] == "cancellation" or (world["issue"] == "payment" and count > 1):
        status, proposal = ("needs_review", None)
    elif world["issue"] == "fare":
        excess = settled - estimate
        if excess > world["cap"]:
            status, proposal = ("needs_review", None)
        elif excess > 0:
            status, proposal = ("awaiting_confirmation", excess)
        else:
            status, proposal = ("awaiting_rider", None)
    else:
        status, proposal = ("awaiting_rider", None)
    return dict(
        status=status,
        proposal=proposal,
        settled=settled,
        settled_count=count,
        pending=pending,
        estimated=estimate,
    )


def label_episode(world, steps, semantics):
    money = financial_expectation(world)
    state = dict(status=money["status"], proposal=money["proposal"], adjustment=None, evidence=0)
    facts = {"boarding label": world["old"], "supporting reference": world["support"]}
    clarification = False
    dependency = True
    labels = []
    for step, semantic in zip(steps, semantics):
        kind = step["kind"]
        label = dict(http=200)
        if kind == "fault_disconnect":
            dependency = False
        elif kind == "fault_reconnect":
            dependency = True
        elif kind == "evidence":
            state["evidence"] += 1
        elif kind in ("create", "message"):
            operation = semantic.get("operation")
            if operation == "replace":
                facts["boarding label"] = world["new"]
                clarification = False
            elif operation == "ambiguous":
                clarification = True
            if kind == "create" and (not dependency):
                state.update(status="needs_review", proposal=None)
            label.update(
                engine_status="success" if dependency else "partial",
                claims=dict(facts),
                requires_clarification=clarification,
                policy_id=world["policy_id"],
                policy_release=world["release"],
                forbidden=[]
                if clarification
                else [world["old"]]
                if facts["boarding label"] == world["new"]
                else [world["new"]],
            )
            if clarification:
                label["forbidden"] = [world["new"]]
        elif kind == "handoff":
            state["status"] = "waiting_for_agent"
        elif kind == "staff_accept":
            state["status"] = "in_review"
        elif kind == "staff_resolve":
            state["status"] = "resolved"
        elif kind == "confirm":
            if state["adjustment"] is not None:
                pass
            elif state["status"] == "awaiting_confirmation" and state["proposal"] is not None:
                state.update(status="resolved", adjustment=state["proposal"])
            else:
                label["http"] = 409
        elif kind == "close":
            if state["status"] == "awaiting_rider":
                state["status"] = "resolved"
            else:
                label["http"] = 409
        elif kind == "foreign_read":
            label["http"] = 404
        if kind not in (
            "fault_disconnect",
            "fault_reconnect",
            "probe",
            "replay_create",
            "foreign_read",
        ):
            label.update(
                status=state["status"],
                proposal_cents=state["proposal"],
                adjustment_cents=state["adjustment"],
                evidence_count=state["evidence"],
            )
        if kind == "probe":
            label.update(mcp_trip_id=world["trip_id"], mcp_settled=money["settled"])
        labels.append(label)
    return labels
