"""``/api/v2/copilot/*`` 路由：同步聚合 + SSE 流式。

两个端点共享 ``container.run_copilot.stream()`` —— 同步端点把全部事件 collect 成 list；
流式端点把每个事件转成一帧 SSE。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from api.v2.deps import make_require_owner
from api.v2.schemas import ChatEventOut, ChatRequest, ChatResponse
from api.v2.sse import stream_with_keepalive

if TYPE_CHECKING:
    from app.container import AppContainer


def build_copilot_routes(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/copilot", tags=["copilot"])
    require_owner = make_require_owner(container)

    @router.post(
        "/chat",
        response_model=ChatResponse,
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
            mode=body.mode,
        )
        collected: list[ChatEventOut] = []
        resolved_task_id = body.task_id or ""
        for ev in events_iter:
            collected.append(
                ChatEventOut(event_type=ev.event_type.value, payload=ev.payload)
            )
            if ev.event_type.value == "task_created":
                resolved_task_id = ev.payload.get("task_id", resolved_task_id)
        return ChatResponse(task_id=resolved_task_id, events=collected)

    @router.post(
        "/chat/stream",
        summary="Agent SSE 流式：思考/工具/答复逐事件推送",
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
            mode=body.mode,
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

    return router
