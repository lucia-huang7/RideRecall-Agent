"""Staff endpoints; installed on an isolated application instance."""

from fastapi import Header, HTTPException, Query

from ride_agent.api.schemas import StaffAction
from ride_agent.api.security import principal_context, staff_receipt_owner


def install(app, runtime):

    @app.get("/api/agent/cases")
    def queue(
        x_demo_agent: str = Header(default=""),
        limit: int = Query(default=25, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
    ):
        runtime.staff(x_demo_agent)
        return runtime.list_response(
            runtime.store.page(handed_off=True, limit=limit, cursor=cursor, full=True),
            "/api/agent/cases",
        )

    @app.get("/api/agent/cases/index")
    def queue_index(
        x_demo_agent: str = Header(default=""),
        limit: int = Query(default=25, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
    ):
        runtime.staff(x_demo_agent)
        return runtime.store.page(handed_off=True, limit=limit, cursor=cursor)

    @app.get("/api/agent/cases/{case_id}")
    def specialist_case(case_id: str, x_demo_agent: str = Header(default="")):
        runtime.staff(x_demo_agent)
        record = runtime.store.get(case_id)
        if not record.get("handoff"):
            raise HTTPException(404, "Case not found.")
        principal = principal_context.get()
        return dict(
            case=runtime.public(record),
            available_actions=runtime.service.staff_options(
                record, principal.subject if runtime.security.production else None
            ),
        )

    @app.post("/api/agent/cases/{case_id}/actions")
    def staff_action(
        case_id: str,
        body: StaffAction,
        x_demo_agent: str = Header(default=""),
        x_request_id: str = Header(default=""),
        if_match: str = Header(default=""),
    ):
        runtime.staff(x_demo_agent)

        def apply(db):
            record = runtime.store.load(db, case_id)
            if runtime.security.production and (not record["handoff"]):
                raise HTTPException(404, "Case not found.")
            runtime.check_revision(record, if_match)
            runtime.service.staff(
                record,
                body.action,
                body.text.strip(),
                staff_id=principal_context.get().subject if runtime.security.production else None,
            )
            if runtime.security.production and record["messages"] and (record["messages"][-1]["role"] == "agent"):
                record["messages"][-1]["staff_subject"] = principal_context.get().subject
            runtime.store.save(db, record)
            return runtime.public(record)

        return runtime.case_response(
            runtime.write(staff_receipt_owner(), x_request_id, [case_id, body.model_dump()], apply)
        )
