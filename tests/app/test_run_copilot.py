"""``RunCopilotUseCase`` 应用编排测试。"""

from __future__ import annotations

from app.use_cases.run_copilot import RunCopilotUseCase
from app.use_cases.task_management import TaskManagementUseCase
from domain.agent import AgentEventType
from tests.fakes.fake_copilot_agent import FakeCopilotAgent
from tests.fakes.fake_repos import InMemoryTaskRepo


def _make_uc() -> tuple[RunCopilotUseCase, FakeCopilotAgent, InMemoryTaskRepo]:
    repo = InMemoryTaskRepo()
    agent = FakeCopilotAgent()
    return (
        RunCopilotUseCase(
            agent=agent,
            task_management=TaskManagementUseCase(repo),
        ),
        agent,
        repo,
    )


def _make_uc_with_risk_profile(
    risk_profile: object,
) -> tuple[RunCopilotUseCase, InMemoryTaskRepo]:
    repo = InMemoryTaskRepo()
    return (
        RunCopilotUseCase(
            agent=FakeCopilotAgent(),
            task_management=TaskManagementUseCase(repo),
            risk_profile=risk_profile,  # type: ignore[arg-type]
        ),
        repo,
    )


class TestNewTaskCreation:
    def test_creates_task_when_id_none(self) -> None:
        uc, _, repo = _make_uc()
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
        uc, _, repo = _make_uc()
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
        uc, _, repo = _make_uc()
        # 先建一个 task
        task_uc = TaskManagementUseCase(repo)
        task = task_uc.create_task("anon:x", title="existing")

        events = list(
            uc.stream(owner_id="anon:x", task_id=task.task_id, user_message="q")
        )
        assert all(e.event_type is not AgentEventType.TASK_CREATED for e in events)


class TestAttachmentInjection:
    def test_attachment_ids_appended_to_user_message(self) -> None:
        uc, agent, _ = _make_uc()
        list(
            uc.stream(
                owner_id="anon:x",
                task_id=None,
                user_message="请评估隐私政策",
                attachment_doc_ids=["DOC-1", "DOC-2"],
            )
        )
        # LLM 收到的 user message 应当包含附件 ID
        user_msg = agent.calls[0]["user_message"]
        assert "请评估隐私政策" in user_msg
        assert "DOC-1" in user_msg
        assert "DOC-2" in user_msg
        assert "已上传文档" in user_msg

    def test_no_attachment_message_unchanged(self) -> None:
        uc, agent, _ = _make_uc()
        list(uc.stream(owner_id="anon:x", task_id=None, user_message="问题"))
        user_msg = agent.calls[0]["user_message"]
        assert user_msg == "问题"


class TestValidation:
    def test_owner_required(self) -> None:
        uc, _, _ = _make_uc()
        import pytest

        with pytest.raises(ValueError, match="owner_id"):
            list(uc.stream(owner_id="", task_id=None, user_message="q"))

    def test_user_message_required(self) -> None:
        uc, _, _ = _make_uc()
        import pytest

        with pytest.raises(ValueError, match="user_message"):
            list(uc.stream(owner_id="anon:x", task_id=None, user_message=""))


class TestProfileMode:
    """``mode='profile'`` 走 RiskProfilePort，不进 Agent。"""

    def test_profile_mode_with_unconfigured_client_emits_not_ready_answer(self) -> None:
        from infra.risk_profile import HttpRiskProfileClient

        uc, repo = _make_uc_with_risk_profile(HttpRiskProfileClient(base_url=None))
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
        # 第二帧 answer，文本带明确配置提示
        assert events[1].event_type is AgentEventType.ANSWER
        text = events[1].payload["text"]
        assert "当前不可用" in text
        assert "RISK_PROFILE_API_BASE" in text
        # task.mode 持久化为 profile
        task = repo.get(events[0].payload["task_id"], "anon:x")
        assert task is not None and task.mode == "profile"
        # agent 没被触发：FakeChat 没有调用记录
        # （没法直接断言；通过事件序列里没有 thought/tool_call 反推）
        kinds = [e.event_type for e in events]
        assert AgentEventType.THOUGHT not in kinds
        assert AgentEventType.TOOL_CALL not in kinds

    def test_profile_mode_with_model_result_renders_markdown(self) -> None:
        from tests.fakes import FakeRiskProfile

        uc, _ = _make_uc_with_risk_profile(FakeRiskProfile())
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
        assert "supported" in answer_text

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


def _make_uc_with_research(
    research: object,
) -> tuple[RunCopilotUseCase, InMemoryTaskRepo]:
    repo = InMemoryTaskRepo()
    task_uc = TaskManagementUseCase(repo)
    uc = RunCopilotUseCase(
        agent=FakeCopilotAgent(),
        task_management=task_uc,
        research=research,  # type: ignore[arg-type]
    )
    return uc, repo


class TestResearchMode:
    """``mode='research'`` 走 ResearchPort，不进 ReAct Agent。"""

    def test_research_mode_emits_thoughts_and_answer(self) -> None:
        from tests.fakes.fake_research import FakeResearch

        fake = FakeResearch()
        uc, repo = _make_uc_with_research(fake)
        events = list(
            uc.stream(
                owner_id="anon:x",
                task_id=None,
                user_message="什么是数据出境安全评估",
                mode="research",
            )
        )
        # 第一帧 task_created
        assert events[0].event_type is AgentEventType.TASK_CREATED
        # 决策步骤渲染成 thought（首帧 thought 为"已启动深度研究"提示，其后是 FakeResearch 步骤）
        thoughts = [e for e in events if e.event_type is AgentEventType.THOUGHT]
        assert len(thoughts) == 3
        assert "已启动深度研究" in thoughts[0].payload["text"]
        assert "classify" in thoughts[1].payload["text"]
        # 最后一帧 answer，正文为报告
        answer = events[-1]
        assert answer.event_type is AgentEventType.ANSWER
        assert "深度研究报告" in answer.payload["text"]
        # task.mode 持久化为 research
        task = repo.get(events[0].payload["task_id"], "anon:x")
        assert task is not None and task.mode == "research"
        # ReAct agent 没被触发：无 tool_call / decision_parse_error
        kinds = [e.event_type for e in events]
        assert AgentEventType.TOOL_CALL not in kinds
        # research port 收到 query
        assert fake.calls == [
            {
                "query": "什么是数据出境安全评估",
                "owner_id": "anon:x",
                "top_k": 8,
                "enable_web_search": True,
            }
        ]

    def test_research_mode_streams_steps_incrementally(self) -> None:
        """端口实现 ``research_stream`` 时，逐步骤即时产出 thought，最后产出 answer。"""

        from domain.models import Citation, ResearchReport, ResearchStep

        class StreamingFakeResearch:
            """实现 ``research_stream`` 的 Fake：先 yield 两个 step，再 yield 报告。"""

            def __init__(self) -> None:
                self.stream_calls: list[str] = []
                self.blocking_calls: list[str] = []

            def research(self, query: str, **_: object) -> ResearchReport:
                self.blocking_calls.append(query)
                return ResearchReport(answer="不该走到这")

            def research_stream(self, query: str, **_: object):  # noqa: ANN202
                self.stream_calls.append(query)
                yield ResearchStep(step_name="classify", description="正在分析问题类型...")
                yield ResearchStep(step_name="generate", description="生成深度报告...")
                yield ResearchReport(
                    answer="## 流式报告\n\n正文",
                    citations=[
                        Citation(
                            source_type="law",
                            source_name="个人信息保护法",
                            title="第三十八条",
                            text_snippet="向境外提供个人信息……",
                        )
                    ],
                )

        fake = StreamingFakeResearch()
        uc, _ = _make_uc_with_research(fake)
        events = list(
            uc.stream(
                owner_id="anon:x",
                task_id=None,
                user_message="对比三条出境路径的差异",
                mode="research",
            )
        )
        # 走流式分支，未触发阻塞 research()
        assert fake.stream_calls == ["对比三条出境路径的差异"]
        assert fake.blocking_calls == []
        # 首帧 thought 为启动提示，随后是逐步骤 thought
        thoughts = [e for e in events if e.event_type is AgentEventType.THOUGHT]
        assert "已启动深度研究" in thoughts[0].payload["text"]
        assert "classify" in thoughts[1].payload["text"]
        assert "generate" in thoughts[2].payload["text"]
        # 末帧 answer 携带报告正文与 citations
        answer = events[-1]
        assert answer.event_type is AgentEventType.ANSWER
        assert "流式报告" in answer.payload["text"]
        assert answer.payload["citations"][0]["source_name"] == "个人信息保护法"

    def test_research_mode_without_port_falls_back(self) -> None:
        uc, _ = _make_uc_with_research(None)
        events = list(
            uc.stream(
                owner_id="anon:x",
                task_id=None,
                user_message="x" * 5,
                mode="research",
            )
        )
        text = events[-1].payload["text"]
        assert "未在容器中装配" in text or "未装配" in text

    def test_research_answer_carries_citations(self) -> None:
        from domain.models import Citation, ResearchReport
        from tests.fakes.fake_research import FakeResearch

        report = ResearchReport(
            answer="报告正文",
            citations=[
                Citation(
                    source_type="law",
                    source_name="个人信息保护法",
                    title="第三十八条",
                    text_snippet="向境外提供个人信息……",
                )
            ],
        )
        uc, _ = _make_uc_with_research(FakeResearch(report=report))
        events = list(
            uc.stream(
                owner_id="anon:x",
                task_id=None,
                user_message="个人信息出境条件",
                mode="research",
            )
        )
        answer = events[-1]
        assert answer.event_type is AgentEventType.ANSWER
        cites = answer.payload["citations"]
        assert len(cites) == 1
        assert cites[0]["source_name"] == "个人信息保护法"


class _RecordingScheduler:
    """记录 schedule_summarization / schedule_consolidation 调用的 Fake 调度器。"""

    def __init__(self, *, boom: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.consolidation_calls: list[tuple[str, str]] = []
        self._boom = boom

    def schedule_summarization(self, owner_id: str, task_id: str) -> None:
        if self._boom:
            raise RuntimeError("调度炸了")
        self.calls.append((owner_id, task_id))

    def schedule_consolidation(self, owner_id: str, task_id: str) -> None:
        if self._boom:
            raise RuntimeError("调度炸了")
        self.consolidation_calls.append((owner_id, task_id))


def _make_uc_with_scheduler(
    scheduler: object,
) -> tuple[RunCopilotUseCase, InMemoryTaskRepo]:
    repo = InMemoryTaskRepo()
    task_uc = TaskManagementUseCase(repo)
    uc = RunCopilotUseCase(
        agent=FakeCopilotAgent(),
        task_management=task_uc,
        memory_scheduler=scheduler,  # type: ignore[arg-type]
    )
    return uc, repo


class TestMemoryScheduling:
    """qa 模式收尾后应触发 L2 摘要调度（S-030b）。"""

    def test_qa_schedules_summarization(self) -> None:
        sched = _RecordingScheduler()
        uc, _ = _make_uc_with_scheduler(sched)

        events = list(
            uc.stream(owner_id="anon:x", task_id=None, user_message="个人信息出境条件")
        )
        task_id = events[0].payload["task_id"]

        assert sched.calls == [("anon:x", task_id)]

    def test_qa_schedules_consolidation(self) -> None:
        sched = _RecordingScheduler()
        uc, _ = _make_uc_with_scheduler(sched)

        events = list(
            uc.stream(owner_id="anon:x", task_id=None, user_message="个人信息出境条件")
        )
        task_id = events[0].payload["task_id"]

        assert sched.consolidation_calls == [("anon:x", task_id)]

    def test_scheduler_failure_does_not_break_stream(self) -> None:
        sched = _RecordingScheduler(boom=True)
        uc, _ = _make_uc_with_scheduler(sched)

        # 调度抛错也不能影响主回复
        events = list(
            uc.stream(owner_id="anon:x", task_id=None, user_message="问题")
        )

        assert any(e.event_type is AgentEventType.ANSWER for e in events)

    def test_no_scheduler_is_noop(self) -> None:
        uc, _, _ = _make_uc()  # 不带 scheduler

        events = list(
            uc.stream(owner_id="anon:x", task_id=None, user_message="问题")
        )

        assert any(e.event_type is AgentEventType.ANSWER for e in events)


