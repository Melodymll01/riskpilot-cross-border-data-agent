"""``ComplianceCopilotAgent`` ReAct 主循环测试。

策略：FakeChat 按调用顺序返回预设 JSON 决策字符串，FakeRetrieve/Web/Evidence
提供工具数据，InMemoryTaskRepo 校验持久化。
"""

from __future__ import annotations

import json
import time

import pytest

from app.agent.copilot import ComplianceCopilotAgent
from app.agent.events import AgentEventType
from app.agent.tools import ToolSpec, register_default_tools
from domain.models import Chunk, Task
from tests.fakes.fake_chat import FakeChat
from tests.fakes.fake_evidence import FakeEvidence
from tests.fakes.fake_repos import InMemoryTaskRepo
from tests.fakes.fake_retrieve import FakeRetrieve
from tests.fakes.fake_websearch import FakeWebSearch

# ── 构造工具 ────────────────────────────────────────────────────────────


def _chunk(cid: str = "c1") -> Chunk:
    return Chunk(
        chunk_id=cid,
        text="个人信息保护法第38条...",
        source_type="law",
        source_name="PIPL",
        title="第38条",
        source_url="https://example.com",
        category="law",
        score=0.9,
    )


def _make_agent(
    *,
    responses: list[str],
    retriever: FakeRetrieve | None = None,
    web_search: FakeWebSearch | None = None,
    evidence: FakeEvidence | None = None,
    max_steps: int = 6,
) -> tuple[ComplianceCopilotAgent, FakeChat, InMemoryTaskRepo]:
    chat = FakeChat(responses=responses)
    repo = InMemoryTaskRepo()
    from types import SimpleNamespace

    container = SimpleNamespace(
        retriever=retriever or FakeRetrieve([_chunk()]),
        web_search=web_search or FakeWebSearch(),
        evidence=evidence or FakeEvidence(),
    )
    registry = register_default_tools(container)  # type: ignore[arg-type]
    agent = ComplianceCopilotAgent(
        chat=chat,
        task_repo=repo,
        tool_registry=registry,
        max_steps=max_steps,
    )
    return agent, chat, repo


def _seed_task(repo: InMemoryTaskRepo, owner_id: str = "anon:owner1") -> Task:
    now = time.time()
    task = Task(
        task_id="task_abc",
        owner_id=owner_id,
        title="t",
        state="planning",
        user_goal="",
        collected_facts={},
        created_at=now,
        updated_at=now,
    )
    repo.create(task)
    return task


# ── 单步：final_answer ─────────────────────────────────────────────────


class TestDirectFinalAnswer:
    def test_emits_thought_then_answer(self) -> None:
        agent, chat, repo = _make_agent(
            responses=[
                json.dumps(
                    {
                        "thought": "信息已够",
                        "action": "final_answer",
                        "answer": "PIPL 第38条...",
                        "citations": [{"source_name": "PIPL", "source_type": "law"}],
                    }
                )
            ]
        )
        task = _seed_task(repo)
        events = list(
            agent.run(owner_id=task.owner_id, task_id=task.task_id, user_message="你好")
        )

        types = [e.event_type for e in events]
        assert types == [AgentEventType.THOUGHT, AgentEventType.ANSWER]
        assert events[1].payload["text"] == "PIPL 第38条..."
        # LLM 只被叫了 1 次
        assert len(chat.calls) == 1

    def test_persists_user_and_assistant_messages(self) -> None:
        agent, _, repo = _make_agent(
            responses=[
                json.dumps(
                    {
                        "thought": "ok",
                        "action": "final_answer",
                        "answer": "answer-text",
                    }
                )
            ]
        )
        task = _seed_task(repo)
        list(agent.run(owner_id=task.owner_id, task_id=task.task_id, user_message="问题"))

        msgs = repo.list_messages(task.task_id)
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[0].content == "问题"
        assert msgs[1].role == "assistant"
        assert msgs[1].content == "answer-text"


# ── ask_user 终止 ──────────────────────────────────────────────────────


class TestAskUser:
    def test_ask_terminates_loop(self) -> None:
        agent, chat, repo = _make_agent(
            responses=[
                json.dumps(
                    {
                        "thought": "缺信息",
                        "action": "ask_user",
                        "question": "用户规模？",
                        "missing_facts": ["user_count"],
                    }
                ),
                # 这条不应被调用
                json.dumps({"thought": "", "action": "final_answer", "answer": "x"}),
            ]
        )
        task = _seed_task(repo)
        events = list(agent.run(owner_id=task.owner_id, task_id=task.task_id, user_message="q"))

        types = [e.event_type for e in events]
        assert types == [AgentEventType.THOUGHT, AgentEventType.ASK_USER]
        assert events[1].payload["question"] == "用户规模？"
        assert events[1].payload["missing_facts"] == ["user_count"]
        assert len(chat.calls) == 1  # 没继续

    def test_ask_does_not_persist_assistant_message(self) -> None:
        agent, _, repo = _make_agent(
            responses=[
                json.dumps({"thought": "", "action": "ask_user", "question": "?"})
            ]
        )
        task = _seed_task(repo)
        list(agent.run(owner_id=task.owner_id, task_id=task.task_id, user_message="q"))
        msgs = repo.list_messages(task.task_id)
        # 只有 user 消息，没 assistant
        assert [m.role for m in msgs] == ["user"]


# ── 工具调用闭环 ───────────────────────────────────────────────────────


class TestToolCallLoop:
    def test_tool_call_then_final_answer(self) -> None:
        retr = FakeRetrieve([_chunk("c1"), _chunk("c2")])
        agent, chat, repo = _make_agent(
            responses=[
                json.dumps(
                    {
                        "thought": "先查法条",
                        "action": "tool",
                        "tool_name": "search_law",
                        "tool_args": {"query": "PIPL 38条"},
                    }
                ),
                json.dumps(
                    {
                        "thought": "拿到结果",
                        "action": "final_answer",
                        "answer": "依据 PIPL 第38条...",
                    }
                ),
            ],
            retriever=retr,
        )
        task = _seed_task(repo)
        events = list(agent.run(owner_id=task.owner_id, task_id=task.task_id, user_message="q"))

        types = [e.event_type for e in events]
        assert types == [
            AgentEventType.THOUGHT,
            AgentEventType.TOOL_CALL,
            AgentEventType.TOOL_RESULT,
            AgentEventType.THOUGHT,
            AgentEventType.ANSWER,
        ]
        # 第一次 LLM 决策：messages 只含 system + user
        assert len(chat.calls[0]["messages"]) == 2
        # 第二次 LLM 决策：messages 含 observation
        assert len(chat.calls[1]["messages"]) == 3
        obs_text = chat.calls[1]["messages"][2]["content"]
        assert "search_law" in obs_text
        assert "c1" in obs_text

    def test_tool_call_owner_id_injected(self) -> None:
        retr = FakeRetrieve([_chunk()])
        agent, _, repo = _make_agent(
            responses=[
                json.dumps(
                    {
                        "thought": "",
                        "action": "tool",
                        "tool_name": "search_law",
                        "tool_args": {"query": "q"},
                    }
                ),
                json.dumps({"thought": "", "action": "final_answer", "answer": "ok"}),
            ],
            retriever=retr,
        )
        task = _seed_task(repo, owner_id="github:alice")
        list(agent.run(owner_id="github:alice", task_id=task.task_id, user_message="q"))

        # retriever 收到的 owner_id 是注入进去的（即使 LLM 没在 tool_args 写）
        assert retr.calls[0]["owner_id"] == "github:alice"

    def test_unknown_tool_emits_error_and_continues(self) -> None:
        agent, _, repo = _make_agent(
            responses=[
                json.dumps(
                    {
                        "thought": "",
                        "action": "tool",
                        "tool_name": "nonexistent_tool",
                        "tool_args": {},
                    }
                ),
                json.dumps({"thought": "降级", "action": "final_answer", "answer": "fallback"}),
            ]
        )
        task = _seed_task(repo)
        events = list(agent.run(owner_id=task.owner_id, task_id=task.task_id, user_message="q"))

        types = [e.event_type for e in events]
        assert AgentEventType.TOOL_ERROR in types
        assert types[-1] == AgentEventType.ANSWER
        # 错误信息应包含工具名
        err_ev = next(e for e in events if e.event_type is AgentEventType.TOOL_ERROR)
        assert "nonexistent_tool" in err_ev.payload["error"]

    def test_tool_handler_exception_softfails(self) -> None:
        # 自定义一个总是抛错的工具
        def _bad(**_: object) -> None:
            msg = "boom"
            raise RuntimeError(msg)

        chat = FakeChat(
            responses=[
                json.dumps(
                    {
                        "thought": "",
                        "action": "tool",
                        "tool_name": "bad",
                        "tool_args": {},
                    }
                ),
                json.dumps({"thought": "", "action": "final_answer", "answer": "ok"}),
            ]
        )
        repo = InMemoryTaskRepo()
        registry: dict[str, ToolSpec] = {
            "bad": ToolSpec(
                name="bad",
                description="d",
                parameters_schema={"type": "object"},
                handler=_bad,
            )
        }
        agent = ComplianceCopilotAgent(
            chat=chat, task_repo=repo, tool_registry=registry, max_steps=3
        )
        task = _seed_task(repo)
        events = list(agent.run(owner_id=task.owner_id, task_id=task.task_id, user_message="q"))

        err_ev = next(e for e in events if e.event_type is AgentEventType.TOOL_ERROR)
        assert "boom" in err_ev.payload["error"]
        assert "RuntimeError" in err_ev.payload["error"]
        # 最终仍要给出 answer
        assert events[-1].event_type is AgentEventType.ANSWER

    def test_tool_call_persisted_to_repo(self) -> None:
        retr = FakeRetrieve([_chunk()])
        agent, _, repo = _make_agent(
            responses=[
                json.dumps(
                    {
                        "thought": "",
                        "action": "tool",
                        "tool_name": "search_law",
                        "tool_args": {"query": "q"},
                    }
                ),
                json.dumps({"thought": "", "action": "final_answer", "answer": "ok"}),
            ],
            retriever=retr,
        )
        task = _seed_task(repo)
        list(agent.run(owner_id=task.owner_id, task_id=task.task_id, user_message="q"))

        assert len(repo._tool_calls) == 1
        tc = next(iter(repo._tool_calls.values()))
        assert tc.tool_name == "search_law"
        assert tc.status == "success"
        assert tc.input_json == {"query": "q"}


# ── 异常路径 ───────────────────────────────────────────────────────────


class TestErrorHandling:
    def test_invalid_json_response_yields_parse_error_and_fallback(self) -> None:
        agent, _, repo = _make_agent(responses=["not a json"])
        task = _seed_task(repo)
        events = list(agent.run(owner_id=task.owner_id, task_id=task.task_id, user_message="q"))

        types = [e.event_type for e in events]
        assert AgentEventType.DECISION_PARSE_ERROR in types
        assert types[-1] == AgentEventType.ANSWER
        # 兜底 assistant message 已写入
        msgs = repo.list_messages(task.task_id)
        assert msgs[-1].role == "assistant"

    def test_max_steps_exhausted(self) -> None:
        # 每一步都让 LLM 继续调工具，永不 final_answer
        loop_response = json.dumps(
            {
                "thought": "继续",
                "action": "tool",
                "tool_name": "search_law",
                "tool_args": {"query": "q"},
            }
        )
        agent, _, repo = _make_agent(
            responses=[loop_response] * 10,  # 远超 max_steps
            max_steps=2,
        )
        task = _seed_task(repo)
        events = list(agent.run(owner_id=task.owner_id, task_id=task.task_id, user_message="q"))

        types = [e.event_type for e in events]
        assert AgentEventType.MAX_STEPS_REACHED in types
        # 最后一定是 answer 兜底
        assert types[-1] == AgentEventType.ANSWER
        # 兜底文案包含"最大推理步数"
        assert "最大推理步数" in events[-1].payload["text"]


class TestArgValidation:
    def test_max_steps_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="max_steps"):
            ComplianceCopilotAgent(
                chat=FakeChat(), task_repo=InMemoryTaskRepo(), tool_registry={}, max_steps=0
            )

    def test_owner_id_required(self) -> None:
        agent, _, _ = _make_agent(responses=["{}"])
        with pytest.raises(ValueError, match="owner_id"):
            list(agent.run(owner_id="", task_id="t", user_message="q"))

    def test_task_id_required(self) -> None:
        agent, _, _ = _make_agent(responses=["{}"])
        with pytest.raises(ValueError, match="task_id"):
            list(agent.run(owner_id="anon:x", task_id="", user_message="q"))
