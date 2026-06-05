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


def _make_uc_with_risk_profile(
    risk_profile: object,
) -> tuple[RunCopilotUseCase, InMemoryTaskRepo]:
    """对 profile 模式专用：装一个 risk_profile 进 use case，agent 用 FakeChat 不会被触发。"""
    chat = FakeChat(responses=[_FINAL])
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
    uc = RunCopilotUseCase(
        agent=agent, task_management=task_uc, risk_profile=risk_profile  # type: ignore[arg-type]
    )
    return uc, repo


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


class TestProfileMode:
    """``mode='profile'`` 走 RiskProfilePort，不进 Agent。"""

    def test_profile_mode_with_stub_emits_not_ready_answer(self) -> None:
        from infra.risk_profile import StubRiskProfileService

        uc, repo = _make_uc_with_risk_profile(StubRiskProfileService(mode="raise"))
        events = list(
            uc.stream(
                owner_id="anon:x",
                task_id=None,
                user_message="跨境电商日均向香港传 5 万条订单数据",
                mode="profile",
            )
        )
        # 第一帧 task_created
        assert events[0].event_type is AgentEventType.TASK_CREATED
        # 第二帧 answer，文本带"尚未上线"
        assert events[1].event_type is AgentEventType.ANSWER
        text = events[1].payload["text"]
        assert "尚未上线" in text or "未上线" in text
        assert "schema-evidence" in text
        # task.mode 持久化为 profile
        task = repo.get(events[0].payload["task_id"], "anon:x")
        assert task is not None and task.mode == "profile"
        # agent 没被触发：FakeChat 没有调用记录
        # （没法直接断言；通过事件序列里没有 thought/tool_call 反推）
        kinds = [e.event_type for e in events]
        assert AgentEventType.THOUGHT not in kinds
        assert AgentEventType.TOOL_CALL not in kinds

    def test_profile_mode_with_placeholder_renders_markdown(self) -> None:
        from infra.risk_profile import StubRiskProfileService

        uc, _ = _make_uc_with_risk_profile(StubRiskProfileService(mode="placeholder"))
        events = list(
            uc.stream(
                owner_id="anon:x",
                task_id=None,
                user_message="临床数据出境到德国总部是否需要安全评估",
                mode="profile",
            )
        )
        answer_text = events[1].payload["text"]
        assert "## 风险画像评估" in answer_text
        assert "**目标命题**" in answer_text
        assert "临床数据出境到德国总部是否需要安全评估" in answer_text
        # not_disclosed 占位
        assert "not_disclosed" in answer_text

    def test_profile_mode_without_risk_profile_falls_back(self) -> None:
        """容器没装 risk_profile（不应该出现，但兜底友好提示）。"""
        uc, _ = _make_uc_with_risk_profile(None)
        events = list(
            uc.stream(
                owner_id="anon:x",
                task_id=None,
                user_message="x" * 5,
                mode="profile",
            )
        )
        text = events[1].payload["text"]
        assert "未在容器中装配" in text or "未装配" in text

    def test_qa_mode_unchanged_does_not_call_risk_profile(self) -> None:
        """qa 模式下 risk_profile 不应被调用（即使装了也不调）。"""

        class _ExplodingRiskProfile:
            def assess(self, target, document=None, *, language="zh"):  # type: ignore[no-untyped-def]
                raise AssertionError("qa 模式不应调用 risk_profile.assess")

        uc, _ = _make_uc_with_risk_profile(_ExplodingRiskProfile())
        # qa 模式正常走 agent
        events = list(
            uc.stream(
                owner_id="anon:x",
                task_id=None,
                user_message="个人信息出境路径有哪些",
                mode="qa",
            )
        )
        # 至少有 answer
        assert any(e.event_type is AgentEventType.ANSWER for e in events)
