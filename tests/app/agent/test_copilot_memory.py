"""Agent 记忆注入测试（S-030a）。

覆盖三类重点之"注入"：当前 task 最近 N 条历史能进 prompt；
并验证降级（memory_assembler=None 保持旧行为）与"当前消息不重复进记忆块"。
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from app.agent.copilot import ComplianceCopilotAgent
from app.agent.tools import register_default_tools
from app.memory import MemoryAssembler
from domain.models import Chunk, Message, Task
from infra.memory import TaskBackedMemory
from tests.fakes.fake_chat import FakeChat
from tests.fakes.fake_evidence import FakeEvidence
from tests.fakes.fake_repos import InMemoryTaskRepo
from tests.fakes.fake_retrieve import FakeRetrieve
from tests.fakes.fake_websearch import FakeWebSearch

pytestmark = pytest.mark.unit

_OWNER = "anon:owner1"
_TASK = "task_abc"


def _chunk() -> Chunk:
    return Chunk(
        chunk_id="c1",
        text="个人信息保护法第38条...",
        source_type="law",
        source_name="PIPL",
        title="第38条",
        source_url="https://example.com",
        category="law",
        score=0.9,
    )


def _final(answer: str) -> str:
    return json.dumps(
        {"thought": "t", "action": "final_answer", "answer": answer},
        ensure_ascii=False,
    )


def _make_agent(
    repo: InMemoryTaskRepo,
    *,
    with_memory: bool,
    recent_n: int = 6,
    token_budget: int = 1500,
) -> tuple[ComplianceCopilotAgent, FakeChat]:
    chat = FakeChat(responses=[_final("好的")])
    container = SimpleNamespace(
        retriever=FakeRetrieve([_chunk()]),
        web_search=FakeWebSearch(),
        evidence=FakeEvidence(),
    )
    registry = register_default_tools(container)  # type: ignore[arg-type]
    assembler = None
    if with_memory:
        assembler = MemoryAssembler(
            TaskBackedMemory(repo),
            recent_n=recent_n,
            token_budget=token_budget,
        )
    agent = ComplianceCopilotAgent(
        chat=chat,
        task_repo=repo,
        tool_registry=registry,
        memory_assembler=assembler,
    )
    return agent, chat


def _seed_task(repo: InMemoryTaskRepo, owner_id: str = _OWNER) -> None:
    now = time.time()
    repo.create(
        Task(
            task_id=_TASK,
            owner_id=owner_id,
            title="t",
            state="planning",
            user_goal="",
            collected_facts={},
            created_at=now,
            updated_at=now,
        )
    )


def _seed_history(repo: InMemoryTaskRepo) -> None:
    repo.append_message(
        Message(msg_id="m1", task_id=_TASK, role="user", content="上一轮问题", created_at=1.0)
    )
    repo.append_message(
        Message(msg_id="m2", task_id=_TASK, role="assistant", content="上一轮回答", created_at=2.0)
    )


def _system_contents(chat: FakeChat) -> str:
    msgs = chat.calls[0]["messages"]
    return "\n".join(m["content"] for m in msgs if m["role"] == "system")


class TestInjection:
    def test_recent_history_injected_into_prompt(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo)
        _seed_history(repo)
        agent, chat = _make_agent(repo, with_memory=True)

        list(agent.run(owner_id=_OWNER, task_id=_TASK, user_message="新问题"))

        sys_text = _system_contents(chat)
        assert "上一轮问题" in sys_text
        assert "上一轮回答" in sys_text

    def test_current_message_not_in_memory_block(self) -> None:
        # 记忆块只含先前轮次；当前 user_message 走独立 user 消息，不重复进记忆块。
        repo = InMemoryTaskRepo()
        _seed_task(repo)
        _seed_history(repo)
        agent, chat = _make_agent(repo, with_memory=True)

        list(agent.run(owner_id=_OWNER, task_id=_TASK, user_message="新问题独特串X"))

        sys_text = _system_contents(chat)
        assert "新问题独特串X" not in sys_text

    def test_owner_isolation_no_cross_leak(self) -> None:
        # 历史属于 owner_a；owner_b 跑同一 task_id 不应看到历史。
        repo = InMemoryTaskRepo()
        _seed_task(repo, owner_id="anon:owner_a")
        _seed_history(repo)
        agent, chat = _make_agent(repo, with_memory=True)

        list(agent.run(owner_id="anon:owner_b", task_id=_TASK, user_message="hi"))

        sys_text = _system_contents(chat)
        assert "上一轮问题" not in sys_text


class TestDegradation:
    def test_no_assembler_keeps_old_behavior(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo)
        _seed_history(repo)
        agent, chat = _make_agent(repo, with_memory=False)

        list(agent.run(owner_id=_OWNER, task_id=_TASK, user_message="新问题"))

        msgs = chat.calls[0]["messages"]
        # 旧行为：恰好一个 system（主提示词），无记忆块。
        assert sum(1 for m in msgs if m["role"] == "system") == 1
        assert "上一轮问题" not in _system_contents(chat)
