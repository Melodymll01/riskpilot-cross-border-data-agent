"""内存版 UserRepo / TaskRepo Fake，用于单测。"""

from __future__ import annotations

import time

from domain.models import Artifact, Message, Task, ToolCall, User


class InMemoryUserRepo:
    """`UserRepoPort` 的内存实现。"""

    def __init__(self) -> None:
        self._users: dict[str, User] = {}

    def upsert(self, user: User) -> None:
        self._users[user.user_id] = user

    def get(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    def merge_owner(self, from_id: str, to_id: str) -> int:
        # 内存版无 task 表，配套 InMemoryTaskRepo 才有意义
        return 0

    def touch(self, user_id: str) -> None:
        u = self._users.get(user_id)
        if u is None:
            return
        self._users[user_id] = u.model_copy(update={"last_active_at": time.time()})


class InMemoryTaskRepo:
    """`TaskRepoPort` 的内存实现。"""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._messages: dict[str, list[Message]] = {}
        self._tool_calls: dict[str, ToolCall] = {}
        self._artifacts: list[Artifact] = []

    def create(self, task: Task) -> None:
        self._tasks[task.task_id] = task
        self._messages.setdefault(task.task_id, [])

    def get(self, task_id: str, owner_id: str) -> Task | None:
        t = self._tasks.get(task_id)
        if t is None or t.owner_id != owner_id:
            return None
        return t

    def list_for_owner(self, owner_id: str, limit: int = 50) -> list[Task]:
        items = [t for t in self._tasks.values() if t.owner_id == owner_id]
        items.sort(key=lambda x: x.updated_at, reverse=True)
        return items[:limit]

    def update(self, task: Task) -> None:
        if task.task_id in self._tasks:
            self._tasks[task.task_id] = task

    def delete(self, task_id: str, owner_id: str) -> bool:
        t = self._tasks.get(task_id)
        if t is None or t.owner_id != owner_id:
            return False
        del self._tasks[task_id]
        self._messages.pop(task_id, None)
        return True

    def append_message(self, msg: Message) -> None:
        self._messages.setdefault(msg.task_id, []).append(msg)
        t = self._tasks.get(msg.task_id)
        if t is not None:
            self._tasks[msg.task_id] = t.model_copy(
                update={"updated_at": msg.created_at}
            )

    def list_messages(self, task_id: str) -> list[Message]:
        return list(self._messages.get(task_id, []))

    def append_tool_call(self, call: ToolCall) -> None:
        self._tool_calls[call.tool_call_id] = call

    def append_artifact(self, art: Artifact) -> None:
        self._artifacts.append(art)
