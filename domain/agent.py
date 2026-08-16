"""Copilot 运行事件模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentEventType(str, Enum):
    TASK_CREATED = "task_created"
    THOUGHT = "thought"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_ERROR = "tool_error"
    ASK_USER = "ask_user"
    ANSWER = "answer"


@dataclass(frozen=True)
class AgentEvent:
    event_type: AgentEventType
    payload: dict[str, Any] = field(default_factory=dict)

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
    def ask_user(
        cls,
        question: str,
        missing_facts: list[str] | None = None,
    ) -> AgentEvent:
        return cls(
            AgentEventType.ASK_USER,
            {"question": question, "missing_facts": list(missing_facts or [])},
        )

    @classmethod
    def answer(
        cls,
        text: str,
        citations: list[dict[str, Any]] | None = None,
        msg_id: str | None = None,
    ) -> AgentEvent:
        return cls(
            AgentEventType.ANSWER,
            {
                "text": text,
                "citations": list(citations or []),
                "msg_id": msg_id,
            },
        )
