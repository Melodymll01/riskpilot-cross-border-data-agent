"""Agent 流式事件协议。

每一步 Agent 都产出一个 ``AgentEvent``。事件 = 类型 + 一个 ``payload`` 字典；
API 层（Step 010）直接把它序列化成 JSON / SSE 推给前端。

设计：
- 不依赖任何 infra；纯数据类
- 使用 frozen=True 防止意外修改
- 不放 domain 引用，保持事件可独立序列化
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentEventType(str, Enum):
    """Agent 主循环可能产出的事件类型。"""

    TASK_CREATED = "task_created"
    THOUGHT = "thought"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_ERROR = "tool_error"
    ASK_USER = "ask_user"
    ANSWER = "answer"
    MAX_STEPS_REACHED = "max_steps_reached"
    DECISION_PARSE_ERROR = "decision_parse_error"


@dataclass(frozen=True)
class AgentEvent:
    """Agent 主循环一步的输出。"""

    event_type: AgentEventType
    payload: dict[str, Any] = field(default_factory=dict)

    # ── 工厂方法：避免外部直接拼 payload，类型安全且易于演进 ─────────

    @classmethod
    def task_created(cls, task_id: str) -> AgentEvent:
        return cls(AgentEventType.TASK_CREATED, {"task_id": task_id})

    @classmethod
    def thought(cls, text: str) -> AgentEvent:
        return cls(AgentEventType.THOUGHT, {"text": text})

    @classmethod
    def tool_call(cls, tool_name: str, tool_args: dict[str, Any]) -> AgentEvent:
        return cls(
            AgentEventType.TOOL_CALL,
            {"tool_name": tool_name, "tool_args": dict(tool_args)},
        )

    @classmethod
    def tool_result(cls, tool_name: str, result: Any) -> AgentEvent:
        return cls(
            AgentEventType.TOOL_RESULT,
            {"tool_name": tool_name, "result": result},
        )

    @classmethod
    def tool_error(cls, tool_name: str, error: str) -> AgentEvent:
        return cls(
            AgentEventType.TOOL_ERROR,
            {"tool_name": tool_name, "error": error},
        )

    @classmethod
    def ask_user(cls, question: str, missing_facts: list[str] | None = None) -> AgentEvent:
        return cls(
            AgentEventType.ASK_USER,
            {"question": question, "missing_facts": list(missing_facts or [])},
        )

    @classmethod
    def answer(cls, text: str, citations: list[dict[str, Any]] | None = None) -> AgentEvent:
        return cls(
            AgentEventType.ANSWER,
            {"text": text, "citations": list(citations or [])},
        )

    @classmethod
    def max_steps_reached(cls, partial_text: str) -> AgentEvent:
        return cls(AgentEventType.MAX_STEPS_REACHED, {"partial_text": partial_text})

    @classmethod
    def decision_parse_error(cls, raw: str, error: str) -> AgentEvent:
        return cls(
            AgentEventType.DECISION_PARSE_ERROR,
            {"raw": raw, "error": error},
        )
