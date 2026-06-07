"""``RunCopilotUseCase``：API 入口的薄壳。

职责：
- 没有 task_id 就先 create_task
- 把 attachment 信息塞进 user_message（让 Agent 知道有可用文档）
- 委托 Agent 主循环流式产出 ``AgentEvent``
- ``mode == "profile"`` 时跳过 agent，直接调 ``RiskProfilePort.assess()``，
  把结果（或 ``RiskProfileNotReady``）格式化成 ``answer`` 事件返回
- ``mode == "research"`` 时跳过 ReAct agent，走 ``ResearchPort.research()`` 产出长篇
  结构化报告：决策步骤渲染成 ``thought`` 事件，报告正文渲染成 ``answer`` 事件（Step 028）

API 层（Step 010）直接迭代 yield 出来的事件序列化成 SSE。
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import TYPE_CHECKING

from app.agent.events import AgentEvent
from domain.errors import RiskProfileNotReady

if TYPE_CHECKING:
    from app.agent.copilot import ComplianceCopilotAgent
    from app.use_cases.task_management import TaskManagementUseCase
    from domain.models import RiskProfile, TaskMode
    from domain.ports import MemoryJobSchedulerPort, ResearchPort, RiskProfilePort


class RunCopilotUseCase:
    def __init__(
        self,
        *,
        agent: ComplianceCopilotAgent,
        task_management: TaskManagementUseCase,
        risk_profile: RiskProfilePort | None = None,
        research: ResearchPort | None = None,
        memory_scheduler: MemoryJobSchedulerPort | None = None,
    ) -> None:
        self._agent = agent
        self._task_uc = task_management
        self._risk_profile = risk_profile
        self._research = research
        self._memory_scheduler = memory_scheduler

    def stream(
        self,
        *,
        owner_id: str,
        task_id: str | None,
        user_message: str,
        attachment_doc_ids: list[str] | None = None,
        mode: TaskMode = "qa",
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
                owner_id, title=title, user_goal=user_message, mode=mode
            )
            task_id = task.task_id
            yield AgentEvent.task_created(task_id)

        # 2) profile 模式：跳过 agent，直接走 RiskProfilePort
        if mode == "profile":
            yield from self._run_profile(target=user_message)
            return

        # 2b) research 模式：跳过 ReAct agent，走 ResearchPort 产出长篇报告
        if mode == "research":
            yield from self._run_research(query=user_message)
            return

        # 3) qa：附件信息进 user_message，再跑 Agent
        effective_message = user_message
        if attachment_doc_ids:
            ids = ", ".join(attachment_doc_ids)
            effective_message = f"{user_message}\n\n[已上传文档 ID: {ids}]"

        try:
            yield from self._agent.run(
                owner_id=owner_id,
                task_id=task_id,
                user_message=effective_message,
            )
        finally:
            # 回复完成后显式调度 L2 摘要（§14.1）。放 finally 而非生成器尾部：
            # SSE 客户端提前断连触发 GeneratorExit 时仍会调度，不漏跑；
            # 后台 best-effort，失败下一轮按 watermark 自愈。
            self._schedule_memory(owner_id=owner_id, task_id=task_id)

    # ─── 记忆调度 ──────────────────────────────────────────────────

    def _schedule_memory(self, *, owner_id: str, task_id: str) -> None:
        if self._memory_scheduler is None:
            return
        # 调度失败绝不影响主回复（后台 best-effort）。
        with contextlib.suppress(Exception):
            self._memory_scheduler.schedule_summarization(owner_id, task_id)
        with contextlib.suppress(Exception):
            self._memory_scheduler.schedule_consolidation(owner_id, task_id)

    # ─── profile 分支 ──────────────────────────────────────────────

    def _run_profile(self, *, target: str) -> Iterator[AgentEvent]:
        """profile 模式分流：调 RiskProfilePort，把结果或未就绪提示渲染成 answer。"""
        if self._risk_profile is None:
            yield AgentEvent.answer(
                "⚠️ 风险画像服务未在容器中装配，请联系运维。"
            )
            return
        try:
            result = self._risk_profile.assess(target=target)
        except RiskProfileNotReady as exc:
            yield AgentEvent.answer(
                "⏳ **风险画像模型尚未上线**\n\n"
                f"{exc}\n\n"
                "目前 `📊 风险画像` Tab 以接口预留形态运行；"
                "`schema-evidence-risk-profiling` 仓库的 evidence-state v1 "
                "模型完成训练后会自动接入此处。"
            )
            return
        yield AgentEvent.answer(_format_risk_profile_md(result))

    # ─── research 分支 ─────────────────────────────────────────────

    def _run_research(self, *, query: str) -> Iterator[AgentEvent]:
        """research 模式分流：调 ResearchPort，把决策步骤渲染成 thought、报告渲染成 answer。"""
        if self._research is None:
            yield AgentEvent.answer(
                "⚠️ 深度研究服务未在容器中装配，请联系运维。"
            )
            return
        report = self._research.research(query)
        # 把研究链路（分类 → 改写 → 多轮检索 → 证据检查 → 生成）逐步渲染成 thought
        for step in report.steps:
            detail = f"[{step.step_name}] {step.description}"
            if step.result_summary:
                detail += f" → {step.result_summary}"
            yield AgentEvent.thought(detail)
        citations = [c.model_dump() for c in report.citations]
        yield AgentEvent.answer(report.answer, citations)


# ─── 工具函数 ──────────────────────────────────────────────────────

_STATE_EMOJI: dict[str, str] = {
    "supported": "✅",
    "contradicted": "❌",
    "not_disclosed": "⚪",
    "insufficiently_disclosed": "🟡",
    "irrelevant": "⚫",
}

_STATE_LABEL: dict[str, str] = {
    "supported": "supported（文档显式支持目标命题）",
    "contradicted": "contradicted（文档反驳目标命题）",
    "not_disclosed": "not_disclosed（文档未涉及该命题）",
    "insufficiently_disclosed": "insufficiently_disclosed（涉及但信息不足）",
    "irrelevant": "irrelevant（与命题无关）",
}


def _format_risk_profile_md(rp: RiskProfile) -> str:
    """把 ``RiskProfile`` 渲染成 markdown，适配前端 marked 渲染。"""
    state = rp.evidence_state
    lines: list[str] = [
        "## 风险画像评估",
        "",
        f"**目标命题**：{rp.target}",
        "",
        f"**证据状态**：{_STATE_EMOJI.get(state, '·')} {_STATE_LABEL.get(state, state)}",
    ]
    if rp.explanation:
        lines += ["", f"**解释**：{rp.explanation}"]
    if rp.evidence_spans:
        lines += ["", "### 关键证据"]
        for sp in rp.evidence_spans:
            offset = (
                f"（字符 {sp.start}-{sp.end}）"
                if sp.start is not None and sp.end is not None
                else ""
            )
            lines.append(f"- {sp.text}{offset}")
    return "\n".join(lines)

