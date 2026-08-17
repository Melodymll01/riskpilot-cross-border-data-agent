"""LangChain Copilot 的 Tool Calling、隔离和记忆测试。"""

from __future__ import annotations

import time

from langchain_core.messages import AIMessage, SystemMessage

from app.memory import MemoryAssembler
from domain.agent import AgentEventType
from domain.models import Chunk, Message, Task
from infra.agents import LangChainComplianceAgent
from infra.memory import TaskBackedMemory
from tests.fakes import (
    FakeRetrieve,
    FakeRiskProfile,
    FakeToolCallingModel,
    FakeTrace,
    FakeWebSearch,
    InMemoryTaskRepo,
)


def _seed_task(repo: InMemoryTaskRepo, *, owner_id: str = "anon:alice") -> Task:
    now = time.time()
    task = Task(
        task_id="task_001",
        owner_id=owner_id,
        title="test",
        state="planning",
        user_goal="",
        collected_facts={},
        created_at=now,
        updated_at=now,
    )
    repo.create(task)
    return task


def _chunk() -> Chunk:
    return Chunk(
        chunk_id="chunk_001",
        text="个人信息保护法第三十八条规定了个人信息出境条件。",
        source_type="law",
        source_name="个人信息保护法",
        title="第三十八条",
        score=0.9,
    )


def _agent(
    model: FakeToolCallingModel,
    repo: InMemoryTaskRepo,
    *,
    retriever: FakeRetrieve | None = None,
    memory_assembler: MemoryAssembler | None = None,
) -> LangChainComplianceAgent:
    return LangChainComplianceAgent(
        model=model,
        task_repo=repo,
        retriever=retriever or FakeRetrieve([_chunk()]),
        web_search=FakeWebSearch(),
        risk_profile=FakeRiskProfile(),
        memory_assembler=memory_assembler,
    )


def test_direct_answer_persists_messages() -> None:
    repo = InMemoryTaskRepo()
    task = _seed_task(repo)
    model = FakeToolCallingModel(responses=[AIMessage(content="直接回答")])

    events = list(
        _agent(model, repo).run(
            owner_id=task.owner_id,
            task_id=task.task_id,
            user_message="问题",
        )
    )

    assert [event.event_type for event in events] == [AgentEventType.ANSWER]
    messages = repo.list_messages(task.task_id)
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[-1].content == "直接回答"


def test_copilot_records_only_structured_trace_metadata() -> None:
    repo = InMemoryTaskRepo()
    task = _seed_task(repo)
    trace = FakeTrace()
    model = FakeToolCallingModel(responses=[AIMessage(content="直接回答")])
    agent = LangChainComplianceAgent(
        model=model,
        task_repo=repo,
        retriever=FakeRetrieve([_chunk()]),
        web_search=FakeWebSearch(),
        risk_profile=FakeRiskProfile(),
        trace=trace,
    )

    list(
        agent.run(
            owner_id=task.owner_id,
            task_id=task.task_id,
            user_message="案件正文不得进入 Trace",
        )
    )

    assert trace.spans[0]["name"] == "riskpilot.copilot.run"
    metadata = trace.spans[0]["metadata"]
    assert metadata["message_length"] == len("案件正文不得进入 Trace")
    assert metadata["status"] == "completed"
    assert metadata["tool_count"] == 0
    assert "user_message" not in metadata
    assert "案件正文不得进入 Trace" not in str(metadata)


def test_tool_call_uses_runtime_owner_and_is_audited() -> None:
    repo = InMemoryTaskRepo()
    task = _seed_task(repo, owner_id="github:alice")
    retriever = FakeRetrieve([_chunk()])
    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_law",
                        "args": {"query": "PIPL 38条", "top_k": 3},
                        "id": "call_001",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="依据 [个人信息保护法] 第三十八条……"),
        ]
    )

    events = list(
        _agent(model, repo, retriever=retriever).run(
            owner_id=task.owner_id,
            task_id=task.task_id,
            user_message="个人信息出境条件是什么？",
        )
    )

    assert [event.event_type for event in events] == [
        AgentEventType.TOOL_CALL,
        AgentEventType.TOOL_RESULT,
        AgentEventType.ANSWER,
    ]
    assert retriever.calls[0]["owner_id"] == "github:alice"
    assert retriever.calls[0]["corpus"] == "law"
    tool_call = next(iter(repo._tool_calls.values()))
    assert tool_call.tool_name == "search_law"
    assert tool_call.status == "success"
    assert events[-1].payload["citations"][0]["source_name"] == "个人信息保护法"


def test_risk_profile_tool_calls_real_port_contract() -> None:
    repo = InMemoryTaskRepo()
    task = _seed_task(repo)
    risk_profile = FakeRiskProfile()
    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "risk_profile_assess",
                        "args": {
                            "target": "临床数据是否出境",
                            "document": "合同约定传输至德国总部",
                        },
                        "id": "call_profile",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="风险评估完成"),
        ]
    )
    agent = LangChainComplianceAgent(
        model=model,
        task_repo=repo,
        retriever=FakeRetrieve([_chunk()]),
        web_search=FakeWebSearch(),
        risk_profile=risk_profile,
    )

    events = list(
        agent.run(
            owner_id=task.owner_id,
            task_id=task.task_id,
            user_message="评估该合同",
        )
    )

    assert risk_profile.calls[0]["target"] == "临床数据是否出境"
    assert risk_profile.calls[0]["document"] == "合同约定传输至德国总部"
    assert events[1].event_type is AgentEventType.TOOL_RESULT
    assert events[1].payload["result"]["evidence_state"] == "supported"


def test_memory_is_injected_as_system_message_without_current_query_duplication() -> None:
    repo = InMemoryTaskRepo()
    task = _seed_task(repo)
    repo.append_message(
        Message(
            msg_id="msg_old",
            task_id=task.task_id,
            role="user",
            content="我偏好中文回答",
        )
    )
    assembler = MemoryAssembler(
        TaskBackedMemory(repo),
        recent_n=6,
        token_budget=1500,
    )
    model = FakeToolCallingModel(responses=[AIMessage(content="好的")])

    list(
        _agent(model, repo, memory_assembler=assembler).run(
            owner_id=task.owner_id,
            task_id=task.task_id,
            user_message="本轮独特问题",
        )
    )

    system_text = "\n".join(
        str(message.content) for message in model.calls[0] if isinstance(message, SystemMessage)
    )
    assert "我偏好中文回答" in system_text
    assert "本轮独特问题" not in system_text


def test_unknown_owner_cannot_read_task() -> None:
    repo = InMemoryTaskRepo()
    task = _seed_task(repo, owner_id="anon:alice")
    model = FakeToolCallingModel(responses=[AIMessage(content="不应调用")])

    try:
        list(
            _agent(model, repo).run(
                owner_id="anon:bob",
                task_id=task.task_id,
                user_message="问题",
            )
        )
    except ValueError as exc:
        assert "不属于" in str(exc)
    else:
        raise AssertionError("跨 owner task 必须拒绝")
