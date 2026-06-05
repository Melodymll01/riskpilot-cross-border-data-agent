"""AgentEvent 工厂方法 + payload 结构契约测试。"""

from __future__ import annotations

import pytest

from app.agent.events import AgentEvent, AgentEventType


class TestAgentEvent:
    def test_task_created(self) -> None:
        ev = AgentEvent.task_created("task_abc123")
        assert ev.event_type is AgentEventType.TASK_CREATED
        assert ev.payload == {"task_id": "task_abc123"}

    def test_thought(self) -> None:
        ev = AgentEvent.thought("我先检索法条")
        assert ev.event_type is AgentEventType.THOUGHT
        assert ev.payload["text"] == "我先检索法条"

    def test_tool_call_copies_args(self) -> None:
        args = {"query": "PIPL"}
        ev = AgentEvent.tool_call("search_law", args)
        assert ev.event_type is AgentEventType.TOOL_CALL
        assert ev.payload["tool_name"] == "search_law"
        assert ev.payload["tool_args"] == {"query": "PIPL"}
        # 修改外部 dict 不影响事件 payload（防御性拷贝）
        args["query"] = "MUTATED"
        assert ev.payload["tool_args"] == {"query": "PIPL"}

    def test_tool_result_accepts_any_payload(self) -> None:
        ev = AgentEvent.tool_result("search_law", [{"chunk_id": "c1"}])
        assert ev.event_type is AgentEventType.TOOL_RESULT
        assert ev.payload["result"] == [{"chunk_id": "c1"}]

    def test_tool_error(self) -> None:
        ev = AgentEvent.tool_error("web_search", "TimeoutError: 30s")
        assert ev.event_type is AgentEventType.TOOL_ERROR
        assert ev.payload["error"] == "TimeoutError: 30s"

    def test_ask_user_with_missing_facts(self) -> None:
        ev = AgentEvent.ask_user("用户量大致多少？", missing_facts=["user_count"])
        assert ev.event_type is AgentEventType.ASK_USER
        assert ev.payload["question"] == "用户量大致多少？"
        assert ev.payload["missing_facts"] == ["user_count"]

    def test_ask_user_default_no_missing(self) -> None:
        ev = AgentEvent.ask_user("再说一下数据类型？")
        assert ev.payload["missing_facts"] == []

    def test_answer_with_citations(self) -> None:
        ev = AgentEvent.answer("根据 PIPL 第38条...", [{"source_name": "PIPL"}])
        assert ev.event_type is AgentEventType.ANSWER
        assert ev.payload["text"].startswith("根据 PIPL")
        assert ev.payload["citations"] == [{"source_name": "PIPL"}]

    def test_answer_default_no_citations(self) -> None:
        ev = AgentEvent.answer("简短回答")
        assert ev.payload["citations"] == []

    def test_max_steps_reached(self) -> None:
        ev = AgentEvent.max_steps_reached("当前进度...")
        assert ev.event_type is AgentEventType.MAX_STEPS_REACHED
        assert ev.payload["partial_text"] == "当前进度..."

    def test_decision_parse_error(self) -> None:
        ev = AgentEvent.decision_parse_error("not json", "JSONDecodeError")
        assert ev.event_type is AgentEventType.DECISION_PARSE_ERROR
        assert ev.payload["raw"] == "not json"
        assert ev.payload["error"] == "JSONDecodeError"

    def test_frozen_dataclass(self) -> None:
        from dataclasses import FrozenInstanceError

        ev = AgentEvent.thought("x")
        with pytest.raises(FrozenInstanceError):
            ev.event_type = AgentEventType.ANSWER  # type: ignore[misc]

    def test_event_type_str_values(self) -> None:
        # 用作 SSE event 名时直接当字符串使用
        assert AgentEventType.ANSWER.value == "answer"
        assert AgentEventType.TOOL_CALL.value == "tool_call"
