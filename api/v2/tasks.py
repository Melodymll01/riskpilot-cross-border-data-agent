"""``/api/v2/tasks/*`` 路由：列表 / 详情 / 修改 / 删除。

owner_id 来自 session cookie（``require_owner`` 依赖），所有查询/写入都按 owner 隔离。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, status

from api.v2.deps import make_require_owner
from api.v2.schemas import (
    MessageOut,
    OkResponse,
    TaskCitationOut,
    TaskDetailResponse,
    TaskListResponse,
    TaskOut,
    UpdateTaskRequest,
)

if TYPE_CHECKING:
    from app.container import AppContainer
    from domain.models import Message, Task


def _to_task_out(task: Task) -> TaskOut:
    return TaskOut(
        task_id=task.task_id,
        owner_id=task.owner_id,
        title=task.title,
        state=task.state,
        user_goal=task.user_goal,
        collected_facts=dict(task.collected_facts),
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _to_message_out(msg: Message) -> MessageOut:
    return MessageOut(
        msg_id=msg.msg_id,
        role=msg.role,
        content=msg.content,
        citations=[
            TaskCitationOut(
                source_type=c.source_type,
                source_name=c.source_name,
                title=c.title,
                source_url=c.source_url,
                text_snippet=c.text_snippet,
            )
            for c in msg.citations
        ],
        created_at=msg.created_at,
    )


def build_task_routes(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/tasks", tags=["tasks"])
    require_owner = make_require_owner(container)

    @router.get("", response_model=TaskListResponse, summary="当前 owner 的任务列表")
    def list_tasks(
        owner_id: str = Depends(require_owner),
        limit: int = 50,
    ) -> TaskListResponse:
        limit = max(1, min(limit, 200))
        tasks = container.task_management.list_tasks(owner_id, limit=limit)
        return TaskListResponse(tasks=[_to_task_out(t) for t in tasks])

    @router.get(
        "/{task_id}",
        response_model=TaskDetailResponse,
        summary="任务详情：基本信息 + 完整消息历史",
    )
    def get_task(
        task_id: str,
        owner_id: str = Depends(require_owner),
    ) -> TaskDetailResponse:
        task = container.task_management.get_task(task_id, owner_id)
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error_code": "TASK_NOT_FOUND", "message": f"task {task_id!r} not found"},
            )
        messages = container.task_management.list_messages(task_id, owner_id)
        return TaskDetailResponse(
            task=_to_task_out(task),
            messages=[_to_message_out(m) for m in messages],
        )

    @router.patch(
        "/{task_id}",
        response_model=TaskOut,
        summary="修改任务标题或追加 collected_facts（浅 merge）",
    )
    def update_task(
        task_id: str,
        body: UpdateTaskRequest,
        owner_id: str = Depends(require_owner),
    ) -> TaskOut:
        task = container.task_management.get_task(task_id, owner_id)
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error_code": "TASK_NOT_FOUND", "message": f"task {task_id!r} not found"},
            )

        # collected_facts 走 use case（带 owner 校验 + 时间戳）
        if body.collected_facts:
            updated = container.task_management.update_facts(
                task_id, owner_id, body.collected_facts
            )
            task = updated if updated is not None else task

        # title 改动直接走 repo.update（use case 暂未暴露专用方法）
        if body.title is not None and body.title != task.title:
            import time

            task = task.model_copy(update={"title": body.title, "updated_at": time.time()})
            container.task_repo.update(task)

        return _to_task_out(task)

    @router.delete(
        "/{task_id}",
        response_model=OkResponse,
        summary="删除任务（连带消息）",
    )
    def delete_task(
        task_id: str,
        owner_id: str = Depends(require_owner),
    ) -> OkResponse:
        ok = container.task_management.delete_task(task_id, owner_id)
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error_code": "TASK_NOT_FOUND", "message": f"task {task_id!r} not found"},
            )
        return OkResponse()

    return router
