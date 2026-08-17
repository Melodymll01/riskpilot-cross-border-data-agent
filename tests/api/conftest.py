"""API v2 测试共享 fixtures。

策略：
- container：全 Fake 注入的 AppContainer（不连 SQLite/openai/网络）
- app：FastAPI + ``build_v2_router(container)`` + ``install_exception_handlers``
- client：TestClient（自动持 cookie，多轮请求保持 session）
- authed_client：先调 /auth/anonymous，后续请求自动带 owner cookie
- chat_responses fixture：让单测灵活注入 FakeChat 决策序列
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from api.v2 import build_v2_router
from api.v2.errors import install_exception_handlers
from api.v3 import build_v3_router
from app.container import AppContainer
from app.request_context import install_request_id_middleware
from config import Settings
from infra.workflows import LangGraphWorkflowRuntime
from tests.fakes.fake_agent_model import FakeToolCallingModel
from tests.fakes.fake_audit_log import FakeAuditLogRepo
from tests.fakes.fake_auth import FakeAuth
from tests.fakes.fake_chat import FakeChat
from tests.fakes.fake_document_loader import FakeDocumentLoader
from tests.fakes.fake_document_parser import FakeDocumentParser
from tests.fakes.fake_embed import FakeEmbed
from tests.fakes.fake_evidence_chunker import FakeEvidenceChunker
from tests.fakes.fake_evidence_index import FakeEvidenceIndex
from tests.fakes.fake_fact_proposals import FakeFactProposalGenerator
from tests.fakes.fake_kb_repo import FakeKbRepo
from tests.fakes.fake_object_store import FakeObjectStore
from tests.fakes.fake_qa import FakeClaimSupportVerifier, FakeEvidenceQAGenerator
from tests.fakes.fake_readiness import FakeReadiness
from tests.fakes.fake_repos import (
    InMemoryAgentRunRepo,
    InMemoryAssessmentRepo,
    InMemoryCaseFactRepo,
    InMemoryCaseRepo,
    InMemoryDocumentRepo,
    InMemoryPolicyRuleRepo,
    InMemoryTaskRepo,
    InMemoryUserRepo,
    InMemoryWorkspaceRepo,
)
from tests.fakes.fake_research import FakeResearch
from tests.fakes.fake_retrieve import FakeRetrieve
from tests.fakes.fake_risk_profile import FakeRiskProfile
from tests.fakes.fake_visual import FakeVisualEmbedder, FakeVisualIndex
from tests.fakes.fake_websearch import FakeWebSearch


@pytest.fixture
def admin_user_ids() -> list[str]:
    """允许测试类用同名 fixture override 注入 admin 列表。默认空。"""
    return []


@pytest.fixture
def test_settings(admin_user_ids: list[str]) -> Settings:
    """构造测试用 Settings；用 _env_file=None 防止读项目里的 .env。"""
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        openai_api_key="sk-test-fake",
        openai_api_base="http://127.0.0.1:9/v1",
        chat_api_key="sk-test-fake",
        chat_api_base="http://127.0.0.1:9/v1",
        enable_reranker=False,
        admin_user_ids=admin_user_ids,
    )


@pytest.fixture
def agent_script() -> list[AIMessage]:
    """单测可覆盖此 fixture 注入标准 LangChain AIMessage 序列。

    默认：直接回答。
    """
    return [AIMessage(content="done")]


@pytest.fixture
def container(
    test_settings: Settings,
    agent_script: list[AIMessage],
    tmp_path: Path,
) -> AppContainer:
    """全 Fake 注入的 AppContainer。"""
    document_repo = InMemoryDocumentRepo()
    case_repo = InMemoryCaseRepo()
    return AppContainer(
        test_settings,
        agent_run_repo=InMemoryAgentRunRepo(),
        assessment_repo=InMemoryAssessmentRepo(case_repo),
        user_repo=InMemoryUserRepo(),
        task_repo=InMemoryTaskRepo(),
        workspace_repo=InMemoryWorkspaceRepo(),
        case_repo=case_repo,
        case_fact_repo=InMemoryCaseFactRepo(),
        policy_rule_repo=InMemoryPolicyRuleRepo(),
        document_repo=document_repo,
        object_store=FakeObjectStore(),
        document_parser=FakeDocumentParser(),
        evidence_chunker=FakeEvidenceChunker(),
        evidence_index=FakeEvidenceIndex(document_repo),
        workflow_runtime=LangGraphWorkflowRuntime(str(tmp_path / "langgraph-checkpoints.sqlite3")),
        audit_log=FakeAuditLogRepo(),
        embedder=FakeEmbed(),
        chat=FakeChat(responses=["done"]),
        evidence_qa_generator=FakeEvidenceQAGenerator(),
        claim_support_verifier=FakeClaimSupportVerifier(),
        fact_proposal_generator=FakeFactProposalGenerator(),
        retriever=FakeRetrieve(),
        web_search=FakeWebSearch(),
        risk_profile=FakeRiskProfile(),
        readiness=FakeReadiness(),
        research=FakeResearch(),
        kb_repo=FakeKbRepo(),
        document_loader=FakeDocumentLoader(),
        auth=FakeAuth(),
        agent_model=FakeToolCallingModel(responses=agent_script),
        visual_index=FakeVisualIndex(),
        visual_embedder=FakeVisualEmbedder(),
    )


@pytest.fixture
def app(container: AppContainer) -> FastAPI:
    fastapi_app = FastAPI()
    # Step 025d：挂 request_id middleware，确保端到端测试的 contextvar 与生产一致
    install_request_id_middleware(fastapi_app)
    fastapi_app.include_router(build_v2_router(container), prefix="/api/v2")
    fastapi_app.include_router(build_v3_router(container), prefix="/api/v3")
    install_exception_handlers(fastapi_app)
    # 暴露 container，方便测试里直接读 repo / fakes 验证副作用
    fastapi_app.state.container = container
    return fastapi_app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def authed_client(client: TestClient) -> tuple[TestClient, dict[str, Any]]:
    """已通过 /auth/anonymous 拿到 cookie 的 client + 用户信息。"""
    resp = client.post("/api/v2/auth/anonymous")
    assert resp.status_code == 201, resp.text
    return client, resp.json()["user"]
