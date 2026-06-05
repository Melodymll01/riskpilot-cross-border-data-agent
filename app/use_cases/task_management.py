"""TaskManagementUseCase：包装 ``TaskRepoPort``，强制 owner_id 隔离。

API 层只允许通过本 use case 操作 Task，所有方法均必传 owner_id：
- create_task: 生成 task_id + 默认 state="planning"
- list_tasks / get_task / delete_task / update_facts: 严格按 owner_id 过滤
- append_user_message / append_assistant_message: 简化消息追加
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Any

from domain.models import Citation, Message, Task

if TYPE_CHECKING:
    from domain.ports import TaskRepoPort


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class TaskManagementUseCase:
    def __init__(self, task_repo: TaskRepoPort) -> None:
        self._repo = task_repo

    def create_task(
        self,
        owner_id: str,
        *,
        title: str = "",
        user_goal: str = "",
    ) -> Task:
        if not owner_id:
            msg = "owner_id 必填"
            raise ValueError(msg)
        now = time.time()
        task = Task(
            task_id=_new_id("task"),
            owner_id=owner_id,
            title=title,
            state="planning",
            user_goal=user_goal,
            collected_facts={},
            created_at=now,
            updated_at=now,
        )
        self._repo.create(task)
        return task

    def list_tasks(self, owner_id: str, *, limit: int = 50) -> list[Task]:
        return self._repo.list_for_owner(owner_id, limit=limit)

    def get_task(self, task_id: str, owner_id: str) -> Task | None:
        return self._repo.get(task_id, owner_id)

    def delete_task(self, task_id: str, owner_id: str) -> bool:
        return self._repo.delete(task_id, owner_id)

    def update_facts(
        self, task_id: str, owner_id: str, facts: dict[str, Any]
    ) -> Task | None:
        """合并 facts 到 task.collected_facts（浅 merge）。

        返回更新后的 Task；找不到（或不属于 owner）返回 None。
        """
        task = self._repo.get(task_id, owner_id)
        if task is None:
            return None
        merged = {**task.collected_facts, **facts}
        updated = task.model_copy(
            update={"collected_facts": merged, "updated_at": time.time()}
        )
        self._repo.update(updated)
        return updated

    def append_user_message(self, task_id: str, owner_id: str, content: str) -> Message:
        self._ensure_owner(task_id, owner_id)
        msg = Message(
            msg_id=_new_id("msg"),
            task_id=task_id,
            role="user",
            content=content,
        )
        self._repo.append_message(msg)
        return msg

    def append_assistant_message(
        self,
        task_id: str,
        owner_id: str,
        content: str,
        *,
        citations: list[Citation] | None = None,
    ) -> Message:
        self._ensure_owner(task_id, owner_id)
        msg = Message(
            msg_id=_new_id("msg"),
            task_id=task_id,
            role="assistant",
            content=content,
            citations=citations or [],
        )
        self._repo.append_message(msg)
        return msg

    def list_messages(self, task_id: str, owner_id: str) -> list[Message]:
        self._ensure_owner(task_id, owner_id)
        return self._repo.list_messages(task_id)

    def _ensure_owner(self, task_id: str, owner_id: str) -> None:
        if self._repo.get(task_id, owner_id) is None:
            msg = f"task {task_id!r} not found for owner {owner_id!r}"
            raise PermissionError(msg)
