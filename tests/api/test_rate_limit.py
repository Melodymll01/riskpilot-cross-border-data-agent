"""v2 HTTP 限流测试（基于 ``limits`` + FastAPI 依赖）。

策略：
- 不复用 conftest 的 ``app`` fixture（那里不挂 limiter）；本文件自建带 limiter 的 app，
  用很小的限额（如 ``2/minute``）触发 429。
- key_func 在测试里回退到来源 IP（``install_request_id_middleware`` 不写
  ``request.state.user_id``），所有请求共享 ``ip:testclient`` 一个桶，限额稳定触发。
"""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v2 import build_v2_router
from api.v2.errors import install_exception_handlers
from api.v2.ratelimit import RateLimiter, build_limiter
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
from tests.fakes.fake_object_store import FakeObjectStore
from tests.fakes.fake_repos import (
    InMemoryCaseRepo,
    InMemoryDocumentRepo,
    InMemoryTaskRepo,
    InMemoryUserRepo,
    InMemoryWorkspaceRepo,
)
from tests.fakes.fake_retrieve import FakeRetrieve
from tests.fakes.fake_websearch import FakeWebSearch

_FINAL_JSON = json.dumps({"thought": "", "action": "final_answer", "answer": "done"})


def _make_container(**overrides: object) -> AppContainer:
    settings = Settings(_env_file=None, **overrides)  # type: ignore[arg-type]
    return AppContainer(
        settings,
        user_repo=InMemoryUserRepo(),
        task_repo=InMemoryTaskRepo(),
        workspace_repo=InMemoryWorkspaceRepo(),
        case_repo=InMemoryCaseRepo(),
        document_repo=InMemoryDocumentRepo(),
        object_store=FakeObjectStore(),
        audit_log=FakeAuditLogRepo(),
        embedder=FakeEmbed(),
        chat=FakeChat(responses=[_FINAL_JSON, _FINAL_JSON, _FINAL_JSON, _FINAL_JSON]),
        retriever=FakeRetrieve(),
        web_search=FakeWebSearch(),
        evidence=FakeEvidence(),
        kb_repo=FakeKbRepo(),
        document_loader=FakeDocumentLoader(),
        auth=FakeAuth(),
    )


def _build_limited_app(container: AppContainer) -> FastAPI:
    """复刻 main.py 的 limiter 装配（依赖注入式；超额由依赖抛 HTTPException(429)）。"""
    app = FastAPI()
    install_request_id_middleware(app)
    limiter = build_limiter(container.settings)
    assert limiter is not None  # 这些测试都要求 rate_limit_enabled=True
    app.include_router(build_v2_router(container, limiter=limiter), prefix="/api/v2")
    install_exception_handlers(app)
    app.state.container = container
    return app


# ── 单元：工厂 / 依赖 ──────────────────────────────────────────────────────


class TestBuildLimiter:
    def test_disabled_returns_none(self) -> None:
        settings = Settings(_env_file=None, rate_limit_enabled=False)  # type: ignore[call-arg]
        assert build_limiter(settings) is None

    def test_enabled_returns_limiter(self) -> None:
        settings = Settings(_env_file=None, rate_limit_enabled=True)  # type: ignore[call-arg]
        limiter = build_limiter(settings)
        assert isinstance(limiter, RateLimiter)

    def test_dependency_returns_callable(self) -> None:
        limiter = RateLimiter()
        dep = limiter.dependency("5/minute")
        assert callable(dep)


# ── 端到端：限额触发 429 ──────────────────────────────────────────────────


class TestAnonymousRateLimit:
    def test_default_limit_triggers_429(self) -> None:
        container = _make_container(rate_limit_default="2/minute")
        app = _build_limited_app(container)
        with TestClient(app) as client:
            r1 = client.post("/api/v2/auth/anonymous")
            r2 = client.post("/api/v2/auth/anonymous")
            r3 = client.post("/api/v2/auth/anonymous")
        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r3.status_code == 429

    def test_429_body_shape(self) -> None:
        container = _make_container(rate_limit_default="1/minute")
        app = _build_limited_app(container)
        with TestClient(app) as client:
            client.post("/api/v2/auth/anonymous")
            blocked = client.post("/api/v2/auth/anonymous")
        assert blocked.status_code == 429
        body = blocked.json()
        assert body["error_code"] == "RATE_LIMITED"
        assert "message" in body


class TestIngestRateLimit:
    def test_web_ingest_limit_triggers_429(self) -> None:
        container = _make_container(
            rate_limit_default="100/minute",
            rate_limit_ingest="2/minute",
        )
        app = _build_limited_app(container)
        with TestClient(app) as client:
            assert client.post("/api/v2/auth/anonymous").status_code == 201
            payload = {"url": "https://example.com/a", "category": "t"}
            s1 = client.post("/api/v2/documents/web", json=payload)
            s2 = client.post("/api/v2/documents/web", json=payload)
            s3 = client.post("/api/v2/documents/web", json=payload)
        # 限流依赖在 handler 之前计数：前两次放行（业务码与 fake 行为无关），第三次 429
        assert s1.status_code != 429
        assert s2.status_code != 429
        assert s3.status_code == 429


class TestCopilotRateLimit:
    def test_llm_limit_triggers_429(self) -> None:
        container = _make_container(
            rate_limit_default="100/minute",
            rate_limit_llm="2/minute",
        )
        app = _build_limited_app(container)
        with TestClient(app) as client:
            assert client.post("/api/v2/auth/anonymous").status_code == 201
            payload = {"message": "hi", "mode": "qa"}
            c1 = client.post("/api/v2/copilot/chat", json=payload)
            c2 = client.post("/api/v2/copilot/chat", json=payload)
            c3 = client.post("/api/v2/copilot/chat", json=payload)
        assert c1.status_code != 429
        assert c2.status_code != 429
        assert c3.status_code == 429


class TestRateLimitDisabled:
    def test_no_limiter_never_429(self) -> None:
        """limiter=None（rate_limit_enabled=False）时限流依赖为空，绝不 429。"""
        container = _make_container(rate_limit_enabled=False, rate_limit_default="1/minute")
        app = FastAPI()
        install_request_id_middleware(app)
        # 不挂 limiter，模拟生产 rate_limit_enabled=False 路径
        app.include_router(build_v2_router(container, limiter=None), prefix="/api/v2")
        install_exception_handlers(app)
        app.state.container = container
        with TestClient(app) as client:
            for _ in range(5):
                assert client.post("/api/v2/auth/anonymous").status_code == 201
