"""``ComplianceCopilotAgent``：ReAct 风格 LLM 主循环。

设计要点：
- 不假设 ChatPort 支持原生 function-calling；用 JSON 协议（见 ``decision.py``）
- 主循环最多跑 ``max_steps`` 轮；每轮：LLM 决策 → 解析 → 执行 → 喂回观察值
- 任何工具异常或解析失败都"软失败"——产出 error 事件继续走，不 crash
- 流式输出：每动作产出至少一个 ``AgentEvent``，由上层 use case 转 SSE
- 持久化：user_message 在循环前写入，tool_call / 最终 answer 在循环中写入
- 严格 owner_id 隔离：所有 task_repo 操作都带 owner_id；handler 调用注入 owner_id
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from app.agent.decision import (
    AgentDecision,
    AgentDecisionParseError,
    parse_decision,
)
from app.agent.events import AgentEvent
from app.agent.tools import ToolSpec
from domain.models import Citation, Message, ToolCall

if TYPE_CHECKING:
    from domain.ports import ChatPort, TaskRepoPort


DEFAULT_MAX_STEPS = 6
DEFAULT_TEMPERATURE = 0.1


def _system_prompt(tools: dict[str, ToolSpec]) -> str:
    """构造系统提示词，告诉 LLM 工具集与 JSON 决策协议。"""
    tool_lines = []
    for spec in tools.values():
        schema = json.dumps(spec.parameters_schema, ensure_ascii=False)
        tool_lines.append(f"- {spec.name}: {spec.description}\n  parameters: {schema}")
    tools_block = "\n".join(tool_lines)
    return f"""你是合规咨询 Agent。你的目标是帮助用户解答数据出境/隐私合规问题。

【可用工具】
{tools_block}

【决策协议（严格遵守）】
每一轮请只输出一个 JSON 对象，且只包含一个 JSON 对象，不要其他文本。结构：
{{
  "thought": "你的推理过程",
  "action": "tool" | "ask_user" | "final_answer",
  "tool_name": "<工具名>",         // action=tool 时必填
  "tool_args": {{ ... }},            // action=tool 时必填，符合上述 parameters
  "question": "<追问>",             // action=ask_user 时必填
  "missing_facts": ["..."],         // 可选
  "answer": "<最终回复>",           // action=final_answer 时必填
  "citations": [{{...}}]             // 可选，answer 时建议附上
}}

【行为约束】
1. 信息不足时优先 ask_user，不要猜测
2. 涉及法规条款必须先 search_law 获取证据，再 final_answer
3. 涉及用户文档优先 search_user_docs；外部新信息用 web_search
4. final_answer 必须基于已收集到的证据，引用条款名/网址
5. 任何情况下都必须返回有效 JSON——这是唯一的输出协议"""


def _format_observations(observations: list[tuple[str, Any]]) -> str:
    """把工具观察值序列化成单个 user 消息（喂给下一轮决策）。"""
    if not observations:
        return ""
    parts = []
    for name, result in observations:
        body = json.dumps(result, ensure_ascii=False, default=str)
        # 限制单条观察的大小，避免 prompt 爆炸
        if len(body) > 4000:
            body = body[:4000] + "... [截断]"
        parts.append(f"<observation tool=\"{name}\">\n{body}\n</observation>")
    return "\n\n".join(parts)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class ComplianceCopilotAgent:
    """ReAct 主循环 Agent。"""

    def __init__(
        self,
        *,
        chat: ChatPort,
        task_repo: TaskRepoPort,
        tool_registry: dict[str, ToolSpec],
        max_steps: int = DEFAULT_MAX_STEPS,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> None:
        if max_steps < 1:
            msg = f"max_steps must be >= 1, got {max_steps}"
            raise ValueError(msg)
        self._chat = chat
        self._task_repo = task_repo
        self._tools = dict(tool_registry)
        self._max_steps = max_steps
        self._temperature = temperature
        self._system = _system_prompt(self._tools)

    # ── 主接口 ────────────────────────────────────────────────────────

    def run(
        self,
        *,
        owner_id: str,
        task_id: str,
        user_message: str,
    ) -> Iterator[AgentEvent]:
        """跑一轮对话。yield 一系列 AgentEvent。"""
        if not owner_id:
            msg = "owner_id 必填"
            raise ValueError(msg)
        if not task_id:
            msg = "task_id 必填"
            raise ValueError(msg)

        # 1) 持久化用户消息
        self._task_repo.append_message(
            Message(
                msg_id=_new_id("msg"),
                task_id=task_id,
                role="user",
                content=user_message,
            )
        )

        # 2) ReAct 循环
        observations: list[tuple[str, Any]] = []
        last_thought = ""
        for _step in range(self._max_steps):
            messages = self._build_messages(user_message, observations)
            raw = self._chat.chat(
                messages, temperature=self._temperature, max_tokens=None
            )

            try:
                decision = parse_decision(raw)
            except AgentDecisionParseError as exc:
                yield AgentEvent.decision_parse_error(raw=raw, error=str(exc))
                # 解析失败：终止本轮，给出兜底回复
                fallback = "抱歉，我暂时无法给出可靠回答，请稍后再试或换一种问法。"
                self._persist_assistant(task_id, fallback, [])
                yield AgentEvent.answer(fallback, [])
                return

            if decision.thought:
                last_thought = decision.thought
                yield AgentEvent.thought(decision.thought)

            if decision.action == "ask_user":
                assert decision.question is not None
                yield AgentEvent.ask_user(decision.question, decision.missing_facts)
                # 追问不写 assistant message，等下一轮 user_message 回来
                return

            if decision.action == "final_answer":
                assert decision.final_text is not None
                citations = _to_domain_citations(decision.citations)
                self._persist_assistant(task_id, decision.final_text, citations)
                yield AgentEvent.answer(decision.final_text, decision.citations)
                return

            # action == "tool"
            assert decision.tool_name is not None
            tool_event_or_result = self._invoke_tool(
                decision=decision, owner_id=owner_id, task_id=task_id
            )
            yield from tool_event_or_result["events"]
            observations.append((decision.tool_name, tool_event_or_result["observation"]))

        # 3) 达到 max_steps 仍未给出 final_answer
        fallback = (
            f"已达到最大推理步数 ({self._max_steps})。当前思路：{last_thought}"
            if last_thought
            else "已达到最大推理步数，未能完成完整回答。"
        )
        self._persist_assistant(task_id, fallback, [])
        yield AgentEvent.max_steps_reached(fallback)
        yield AgentEvent.answer(fallback, [])

    # ── 内部 ─────────────────────────────────────────────────────────

    def _build_messages(
        self, user_message: str, observations: list[tuple[str, Any]]
    ) -> list[dict[str, str]]:
        msgs: list[dict[str, str]] = [
            {"role": "system", "content": self._system},
            {"role": "user", "content": user_message},
        ]
        obs_text = _format_observations(observations)
        if obs_text:
            msgs.append({"role": "user", "content": f"【观察值】\n{obs_text}"})
        return msgs

    def _invoke_tool(
        self,
        *,
        decision: AgentDecision,
        owner_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        """执行一次工具调用，返回 events + observation。"""
        assert decision.tool_name is not None
        tool_name = decision.tool_name
        tool_args = dict(decision.tool_args)

        events: list[AgentEvent] = [AgentEvent.tool_call(tool_name, tool_args)]

        spec = self._tools.get(tool_name)
        if spec is None:
            err = f"unknown tool {tool_name!r}"
            events.append(AgentEvent.tool_error(tool_name, err))
            self._persist_tool_call(
                task_id=task_id,
                tool_name=tool_name,
                input_json=tool_args,
                output_json=None,
                status="failed",
                duration_ms=0,
            )
            return {"events": events, "observation": {"error": err}}

        # 注入 owner_id（如果工具需要）
        call_kwargs = dict(tool_args)
        if spec.requires_owner:
            call_kwargs["owner_id"] = owner_id

        t0 = time.time()
        try:
            result = spec.handler(**call_kwargs)
            duration_ms = int((time.time() - t0) * 1000)
            events.append(AgentEvent.tool_result(tool_name, result))
            self._persist_tool_call(
                task_id=task_id,
                tool_name=tool_name,
                input_json=tool_args,
                output_json={"result": result} if not isinstance(result, dict) else result,
                status="success",
                duration_ms=duration_ms,
            )
            return {"events": events, "observation": result}
        except Exception as exc:  # noqa: BLE001 — Agent 必须对所有工具失败软失败
            duration_ms = int((time.time() - t0) * 1000)
            err = f"{type(exc).__name__}: {exc}"
            events.append(AgentEvent.tool_error(tool_name, err))
            self._persist_tool_call(
                task_id=task_id,
                tool_name=tool_name,
                input_json=tool_args,
                output_json=None,
                status="failed",
                duration_ms=duration_ms,
            )
            return {"events": events, "observation": {"error": err}}

    def _persist_tool_call(
        self,
        *,
        task_id: str,
        tool_name: str,
        input_json: dict[str, Any],
        output_json: dict[str, Any] | None,
        status: str,
        duration_ms: int,
    ) -> None:
        self._task_repo.append_tool_call(
            ToolCall(
                tool_call_id=_new_id("tc"),
                task_id=task_id,
                tool_name=tool_name,
                input_json=input_json,
                output_json=output_json,
                status=status,  # type: ignore[arg-type]
                duration_ms=duration_ms,
            )
        )

    def _persist_assistant(
        self, task_id: str, content: str, citations: list[Citation]
    ) -> None:
        self._task_repo.append_message(
            Message(
                msg_id=_new_id("msg"),
                task_id=task_id,
                role="assistant",
                content=content,
                citations=citations,
            )
        )


def _to_domain_citations(raw: list[dict[str, Any]]) -> list[Citation]:
    """LLM 返回的 citations dict → domain.Citation；非法字段忽略。

    入参由 ``parse_decision`` 保证只含 dict（已过滤），故无需再做 isinstance 守卫。
    """
    out: list[Citation] = []
    for item in raw:
        try:
            out.append(
                Citation(
                    source_type=str(item.get("source_type") or "law"),
                    source_name=str(item.get("source_name") or "未知来源"),
                    title=str(item.get("title") or ""),
                    source_url=item.get("source_url") if isinstance(item.get("source_url"), str) else None,
                    text_snippet=str(item.get("text_snippet") or "")[:500],
                )
            )
        except Exception:  # noqa: BLE001 — 单条失败不影响整体
            continue
    return out
