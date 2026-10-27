"""Private RiskPilot Agent runtime; no browser-facing business routes."""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.internal_v1 import build_internal_v1_router
from app.container import AppContainer
from app.logging_setup import configure_logging
from app.request_context import reset_request_id, set_request_id
from config import Settings, settings
from infra.auth.service_principal import ServicePrincipalMiddleware

configure_logging(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    log_file="logs/agent-runtime.log",
    json_logs=settings.json_logs_enabled,
    hash_salt=settings.observability_hash_salt,
)
logger = logging.getLogger(__name__)


def create_app(
    *,
    runtime_settings: Settings | None = None,
    container: AppContainer | None = None,
) -> FastAPI:
    selected = runtime_settings or settings
    owned_container = container or AppContainer(selected)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        selected.validate_runtime_configuration()
        logger.info("private Agent runtime starting")
        yield
        await asyncio.to_thread(owned_container.close)

    application = FastAPI(
        title="RiskPilot internal Agent runtime",
        version="1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.state.container = owned_container

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
        token = set_request_id(request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            reset_request_id(token)

    @application.exception_handler(Exception)
    async def fail_closed(request: Request, exc: Exception):
        request_id = request.headers.get("X-Request-ID", "unknown")
        logger.exception("internal runtime request failed: %s", type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={"error_code": "INTERNAL_RUNTIME_ERROR", "request_id": request_id},
        )

    application.include_router(
        build_internal_v1_router(owned_container.runtime, owned_container.documents),
        prefix="/internal/v1",
    )
    application.add_middleware(
        ServicePrincipalMiddleware,
        key_file=selected.internal_service_key_file,
    )
    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8766, reload=False)
