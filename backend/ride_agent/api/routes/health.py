"""Health endpoints; installed on an isolated application instance."""

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from ride_agent.api.security import principal_context
from ride_agent.ops.readiness import readiness


def install(app, runtime):

    @app.get("/runtime-config")
    def runtime_config():
        return dict(
            auth_mode="production" if runtime.security.production else "demo",
            model_mode=runtime.model_settings.mode,
            product="LLM-Agent-System",
        )

    @app.get("/api/session")
    def session():
        principal = principal_context.get()
        return dict(
            subject=principal.subject if principal else None,
            roles=sorted(principal.roles) if principal else ["rider", "specialist", "operator"],
            model_mode=runtime.model_settings.mode,
            auth_mode="production" if runtime.security.production else "demo",
        )

    @app.get("/livez")
    def liveness():
        return dict(status="ok")

    @app.get("/readyz")
    async def ready():

        data, status = await readiness(app)
        return JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store"})

    @app.get("/health")
    def health():
        if not app.state.started:
            raise HTTPException(503, "The application is starting or stopping.")
        if runtime.security.production:
            return dict(status="ok", auth_mode="production")
        return dict(
            status="ok",
            dataset_id=runtime.service.catalog.dataset_id,
            catalog_trips=len(runtime.service.catalog.trips),
            mode=runtime.service.engine.mode,
            model=runtime.service.engine.model,
            api_key_required=runtime.service.engine.mode == "live",
            pipeline="intent-orchestration-rag",
            retrieval="embedded-chroma-local-hash",
        )
