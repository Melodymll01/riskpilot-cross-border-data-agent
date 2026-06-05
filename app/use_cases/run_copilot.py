"""``RunCopilotUseCase``：API 入口的薄壳。

职责：
- 没有 task_id 就先 create_task
- 把 attachment 信息塞进 user_message（让 Agent 知道有可用文档）
- 委托 Agent 主循环流式产出 ``AgentEvent``

API 层（Step 010）直接迭代 yield 出来的事件序列化成 SSE。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from app.agent.events import AgentEvent

if TYPE_CHECKING:
    from app.agent.copilot import ComplianceCopilotAgent
    from app.use_cases.task_management import TaskManagementUseCase


class RunCopilotUseCase:
    def __init__(
        self,
        *,
        agent: ComplianceCopilotAgent,
        task_management: TaskManagementUseCase,
    ) -> None:
        self._agent = agent
        self._task_uc = task_management

    def stream(
        self,
        *,
        owner_id: str,
        task_id: str | None,
        user_message: str,
        attachment_doc_ids: list[str] | None = None,
    ) -> Iterator[AgentEvent]:
        if not owner_id:
            msg = "owner_id 必填"
            raise ValueError(msg)
        if not user_message:
            msg = "user_message 不能为空"
            raise ValueError(msg)

        # 1) 没 task_id 则新建任务（标题取消息前 30 字符作占位）
        if task_id is None:
            title = (user_message[:30] + "…") if len(user_message) > 30 else user_message
            task = self._task_uc.create_task(
                owner_id, title=title, user_goal=user_message
            )
            task_id = task.task_id
            yield AgentEvent.task_created(task_id)

        # 2) 附件信息进 user_message
        effective_message = user_message
        if attachment_doc_ids:
            ids = ", ".join(attachment_doc_ids)
            effective_message = f"{user_message}\n\n[已上传文档 ID: {ids}]"

        # 3) 跑 Agent
        yield from self._agent.run(
            owner_id=owner_id,
            task_id=task_id,
            user_message=effective_message,
        )
