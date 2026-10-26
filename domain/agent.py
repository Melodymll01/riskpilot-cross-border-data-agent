"""Copilot 运行事件模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AgentEventType(StrEnum):
    TASK_CREATED = "task_created"
    THOUGHT = "thought"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_ERROR = "tool_error"
    RUN_STARTED = "run_started"
    RUN_INTERRUPTED = "run_interrupted"
    RUN_RESUMED = "run_resumed"
    RUN_STATUS = "run_status"
    DRAFT_DELTA = "draft_delta"
    DRAFT_DISCARDED = "draft_discarded"
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
    def run_started(cls, run_id: str) -> AgentEvent:
        return cls(AgentEventType.RUN_STARTED, {"run_id": run_id})

    @classmethod
    def run_interrupted(cls, run_id: str, stage: str) -> AgentEvent:
        return cls(AgentEventType.RUN_INTERRUPTED, {"run_id": run_id, "stage": stage})

    @classmethod
    def run_resumed(cls, run_id: str) -> AgentEvent:
        return cls(AgentEventType.RUN_RESUMED, {"run_id": run_id})

    @classmethod
    def run_status(
        cls,
        run_id: str,
        status: str,
        stage: str,
        reason: str | None = None,
    ) -> AgentEvent:
        payload: dict[str, Any] = {"run_id": run_id, "status": status, "stage": stage}
        if reason:
            payload["reason"] = reason
        return cls(AgentEventType.RUN_STATUS, payload)

    @classmethod
    def draft_delta(cls, run_id: str, text: str) -> AgentEvent:
        return cls(AgentEventType.DRAFT_DELTA, {"run_id": run_id, "text": text})

    @classmethod
    def draft_discarded(cls, run_id: str, reason: str) -> AgentEvent:
        return cls(AgentEventType.DRAFT_DISCARDED, {"run_id": run_id, "reason": reason})

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
