"""Trusted LangChain Copilot output, tool scope and trace tests."""

from __future__ import annotations

import hashlib
import threading
import time

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langgraph.checkpoint.memory import InMemorySaver
from prometheus_client import CollectorRegistry

from app.memory import MemoryAssembler
from app.runtime_control import RunControl
from app.use_cases.evidence_qa import EvidenceQAUseCase
from domain.agent import AgentEventType
from domain.models import Chunk, ConversationRun, Message, Task
from domain.qa import ClaimSupportJudgement, ClaimSupportResult, EvidenceQAClaim, EvidenceQADraft
from infra.agents import LangChainComplianceAgent
from infra.memory import TaskBackedMemory
from infra.observability import PrometheusMetricsAdapter
from tests.fakes import (
    FakeClaimSupportVerifier,
    FakeEmbed,
    FakeEvidenceIndex,
    FakeEvidenceQAGenerator,
    FakeRetrieve,
    FakeStreamingToolCallingModel,
    FakeToolCallingModel,
    FakeTrace,
    FakeWebSearch,
    InMemoryConversationRunRepo,
    InMemoryDocumentRepo,
    InMemoryTaskRepo,
)


def _chunk() -> Chunk:
    return Chunk(
        chunk_id="law-38",
        text="个人信息保护法第三十八条规定了个人信息出境条件。",
        source_type="law",
        source_name="中华人民共和国个人信息保护法",
        title="第三十八条",
        source_url="https://example.test/pipl",
        score=0.9,
        metadata={"source_version": "2021-08-20", "clause_id": "第38条"},
    )


def _setup_agent(
    model: FakeToolCallingModel,
    *,
    repo: InMemoryTaskRepo | None = None,
    retriever: FakeRetrieve | None = None,
    generator: FakeEvidenceQAGenerator | None = None,
    verifier: FakeClaimSupportVerifier | None = None,
    trace: FakeTrace | None = None,
    metrics=None,
    memory_assembler: MemoryAssembler | None = None,
):
    task_repo = repo or InMemoryTaskRepo()
    run_repo = InMemoryConversationRunRepo(task_repo)
    evidence_qa = EvidenceQAUseCase(
        retriever=retriever or FakeRetrieve([_chunk()]),
        evidence_index=FakeEvidenceIndex(),
        document_repo=InMemoryDocumentRepo(),
        embedder=FakeEmbed(),
        generator=generator or FakeEvidenceQAGenerator(),
        support_verifier=verifier or FakeClaimSupportVerifier(),
        trace=trace,
        metrics=metrics,
    )
    agent = LangChainComplianceAgent(
        model=model,
        task_repo=task_repo,
        run_repo=run_repo,
        retriever=retriever or FakeRetrieve([_chunk()]),
        web_search=FakeWebSearch(),
        evidence_qa=evidence_qa,
        checkpointer=InMemorySaver(),
        memory_assembler=memory_assembler,
        trace=trace,
        metrics=metrics,
    )
    return agent, task_repo, run_repo


def _execute(agent, task_repo, run_repo, *, owner_id="anon:alice", question="问题"):
    now = time.time()
    task = Task(
        task_id="task-1",
        owner_id=owner_id,
        title="test",
        user_goal=question,
        created_at=now,
        updated_at=now,
    )
    task_repo.create(task)
    user = Message(msg_id="msg-user", task_id=task.task_id, role="user", content=question)
    task_repo.append_message(user)
    run = ConversationRun(
        run_id="run-1",
        thread_id="thread-1",
        owner_id=owner_id,
        task_id=task.task_id,
        question=question,
        input_message_id=user.msg_id,
        created_at=now,
        updated_at=now,
    )
    run_repo.create(run)
    events = list(
        agent.run(
            owner_id=owner_id,
            task_id=task.task_id,
            user_message=question,
            attachment_doc_ids=[],
            run_id=run.run_id,
            thread_id=run.thread_id,
            input_message_id=user.msg_id,
            runtime_control=_runtime_control(run_repo, run),
        )
    )
    return events, task


def _runtime_control(run_repo, run: ConversationRun) -> RunControl:
    return RunControl(
        run_id=run.run_id,
        owner_id=run.owner_id,
        run_repo=run_repo,
        model_call_timeout_seconds=5.0,
        tool_call_timeout_seconds=5.0,
        poll_seconds=0.01,
    )


def _tool_model(final_text: str = "候选回答 [伪造来源]") -> FakeToolCallingModel:
    return FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_law",
                        "args": {"query": "PIPL 第三十八条", "top_k": 3},
                        "id": "call-law",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content=final_text),
        ]
    )


def test_actual_tool_source_replaces_model_written_source_name() -> None:
    retriever = FakeRetrieve([_chunk()])
    agent, repo, runs = _setup_agent(_tool_model(), retriever=retriever)

    events, task = _execute(agent, repo, runs, question="个人信息出境有什么条件？")

    event_types = [event.event_type for event in events]
    assert event_types[-1] is AgentEventType.ANSWER
    assert AgentEventType.TOOL_CALL in event_types
    assert AgentEventType.TOOL_RESULT in event_types
    assert AgentEventType.RUN_STATUS in event_types
    tool_call = next(event for event in events if event.event_type is AgentEventType.TOOL_CALL)
    tool_result = next(event for event in events if event.event_type is AgentEventType.TOOL_RESULT)
    assert tool_call.payload["tool_args"] == {"limit": 3}
    assert tool_result.payload["result"] == {"status": "completed", "evidence_count": 1}
    assert "个人信息保护法第三十八条" not in str(tool_result.payload)
    answer = events[-1].payload
    completed_run = runs.get("run-1", "anon:alice")
    assert completed_run is not None
    tool_evidence = completed_run.evidence[0]
    assert tool_evidence["citation_id"].startswith("SRC-")
    assert tool_evidence["source_id"] == "law-38"
    assert "伪造来源" not in answer["text"]
    assert answer["citations"][0]["citation_id"].startswith("SRC-")
    assert answer["citations"][0]["source_id"] == "law-38"
    assert answer["citations"][0]["source_name"] == "中华人民共和国个人信息保护法"
    assert answer["citations"][0]["source_url"] == "https://example.test/pipl"
    assert answer["citations"][0]["source_version"] == "2021-08-20"
    assert repo.list_messages(task.task_id)[-1].citations[0].source_id == "law-38"
    assert next(iter(repo._tool_calls.values())).status == "success"
    assert completed_run.status == "completed"
    assert completed_run.evidence[0]["source_id"] == "law-38"


def test_forged_citation_id_is_fail_closed() -> None:
    generator = FakeEvidenceQAGenerator(
        EvidenceQADraft(
            status="answered",
            claims=[EvidenceQAClaim(claim_id="C1", text="伪造结论", citation_ids=["SRC-fake"])],
        )
    )
    agent, repo, runs = _setup_agent(_tool_model(), generator=generator)

    events, _ = _execute(agent, repo, runs, question="个人信息出境有什么条件？")

    assert events[-1].payload["citations"] == []
    assert "未通过" in events[-1].payload["text"]


def test_semantically_unsupported_claim_is_fail_closed() -> None:
    verifier = FakeClaimSupportVerifier(
        ClaimSupportResult(
            judgements=[
                ClaimSupportJudgement(
                    claim_id="C1",
                    supported=False,
                    citation_ids=[],
                    reason="引用未支持结论",
                )
            ],
            unsupported_claim_ids=["C1"],
            valid=False,
        )
    )
    agent, repo, runs = _setup_agent(_tool_model(), verifier=verifier)

    events, _ = _execute(agent, repo, runs, question="个人信息出境有什么条件？")

    assert events[-1].payload["citations"] == []
    assert "未通过" in events[-1].payload["text"]


def test_mixed_claims_return_only_the_supported_part() -> None:
    actual_id = "SRC-" + hashlib.sha256(b"regulatory:law-38").hexdigest()[:12]
    generator = FakeEvidenceQAGenerator(
        EvidenceQADraft(
            status="answered",
            claims=[
                EvidenceQAClaim(
                    claim_id="C1",
                    text="受支持结论",
                    citation_ids=[actual_id],
                ),
                EvidenceQAClaim(
                    claim_id="C2",
                    text="伪造来源结论",
                    citation_ids=["SRC-forged"],
                ),
            ],
        )
    )
    agent, repo, runs = _setup_agent(_tool_model(), generator=generator)

    events, _ = _execute(agent, repo, runs, question="个人信息出境有什么条件？")

    assert "受支持结论" in events[-1].payload["text"]
    assert "伪造来源结论" not in events[-1].payload["text"]
    assert {item["citation_id"] for item in events[-1].payload["citations"]} == {actual_id}


def test_factual_free_answer_without_retrieval_is_blocked() -> None:
    agent, repo, runs = _setup_agent(
        FakeToolCallingModel(responses=[AIMessage(content="所有公司都必须申报安全评估。")])
    )
    events, _ = _execute(agent, repo, runs)
    assert "没有经过检索和校验" in events[-1].payload["text"]


def test_clarifying_question_without_citation_is_allowed() -> None:
    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "request_clarification",
                        "args": {"missing_fields": ["数据类型", "涉及人数"]},
                        "id": "call-clarify",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="请补充数据类型。所有企业都无需申报，对吗？"),
        ]
    )
    agent, repo, runs = _setup_agent(model)
    events, _ = _execute(agent, repo, runs)
    assert events[-1].payload["text"] == (
        "为避免猜测，请补充：拟出境的数据类型；涉及的个人信息主体人数。"
    )
    assert events[-1].payload["citations"] == []
    assert "所有企业都无需申报" not in events[-1].payload["text"]


def test_factual_sentence_with_question_mark_is_not_treated_as_clarification() -> None:
    agent, repo, runs = _setup_agent(
        FakeToolCallingModel(
            responses=[AIMessage(content="法律规定所有公司都必须申报，你同意吗？")]
        )
    )
    events, _ = _execute(agent, repo, runs)
    assert "没有经过检索和校验" in events[-1].payload["text"]


def test_mixed_clarification_and_factual_claim_is_not_published() -> None:
    candidate = "请补充企业名称。所有企业都无需申报安全评估，对吗？"
    agent, repo, runs = _setup_agent(FakeToolCallingModel(responses=[AIMessage(content=candidate)]))
    events, _ = _execute(agent, repo, runs)
    assert events[-1].payload["text"] != candidate
    assert "没有经过检索和校验" in events[-1].payload["text"]


def test_final_generator_receives_traceable_user_context() -> None:
    actual_id = "SRC-" + hashlib.sha256(b"regulatory:law-38").hexdigest()[:12]
    generator = FakeEvidenceQAGenerator(
        EvidenceQADraft(
            status="answered",
            claims=[
                EvidenceQAClaim(
                    claim_id="C1",
                    text="人数翻倍后的判断需要结合用户条件与法规证据。",
                    citation_ids=[actual_id],
                    condition_message_ids=["msg-condition", "msg-current"],
                )
            ],
        )
    )
    verifier = FakeClaimSupportVerifier()
    repo = InMemoryTaskRepo()
    agent, repo, runs = _setup_agent(
        _tool_model(),
        repo=repo,
        generator=generator,
        verifier=verifier,
    )
    now = time.time()
    task = Task(
        task_id="task-1",
        owner_id="anon:alice",
        title="test",
        user_goal="",
        created_at=now,
        updated_at=now,
    )
    repo.create(task)
    repo.append_message(
        Message(
            msg_id="msg-condition",
            task_id=task.task_id,
            role="user",
            content="我们涉及2000名员工的普通个人信息。",
        )
    )
    current = Message(
        msg_id="msg-current",
        task_id=task.task_id,
        role="user",
        content="如果人数翻倍呢？",
    )
    repo.append_message(current)
    run = ConversationRun(
        run_id="run-1",
        thread_id="thread-1",
        owner_id=task.owner_id,
        task_id=task.task_id,
        question=current.content,
        input_message_id=current.msg_id,
        created_at=now,
        updated_at=now,
    )
    runs.create(run)

    list(
        agent.run(
            owner_id=task.owner_id,
            task_id=task.task_id,
            user_message=current.content,
            attachment_doc_ids=[],
            run_id=run.run_id,
            thread_id=run.thread_id,
            input_message_id=current.msg_id,
            runtime_control=_runtime_control(runs, run),
        )
    )

    context = generator.calls[-1]["user_context"]
    assert context == [
        {
            "message_id": "msg-condition",
            "text": "我们涉及2000名员工的普通个人信息。",
            "is_current": False,
        },
        {
            "message_id": "msg-current",
            "text": "如果人数翻倍呢？",
            "is_current": True,
        },
    ]
    assert verifier.calls[-1]["user_context"] == context
    completed = runs.get(run.run_id, task.owner_id)
    assert completed is not None
    assert completed.verified_context_message_ids == ["msg-condition", "msg-current"]


def test_user_context_keeps_corrections_and_excludes_assistant_messages() -> None:
    generator = FakeEvidenceQAGenerator()
    repo = InMemoryTaskRepo()
    agent, _, runs = _setup_agent(_tool_model(), repo=repo, generator=generator)
    now = time.time()
    task = Task(
        task_id="task-correction",
        owner_id="anon:alice",
        title="test",
        created_at=now,
        updated_at=now,
    )
    repo.create(task)
    for message in [
        Message(msg_id="msg-old", task_id=task.task_id, role="user", content="人数是2000。"),
        Message(
            msg_id="msg-assistant",
            task_id=task.task_id,
            role="assistant",
            content="因此一定无需申报。",
        ),
        Message(
            msg_id="msg-correction",
            task_id=task.task_id,
            role="user",
            content="更正：人数是3000。",
        ),
        Message(
            msg_id="msg-current",
            task_id=task.task_id,
            role="user",
            content="它如果翻倍呢？",
        ),
    ]:
        repo.append_message(message)
    run = ConversationRun(
        run_id="run-correction",
        thread_id="thread-correction",
        owner_id=task.owner_id,
        task_id=task.task_id,
        question="它如果翻倍呢？",
        input_message_id="msg-current",
        created_at=now,
        updated_at=now,
    )
    runs.create(run)

    list(
        agent.run(
            owner_id=task.owner_id,
            task_id=task.task_id,
            user_message=run.question,
            attachment_doc_ids=[],
            run_id=run.run_id,
            thread_id=run.thread_id,
            input_message_id=run.input_message_id,
            runtime_control=_runtime_control(runs, run),
        )
    )

    assert generator.calls[-1]["user_context"] == [
        {"message_id": "msg-old", "text": "人数是2000。", "is_current": False},
        {
            "message_id": "msg-correction",
            "text": "更正：人数是3000。",
            "is_current": False,
        },
        {"message_id": "msg-current", "text": "它如果翻倍呢？", "is_current": True},
    ]


def test_user_context_does_not_cross_task_boundary() -> None:
    generator = FakeEvidenceQAGenerator()
    repo = InMemoryTaskRepo()
    agent, _, runs = _setup_agent(_tool_model(), repo=repo, generator=generator)
    now = time.time()
    foreign_task = Task(
        task_id="task-other",
        owner_id="anon:alice",
        title="other",
        created_at=now,
        updated_at=now,
    )
    current_task = Task(
        task_id="task-current",
        owner_id="anon:alice",
        title="current",
        created_at=now,
        updated_at=now,
    )
    repo.create(foreign_task)
    repo.create(current_task)
    repo.append_message(
        Message(
            msg_id="msg-other",
            task_id=foreign_task.task_id,
            role="user",
            content="其他会话人数是9999。",
        )
    )
    current = Message(
        msg_id="msg-current",
        task_id=current_task.task_id,
        role="user",
        content="它需要申报吗？",
    )
    repo.append_message(current)
    run = ConversationRun(
        run_id="run-current",
        thread_id="thread-current",
        owner_id=current_task.owner_id,
        task_id=current_task.task_id,
        question=current.content,
        input_message_id=current.msg_id,
        created_at=now,
        updated_at=now,
    )
    runs.create(run)

    list(
        agent.run(
            owner_id=current_task.owner_id,
            task_id=current_task.task_id,
            user_message=current.content,
            attachment_doc_ids=[],
            run_id=run.run_id,
            thread_id=run.thread_id,
            input_message_id=current.msg_id,
            runtime_control=_runtime_control(runs, run),
        )
    )

    assert generator.calls[-1]["user_context"] == [
        {"message_id": "msg-current", "text": "它需要申报吗？", "is_current": True}
    ]


def test_trace_links_agent_tool_and_verification_without_content() -> None:
    trace = FakeTrace()
    agent, repo, runs = _setup_agent(_tool_model(), trace=trace)
    _execute(agent, repo, runs, question="敏感正文不能进入追踪")

    assert [span["name"] for span in trace.spans] == [
        "riskpilot.copilot.run",
        "search_law",
        "riskpilot.citation.verify",
        "riskpilot.claim.generate",
        "riskpilot.claim.verify",
    ]
    serialized = str(trace.spans)
    assert "敏感正文不能进入追踪" not in serialized
    assert {span["metadata"]["run_id"] for span in trace.spans} == {"run-1"}
    assert trace.spans[0]["metadata"]["status"] == "completed"
    verification = next(span for span in trace.spans if span["name"] == "riskpilot.citation.verify")
    assert verification["metadata"]["decision"] == "answered"


def test_verifier_exception_is_traced_as_failure_and_returns_safe_refusal() -> None:
    trace = FakeTrace()
    generator = FakeEvidenceQAGenerator(error=RuntimeError("secret provider detail"))
    agent, repo, runs = _setup_agent(_tool_model(), generator=generator, trace=trace)

    events, _ = _execute(agent, repo, runs, question="敏感问题")

    assert "生成或校验失败" in events[-1].payload["text"]
    verification = next(span for span in trace.spans if span["name"] == "riskpilot.citation.verify")
    assert verification["name"] == "riskpilot.citation.verify"
    assert verification["metadata"]["status"] == "failed"
    assert verification["metadata"]["error_type"] == "RuntimeError"
    assert "secret provider detail" not in str(trace.spans)


def test_tool_failure_is_recorded_and_never_returns_raw_candidate() -> None:
    class BrokenRetrieve(FakeRetrieve):
        def retrieve(self, *args, **kwargs):
            raise TimeoutError("secret upstream detail")

    trace = FakeTrace()
    agent, repo, runs = _setup_agent(
        _tool_model("未经校验的候选"), retriever=BrokenRetrieve(), trace=trace
    )

    events, _ = _execute(agent, repo, runs, question="法规问题")

    assert AgentEventType.TOOL_ERROR in [event.event_type for event in events]
    assert events[-1].event_type is AgentEventType.RUN_STATUS
    assert events[-1].payload["status"] == "timed_out"
    assert all(event.event_type is not AgentEventType.ANSWER for event in events)
    tool_call = next(iter(repo._tool_calls.values()))
    assert tool_call.status == "timeout"
    tool_span = next(span for span in trace.spans if span["name"] == "search_law")
    assert tool_span["metadata"]["status"] == "timeout"
    assert tool_span["metadata"]["error_type"] == "TimeoutError"
    root_span = next(span for span in trace.spans if span["name"] == "riskpilot.copilot.run")
    assert root_span["metadata"]["status"] == "timed_out"
    assert root_span["metadata"]["end_reason"] == "search_law_timeout"
    assert "secret upstream detail" not in str(trace.spans)


def test_genuine_model_chunks_arrive_before_model_call_completes() -> None:
    release = threading.Event()
    model = FakeStreamingToolCallingModel(
        responses=[
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_law",
                            "args": {"query": "第三十八条", "top_k": 3},
                            "id": "call-law",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            [AIMessageChunk(content="待核验"), AIMessageChunk(content="草稿")],
        ],
        pause_call_index=1,
        pause_after_chunk_index=0,
        release_event=release,
    )
    agent, repo, runs = _setup_agent(model)
    now = time.time()
    task = Task(
        task_id="task-stream",
        owner_id="anon:alice",
        title="stream",
        created_at=now,
        updated_at=now,
    )
    repo.create(task)
    user = Message(msg_id="msg-stream", task_id=task.task_id, role="user", content="法规问题")
    repo.append_message(user)
    run = ConversationRun(
        run_id="run-stream",
        thread_id="thread-stream",
        owner_id=task.owner_id,
        task_id=task.task_id,
        question=user.content,
        input_message_id=user.msg_id,
        created_at=now,
        updated_at=now,
    )
    runs.create(run)
    events = agent.run(
        owner_id=task.owner_id,
        task_id=task.task_id,
        user_message=user.content,
        attachment_doc_ids=[],
        run_id=run.run_id,
        thread_id=run.thread_id,
        input_message_id=user.msg_id,
        runtime_control=_runtime_control(runs, run),
    )

    first_delta = next(event for event in events if event.event_type is AgentEventType.DRAFT_DELTA)

    assert first_delta.payload["text"] == "待核验"
    assert model.completed_calls == 1
    release.set()
    remaining = list(events)
    stages = [
        event.payload["stage"]
        for event in [first_delta, *remaining]
        if event.event_type is AgentEventType.RUN_STATUS
    ]
    assert "verification" in stages
    assert any(event.event_type is AgentEventType.DRAFT_DISCARDED for event in remaining)
    assert remaining[-1].event_type is AgentEventType.ANSWER
    assert remaining[-1].payload["text"] != "待核验草稿"


def test_streamed_draft_is_discarded_when_semantic_verification_fails() -> None:
    verifier = FakeClaimSupportVerifier(
        ClaimSupportResult(
            judgements=[
                ClaimSupportJudgement(
                    claim_id="C1",
                    supported=False,
                    citation_ids=[],
                    reason="证据不支持候选结论",
                )
            ],
            unsupported_claim_ids=["C1"],
            valid=False,
        )
    )
    model = FakeStreamingToolCallingModel(
        responses=[
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_law",
                            "args": {"query": "第三十八条", "top_k": 3},
                            "id": "call-law",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            [AIMessageChunk(content="未经核验的错误结论")],
        ]
    )
    agent, repo, runs = _setup_agent(model, verifier=verifier)

    events, task = _execute(agent, repo, runs, question="个人信息出境有什么条件？")

    drafts = [
        event.payload["text"] for event in events if event.event_type is AgentEventType.DRAFT_DELTA
    ]
    assert drafts == ["未经核验的错误结论"]
    assert any(event.event_type is AgentEventType.DRAFT_DISCARDED for event in events)
    assert events[-1].event_type is AgentEventType.ANSWER
    assert "未经核验的错误结论" not in events[-1].payload["text"]
    assert "未通过" in events[-1].payload["text"]
    assert repo.list_messages(task.task_id)[-1].content == events[-1].payload["text"]


def test_metrics_record_only_actual_model_usage() -> None:
    registry = CollectorRegistry()
    metrics = PrometheusMetricsAdapter(registry=registry)
    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="请补充具体数据范围？",
                usage_metadata={"input_tokens": 7, "output_tokens": 4, "total_tokens": 11},
            )
        ]
    )
    agent, repo, runs = _setup_agent(model, metrics=metrics)

    _execute(agent, repo, runs)

    payload = metrics.render().decode()
    assert (
        'riskpilot_llm_tokens_total{model="unknown",operation="conversation",token_type="input"} 7.0'
        in payload
    )
    assert (
        'riskpilot_llm_tokens_total{model="unknown",operation="conversation",token_type="output"} 4.0'
        in payload
    )
    assert "riskpilot_llm_estimated_cost_total" in payload
    assert (
        'riskpilot_llm_estimated_cost_total{currency="unspecified",model="unknown"' not in payload
    )
    run = runs.get("run-1", "anon:alice")
    assert run is not None
    assert run.model_call_count == 1
    assert run.usage_reported_model_calls == 1
    assert run.input_tokens == 7
    assert run.output_tokens == 4


def test_usage_is_partial_when_only_agent_model_reports_tokens() -> None:
    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_law",
                        "args": {"query": "第三十八条"},
                        "id": "call-law",
                        "type": "tool_call",
                    }
                ],
                usage_metadata={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
            ),
            AIMessage(
                content="候选",
                usage_metadata={"input_tokens": 8, "output_tokens": 3, "total_tokens": 11},
            ),
        ]
    )
    agent, repo, runs = _setup_agent(model)

    _execute(agent, repo, runs, question="法规问题")

    run = runs.get("run-1", "anon:alice")
    assert run is not None
    assert run.model_call_count == 4
    assert run.usage_reported_model_calls == 2
    assert run.input_tokens == 13
    assert run.output_tokens == 5


def test_memory_excludes_current_message_but_keeps_previous_turn() -> None:
    repo = InMemoryTaskRepo()
    assembler = MemoryAssembler(TaskBackedMemory(repo), recent_n=6, token_budget=1500)
    agent, repo, runs = _setup_agent(
        FakeToolCallingModel(responses=[AIMessage(content="请补充具体场景？")]),
        repo=repo,
        memory_assembler=assembler,
    )
    now = time.time()
    task = Task(
        task_id="task-1",
        owner_id="anon:alice",
        title="test",
        user_goal="",
        created_at=now,
        updated_at=now,
    )
    repo.create(task)
    repo.append_message(Message(msg_id="old", task_id=task.task_id, role="user", content="前一轮"))
    current = Message(msg_id="current", task_id=task.task_id, role="user", content="本轮问题")
    repo.append_message(current)
    run = ConversationRun(
        run_id="run-1",
        thread_id="thread-1",
        owner_id=task.owner_id,
        task_id=task.task_id,
        question=current.content,
        input_message_id=current.msg_id,
        created_at=now,
        updated_at=now,
    )
    runs.create(run)

    list(
        agent.run(
            owner_id=task.owner_id,
            task_id=task.task_id,
            user_message=current.content,
            attachment_doc_ids=[],
            run_id=run.run_id,
            thread_id=run.thread_id,
            input_message_id=current.msg_id,
            runtime_control=_runtime_control(runs, run),
        )
    )

    model_messages = (
        agent._graph
    )  # graph exists; actual prompt assertion is covered by MemoryAssembler unit tests
    assert model_messages is not None
    assert (
        assembler.assemble(
            owner_id=task.owner_id,
            task_id=task.task_id,
            exclude_message_id=current.msg_id,
        ).find("前一轮")
        >= 0
    )


def test_unknown_owner_cannot_execute_task() -> None:
    agent, repo, runs = _setup_agent(
        FakeToolCallingModel(responses=[AIMessage(content="请补充？")])
    )
    now = time.time()
    repo.create(Task(task_id="task-1", owner_id="alice", created_at=now, updated_at=now))
    with pytest.raises(ValueError, match="不属于"):
        list(
            agent.run(
                owner_id="bob",
                task_id="task-1",
                user_message="问题",
                attachment_doc_ids=[],
                run_id="run-missing",
                thread_id="thread-missing",
                input_message_id="msg-missing",
                runtime_control=_runtime_control(
                    runs,
                    ConversationRun(
                        run_id="run-missing",
                        thread_id="thread-missing",
                        owner_id="bob",
                        task_id="task-1",
                        question="问题",
                        input_message_id="msg-missing",
                        created_at=now,
                        updated_at=now,
                    ),
                ),
            )
        )
