"""API v2 测试共享 fixtures。

策略：
- container：全 Fake 注入的 AppContainer（不连 SQLite/openai/网络）
- app：FastAPI + ``build_v2_router(container)`` + ``install_exception_handlers``
- client：TestClient（自动持 cookie，多轮请求保持 session）
- authed_client：先调 /auth/anonymous，后续请求自动带 owner cookie
- chat_responses fixture：让单测灵活注入 FakeChat 决策序列
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v2 import build_v2_router
from api.v2.errors import install_exception_handlers
from api.v3 import build_v3_router
from app.container import AppContainer
from app.request_context import install_request_id_middleware
from config import Settings
from tests.fakes.fake_audit_log import FakeAuditLogRepo
from tests.fakes.fake_auth import FakeAuth
from tests.fakes.fake_chat import FakeChat
from tests.fakes.fake_document_loader import FakeDocumentLoader
from tests.fakes.fake_embed import FakeEmbed
from tests.fakes.fake_evidence import FakeEvidence
from tests.fakes.fake_kb_repo import FakeKbRepo
from tests.fakes.fake_repos import (
    InMemoryCaseRepo,
    InMemoryTaskRepo,
    InMemoryUserRepo,
    InMemoryWorkspaceRepo,
)
from tests.fakes.fake_retrieve import FakeRetrieve
from tests.fakes.fake_websearch import FakeWebSearch

_FINAL_JSON = json.dumps({"thought": "", "action": "final_answer", "answer": "done"})


@pytest.fixture
def admin_user_ids() -> list[str]:
    """允许测试类用同名 fixture override 注入 admin 列表。默认空。"""
    return []


@pytest.fixture
def test_settings(admin_user_ids: list[str]) -> Settings:
    """构造测试用 Settings；用 _env_file=None 防止读项目里的 .env。"""
    return Settings(_env_file=None, admin_user_ids=admin_user_ids)  # type: ignore[call-arg]


@pytest.fixture
def chat_script() -> list[str]:
    """单测可通过覆盖此 fixture 注入自定义 LLM 决策序列。

    默认：一步 final_answer。
    """
    return [_FINAL_JSON]


@pytest.fixture
def container(test_settings: Settings, chat_script: list[str]) -> AppContainer:
    """全 Fake 注入的 AppContainer。"""
    return AppContainer(
        test_settings,
        user_repo=InMemoryUserRepo(),
        task_repo=InMemoryTaskRepo(),
        workspace_repo=InMemoryWorkspaceRepo(),
        case_repo=InMemoryCaseRepo(),
        audit_log=FakeAuditLogRepo(),
        embedder=FakeEmbed(),
        chat=FakeChat(responses=chat_script),
        retriever=FakeRetrieve(),
        web_search=FakeWebSearch(),
        evidence=FakeEvidence(),
        kb_repo=FakeKbRepo(),
        document_loader=FakeDocumentLoader(),
        auth=FakeAuth(),
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
