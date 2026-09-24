"""Request authentication, boundary validation, error handling and observation."""

import asyncio
import sqlite3
import time
from uuid import uuid4

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ride_agent.api.security import principal_context


def install(app, runtime):

    @app.exception_handler(sqlite3.DatabaseError)
    async def unavailable_database(request, error):
        request.state.diagnostic_error = error
        return JSONResponse(
            {"detail": ("Persistence is unavailable. Retry the same request ID after recovery.")},
            status_code=503,
            headers={"Retry-After": "2"},
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        issues = [
            dict(location=list(item["loc"]), message=item["msg"], type=item["type"]) for item in error.errors()[:20]
        ]
        return JSONResponse(
            {"detail": "Please check the request fields.", "errors": issues},
            status_code=422,
        )

    @app.middleware("http")
    async def local_api_prefix(request, call_next):
        began = time.monotonic()
        correlation_id = uuid4().hex
        context_token = None
        response = None
        failure = None
        cancelled = False
        request.state.correlation_id = correlation_id
        runtime.observation.active.inc()
        try:
            if request.scope["path"].startswith("/ride-api/"):
                request.scope["path"] = request.scope["path"][len("/ride-api") :]
                request.scope["raw_path"] = request.scope["path"].encode()
            try:
                if runtime.security.production and request.scope["path"].startswith("/api/"):
                    principal = runtime.security.authenticate(request.headers.get("authorization", ""))
                    runtime.security.authorize(request.scope["path"], request.method, principal)
                    context_token = principal_context.set(principal)
                elif not runtime.security.production:
                    runtime.security.check_demo_peer(request.client.host if request.client else "")
                    if len(request.headers.getlist("host")) != 1 or len(request.headers.getlist("origin")) > 1:
                        raise HTTPException(403, "Demo requests require a loopback host and origin.")
                    runtime.security.check_demo_authority(
                        request.headers.get("host", ""),
                        request.headers.get("origin"),
                        test_client=bool(request.client and request.client.host == "testclient"),
                    )
                response = await call_next(request)
            except HTTPException as error:
                response = JSONResponse(
                    {"detail": error.detail},
                    status_code=error.status_code,
                    headers=error.headers,
                )
            except asyncio.CancelledError:
                cancelled = True
                raise
            except Exception as error:
                failure = error
                response = JSONResponse(
                    {
                        "detail": (
                            "An unexpected service error occurred. Retry safely or contact the workspace operator."
                        )
                    },
                    status_code=500,
                )
            response.headers["X-Correlation-ID"] = correlation_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
            if runtime.security.production:
                response.headers["Content-Security-Policy"] = (
                    "default-src 'self'; script-src 'self'; style-src 'self' "
                    "'unsafe-inline'; img-src 'self' data: blob:; connect-src "
                    "'self'; font-src 'self'; frame-ancestors 'none'; base-uri "
                    "'self'; form-action "
                    "'self'"
                )
            if request.scope["path"].startswith("/api/"):
                response.headers["Cache-Control"] = "no-store"
            return response
        finally:
            if context_token is not None:
                principal_context.reset(context_token)
            route = getattr(request.scope.get("route"), "path", None)
            status = response.status_code if response is not None else 499 if cancelled else 500
            runtime.observation.finish(
                correlation_id,
                request.method,
                route,
                status,
                time.monotonic() - began,
                failure or getattr(request.state, "diagnostic_error", None),
            )
