"""``RunCopilotUseCase`` 测试：task 自动创建 + 附件信息注入。"""

from __future__ import annotations

import json

from app.agent.copilot import ComplianceCopilotAgent
from app.agent.events import AgentEventType
from app.agent.tools import register_default_tools
from app.use_cases.run_copilot import RunCopilotUseCase
from app.use_cases.task_management import TaskManagementUseCase
from tests.fakes.fake_chat import FakeChat
from tests.fakes.fake_evidence import FakeEvidence
from tests.fakes.fake_repos import InMemoryTaskRepo
from tests.fakes.fake_retrieve import FakeRetrieve
from tests.fakes.fake_websearch import FakeWebSearch


def _make_uc(responses: list[str]) -> tuple[RunCopilotUseCase, FakeChat, InMemoryTaskRepo]:
    chat = FakeChat(responses=responses)
    repo = InMemoryTaskRepo()
    from types import SimpleNamespace

    container = SimpleNamespace(
        retriever=FakeRetrieve(),
        web_search=FakeWebSearch(),
        evidence=FakeEvidence(),
    )
    registry = register_default_tools(container)  # type: ignore[arg-type]
    agent = ComplianceCopilotAgent(
        chat=chat, task_repo=repo, tool_registry=registry, max_steps=3
    )
    task_uc = TaskManagementUseCase(repo)
    uc = RunCopilotUseCase(agent=agent, task_management=task_uc)
    return uc, chat, repo


_FINAL = json.dumps({"thought": "", "action": "final_answer", "answer": "done"})


class TestNewTaskCreation:
    def test_creates_task_when_id_none(self) -> None:
        uc, _, repo = _make_uc(responses=[_FINAL])
        events = list(
            uc.stream(owner_id="anon:x", task_id=None, user_message="hello world")
        )

        # 第一个事件是 task_created
        assert events[0].event_type is AgentEventType.TASK_CREATED
        new_id = events[0].payload["task_id"]
        assert new_id.startswith("task_")

        # 任务真的写入 repo 了
        task = repo.get(new_id, "anon:x")
        assert task is not None
        assert task.user_goal == "hello world"
        assert task.title == "hello world"  # 短消息直接当标题

    def test_long_message_truncates_title(self) -> None:
        uc, _, repo = _make_uc(responses=[_FINAL])
        long_msg = "我们公司要把欧洲用户数据同步回北京数据中心" * 5
        events = list(
            uc.stream(owner_id="anon:x", task_id=None, user_message=long_msg)
        )
        task_id = events[0].payload["task_id"]
        task = repo.get(task_id, "anon:x")
        assert task is not None
        # 标题截断到 30 字符 + "…"
        assert len(task.title) <= 31
        assert task.title.endswith("…")

    def test_no_task_created_event_when_task_id_given(self) -> None:
        uc, _, repo = _make_uc(responses=[_FINAL])
        # 先建一个 task
        task_uc = TaskManagementUseCase(repo)
        task = task_uc.create_task("anon:x", title="existing")

        events = list(
            uc.stream(owner_id="anon:x", task_id=task.task_id, user_message="q")
        )
        assert all(e.event_type is not AgentEventType.TASK_CREATED for e in events)


class TestAttachmentInjection:
    def test_attachment_ids_appended_to_user_message(self) -> None:
        uc, chat, _ = _make_uc(responses=[_FINAL])
        list(
            uc.stream(
                owner_id="anon:x",
                task_id=None,
                user_message="请评估隐私政策",
                attachment_doc_ids=["DOC-1", "DOC-2"],
            )
        )
        # LLM 收到的 user message 应当包含附件 ID
        user_msg = chat.calls[0]["messages"][1]["content"]
        assert "请评估隐私政策" in user_msg
        assert "DOC-1" in user_msg
        assert "DOC-2" in user_msg
        assert "已上传文档" in user_msg

    def test_no_attachment_message_unchanged(self) -> None:
        uc, chat, _ = _make_uc(responses=[_FINAL])
        list(uc.stream(owner_id="anon:x", task_id=None, user_message="问题"))
        user_msg = chat.calls[0]["messages"][1]["content"]
        assert user_msg == "问题"


class TestValidation:
    def test_owner_required(self) -> None:
        uc, _, _ = _make_uc(responses=[_FINAL])
        import pytest

        with pytest.raises(ValueError, match="owner_id"):
            list(uc.stream(owner_id="", task_id=None, user_message="q"))

    def test_user_message_required(self) -> None:
        uc, _, _ = _make_uc(responses=[_FINAL])
        import pytest

        with pytest.raises(ValueError, match="user_message"):
            list(uc.stream(owner_id="anon:x", task_id=None, user_message=""))
