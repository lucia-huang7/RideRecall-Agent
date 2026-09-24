"""Assemble the ride-support API: uvicorn ride_agent.api.app:app --port 8010."""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ride_agent.api import middleware
from ride_agent.api.context import RuntimeContext
from ride_agent.api.limits import RequestBodyLimit
from ride_agent.api.routes import cases, health, operations, staff
from ride_agent.api.security import Security
from ride_agent.domain.catalog import RideCatalog
from ride_agent.integrations.settings import ModelSettings


def create_app(db_path=None, catalog_path=None, *, security=None):
    security = security or Security()
    catalog = RideCatalog(catalog_path or os.getenv("LLM_AGENT_SYSTEM_RIDE_CATALOG_PATH"))
    security.validate_catalog(catalog)
    database_path = db_path or os.getenv("LLM_AGENT_SYSTEM_RIDE_DB")
    if security.production and (not database_path):
        raise ValueError("Production requires an explicit LLM_AGENT_SYSTEM_RIDE_DB")
    database_path = database_path or str(Path(__file__).resolve().parents[3] / ".runtime/rides.sqlite3")
    runtime = RuntimeContext(security, catalog, ModelSettings.from_environment(), database_path)
    app = FastAPI(
        title="LLM-Agent-System Ride Support",
        version="0.5.0",
        lifespan=runtime.lifespan,
        docs_url=None if security.production else "/docs",
        redoc_url=None if security.production else "/redoc",
        openapi_url=None if security.production else "/openapi.json",
    )
    app.add_middleware(RequestBodyLimit)
    app.state.observation = runtime.observation
    app.state.security = security
    app.state.started = False
    for name in ("store", "service", "projections", "configuration"):
        setattr(app.state, name, None)
    middleware.install(app, runtime)
    for group in (cases, staff, operations, health):
        group.install(app, runtime)
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app


app = create_app()
