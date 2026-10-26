"""``/api/v2/copilot/*`` 路由：同步聚合 + SSE 流式。

两个端点共享 ``container.run_copilot.stream()`` —— 同步端点把全部事件 collect 成 list；
流式端点把每个事件转成一帧 SSE。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from api.v2.deps import make_require_owner
from api.v2.schemas import ChatEventOut, ChatRequest, ChatResponse, ConversationRunOut
from api.v2.sse import stream_with_keepalive

if TYPE_CHECKING:
    from api.v2.ratelimit import RateLimiter
    from app.container import AppContainer


def build_copilot_routes(
    container: AppContainer, *, limiter: RateLimiter | None = None
) -> APIRouter:
    router = APIRouter(prefix="/copilot", tags=["copilot"])
    require_owner = make_require_owner(container)
    llm_deps = (
        [Depends(limiter.dependency(container.settings.rate_limit_llm))]
        if limiter is not None
        else []
    )

    @router.post(
        "/chat",
        response_model=ChatResponse,
        dependencies=llm_deps,
        summary="同步聚合 Agent 一轮对话（适合不需要流式 UI 的场景）",
    )
    def chat_sync(
        body: ChatRequest,
        owner_id: str = Depends(require_owner),
    ) -> ChatResponse:
        events_iter = container.run_copilot.stream(
            owner_id=owner_id,
            task_id=body.task_id,
            user_message=body.message,
            attachment_doc_ids=body.attachment_doc_ids,
        )
        collected: list[ChatEventOut] = []
        resolved_task_id = body.task_id or ""
        resolved_run_id = ""
        for ev in events_iter:
            collected.append(ChatEventOut(event_type=ev.event_type.value, payload=ev.payload))
            if ev.event_type.value == "task_created":
                resolved_task_id = ev.payload.get("task_id", resolved_task_id)
            elif ev.event_type.value == "run_started":
                resolved_run_id = ev.payload.get("run_id", resolved_run_id)
        return ChatResponse(task_id=resolved_task_id, run_id=resolved_run_id, events=collected)

    @router.post(
        "/chat/stream",
        summary="Agent SSE 流式：思考/工具/答复逐事件推送",
        dependencies=llm_deps,
        responses={
            200: {
                "description": "text/event-stream，每个 AgentEvent 一帧",
                "content": {"text/event-stream": {}},
            }
        },
    )
    def chat_stream(
        body: ChatRequest,
        owner_id: str = Depends(require_owner),
    ) -> StreamingResponse:
        events_iter = container.run_copilot.stream(
            owner_id=owner_id,
            task_id=body.task_id,
            user_message=body.message,
            attachment_doc_ids=body.attachment_doc_ids,
        )
        async_stream = stream_with_keepalive(
            events_iter,
            keepalive_seconds=container.settings.sse_keepalive_seconds,
        )
        return StreamingResponse(
            async_stream,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # nginx 不缓冲，立即转发
            },
        )

    @router.get("/runs/{run_id}", response_model=ConversationRunOut)
    def get_run(
        run_id: str,
        owner_id: str = Depends(require_owner),
    ) -> ConversationRunOut:
        run = container.run_copilot.get_run(owner_id=owner_id, run_id=run_id)
        if run is None:
            from fastapi import HTTPException, status

            raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
        return ConversationRunOut(
            run_id=run.run_id,
            task_id=run.task_id,
            status=run.status,
            stage=run.stage,
            attachment_doc_ids=run.attachment_doc_ids,
            evidence_count=len(run.evidence),
            evidence_ids=[str(item.get("citation_id") or "") for item in run.evidence],
            verified_citation_ids=run.verified_citation_ids,
            verified_context_message_ids=run.verified_context_message_ids,
            clarification_fields=run.clarification_fields,
            max_duration_seconds=run.max_duration_seconds,
            remaining_seconds=_remaining_seconds(run),
            max_model_calls=run.max_model_calls,
            max_tool_calls=run.max_tool_calls,
            model_call_count=run.model_call_count,
            tool_call_count=run.tool_call_count,
            model_retry_count=run.model_retry_count,
            tool_retry_count=run.tool_retry_count,
            input_tokens=run.input_tokens,
            output_tokens=run.output_tokens,
            usage_status=_usage_status(run.model_call_count, run.usage_reported_model_calls),
            answer_message_id=run.answer_message_id,
            error_type=run.error_type,
            end_reason=run.end_reason,
            cancel_requested_at=run.cancel_requested_at,
            finished_at=run.finished_at,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    @router.post("/runs/{run_id}/cancel", response_model=ConversationRunOut)
    def cancel_run(
        run_id: str,
        owner_id: str = Depends(require_owner),
    ) -> ConversationRunOut:
        run = container.run_copilot.cancel(owner_id=owner_id, run_id=run_id)
        return ConversationRunOut(
            run_id=run.run_id,
            task_id=run.task_id,
            status=run.status,
            stage=run.stage,
            attachment_doc_ids=run.attachment_doc_ids,
            evidence_count=len(run.evidence),
            evidence_ids=[str(item.get("citation_id") or "") for item in run.evidence],
            verified_citation_ids=run.verified_citation_ids,
            verified_context_message_ids=run.verified_context_message_ids,
            clarification_fields=run.clarification_fields,
            max_duration_seconds=run.max_duration_seconds,
            remaining_seconds=_remaining_seconds(run),
            max_model_calls=run.max_model_calls,
            max_tool_calls=run.max_tool_calls,
            model_call_count=run.model_call_count,
            tool_call_count=run.tool_call_count,
            model_retry_count=run.model_retry_count,
            tool_retry_count=run.tool_retry_count,
            input_tokens=run.input_tokens,
            output_tokens=run.output_tokens,
            usage_status=_usage_status(run.model_call_count, run.usage_reported_model_calls),
            answer_message_id=run.answer_message_id,
            error_type=run.error_type,
            end_reason=run.end_reason,
            cancel_requested_at=run.cancel_requested_at,
            finished_at=run.finished_at,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    @router.post("/runs/{run_id}/resume")
    def resume_run(
        run_id: str,
        owner_id: str = Depends(require_owner),
    ) -> StreamingResponse:
        events_iter = container.run_copilot.resume(owner_id=owner_id, run_id=run_id)
        return StreamingResponse(
            stream_with_keepalive(
                events_iter,
                keepalive_seconds=container.settings.sse_keepalive_seconds,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router


def _usage_status(model_calls: int, reported_calls: int) -> str:
    if reported_calls == 0:
        return "unknown"
    return "complete" if reported_calls == model_calls else "partial"


def _remaining_seconds(run) -> float:
    if run.deadline_at is None:
        return run.remaining_seconds
    return max(0.0, min(run.remaining_seconds, run.deadline_at - time.time()))
