"""Strict internal-v1 routes backed by the frozen contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from domain.errors import RuntimeConflict
from domain.runtime import (
    DocumentProcess,
    DocumentResult,
    RunControlCommand,
    RunEvent,
    RunExecute,
    ServicePrincipal,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.document_processing import InternalDocumentService
    from app.internal_runtime import InternalRuntimeService


def _principal(request: Request) -> ServicePrincipal:
    principal = getattr(request.state, "service_principal", None)
    if not isinstance(principal, ServicePrincipal):
        raise HTTPException(status_code=401, detail="service identity required")
    return principal


def _ndjson(events: Iterator[RunEvent]) -> Iterator[str]:
    for event in events:
        yield event.model_dump_json() + "\n"


def build_internal_v1_router(
    runtime: InternalRuntimeService,
    documents: InternalDocumentService,
) -> APIRouter:
    router = APIRouter()

    @router.post("/runs/execute")
    def execute(body: RunExecute, request: Request) -> StreamingResponse:
        principal = _principal(request)
        if principal.sub != body.owner_id:
            raise HTTPException(status_code=403, detail="service subject does not own snapshot")
        try:
            events = runtime.execute(body)
        except RuntimeConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return StreamingResponse(_ndjson(events), media_type="application/x-ndjson")

    @router.post("/runs/{run_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
    def cancel(run_id: str, body: RunControlCommand, request: Request) -> Response:
        principal = _principal(request)
        if body.run_id != run_id:
            raise HTTPException(status_code=422, detail="path run_id must match body run_id")
        try:
            if runtime.owner_for(run_id, body.attempt_id) != principal.sub:
                raise HTTPException(status_code=403, detail="service subject does not own run")
            runtime.cancel(run_id, body.attempt_id, body.reason)
        except RuntimeConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/documents/process", response_model=DocumentResult)
    def process_document(body: DocumentProcess, request: Request) -> DocumentResult:
        principal = _principal(request)
        if principal.sub != body.actor_id:
            raise HTTPException(status_code=403, detail="service subject does not match actor_id")
        return documents.process(body)

    return router
