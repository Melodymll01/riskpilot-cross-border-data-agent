"""Browser acceptance against a real HTTP server with controlled model/tool adapters."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatResult

from api.v2 import build_v2_router
from api.v2.errors import install_exception_handlers
from app.container import AppContainer
from domain.models import Chunk
from tests.fakes import FakeRetrieve, FakeStreamingToolCallingModel, FakeToolCallingModel
from tests.infra.test_conversation_checkpoint_recovery import _container, _settings

CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


class SlowRetrieve(FakeRetrieve):
    def retrieve(self, *args, **kwargs):
        time.sleep(0.08)
        return super().retrieve(*args, **kwargs)


class ExplodingToolCallingModel(FakeToolCallingModel):
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        self.calls.append(list(messages))
        raise RuntimeError("controlled model failure")


def _free_port() -> int:
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _serve(container: AppContainer) -> Iterator[str]:
    app = FastAPI()
    app.state.container = container
    app.include_router(build_v2_router(container), prefix="/api/v2")
    install_exception_handlers(app)
    if container.settings.internal_service_required:
        from infra.auth.gateway_identity import GatewayIdentityMiddleware

        app.add_middleware(
            GatewayIdentityMiddleware,
            key_file=container.settings.internal_service_key_file,
            users=container.user_repo,
        )
    frontend = Path(__file__).resolve().parents[2] / "frontend"
    app.mount("/static", StaticFiles(directory=frontend), name="static")

    @app.get("/")
    def index():
        from fastapi.responses import FileResponse

        return FileResponse(frontend / "index.html")

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    deadline = time.time() + 5
    while not server.started and time.time() < deadline:
        time.sleep(0.01)
    assert server.started
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        server_thread.join(timeout=5)
        container.close()


@pytest.mark.skipif(not CHROME.exists(), reason="Google Chrome is not installed")
def test_browser_receives_real_draft_stream_then_verified_answer(tmp_path: Path) -> None:
    playwright = pytest.importorskip("playwright.sync_api")
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
            [AIMessageChunk(content="待核验首段"), AIMessageChunk(content="后续草稿")],
            [AIMessageChunk(content="不应发布的迟到正文")],
        ],
        pause_call_index=1,
        pause_after_chunk_index=0,
        release_event=release,
    )
    retriever = SlowRetrieve(
        [
            Chunk(
                chunk_id="law-38",
                text="个人信息保护法第三十八条规定了个人信息出境条件。",
                source_type="law",
                source_name="中华人民共和国个人信息保护法",
                title="第三十八条",
                source_url="https://example.test/pipl",
                score=0.9,
            )
        ]
    )
    container = _container(_settings(tmp_path), model, retriever=retriever)

    page_errors: list[str] = []
    try:
        with _serve(container) as base_url:
            with playwright.sync_playwright() as manager:
                browser = manager.chromium.launch(
                    executable_path=str(CHROME),
                    headless=True,
                    args=["--no-sandbox"],
                )
                page = browser.new_page()
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.goto(f"{base_url}/")
                page.wait_for_function("document.documentElement.dataset.appReady === 'true'")
                page.locator("#composer-input").fill("个人信息出境有什么条件？")
                page.locator("#btn-send").click()
                page.wait_for_selector(".answer-draft-text")
                assert page.locator(".answer-draft-text").text_content() == "待核验首段"
                assert page.locator("#run-status").text_content() == "正在生成待核验草稿"
                assert page.locator("#btn-stop").is_visible()
                assert page.locator(".tool-args").first.text_content() == "已提交受控工具请求"
                assert model.completed_calls == 1
                release.set()
                page.wait_for_selector(".answer")
                page.wait_for_function("!document.querySelector('.answer-draft')")
                assert "证据支持该结论" in page.locator(".answer").last.text_content()
                assert "SRC-" in page.locator(".citation-location").last.text_content()
                assert page.locator("#run-status").text_content() == "已完成"
                assert not page.locator("#btn-stop").is_visible()

                second_started = threading.Event()
                release.clear()
                model.pause_call_index = 2
                model.pause_after_chunk_index = None
                model.pause_before_first_chunk = True
                model.call_started_event = second_started
                page.locator("#btn-new-task").click()
                page.locator("#composer-input").fill("停止这个慢请求")
                page.locator("#btn-send").click()
                assert second_started.wait(timeout=2.0)
                page.wait_for_selector("#btn-stop:not(.hidden)")
                page.locator("#btn-stop").click()
                page.wait_for_function(
                    "document.querySelector('#run-status').textContent === '已取消'"
                )
                assert page.locator(".answer").count() == 0
                assert not page.locator("#btn-stop").is_visible()
                release.set()
                browser.close()
    finally:
        release.set()

    assert page_errors == []


@pytest.mark.skipif(not CHROME.exists(), reason="Google Chrome is not installed")
def test_browser_safe_failure_answer_does_not_override_failed_run(tmp_path: Path) -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    model = ExplodingToolCallingModel(responses=[AIMessage(content="unused")])
    container = _container(_settings(tmp_path), model)
    user = container.auth.create_anonymous()
    container.user_repo.upsert(user)
    token = container.auth.issue_jwt(user.user_id)
    page_errors: list[str] = []

    with _serve(container) as base_url:
        with playwright.sync_playwright() as manager:
            browser = manager.chromium.launch(
                executable_path=str(CHROME),
                headless=True,
                args=["--no-sandbox"],
            )
            context = browser.new_context()
            context.add_cookies(
                [
                    {
                        "name": container.settings.cookie_name,
                        "value": token,
                        "url": base_url,
                    }
                ]
            )
            page = context.new_page()
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.goto(f"{base_url}/")
            page.wait_for_function("document.documentElement.dataset.appReady === 'true'")
            page.locator("#composer-input").fill("触发可控模型异常")
            page.locator("#btn-send").click()

            page.wait_for_selector(".answer")
            page.wait_for_function("document.querySelector('#run-status').textContent === '失败'")
            assert "本轮执行失败" in page.locator(".answer").last.text_content()
            assert not page.locator("#btn-stop").is_visible()
            assert page.locator(".typing").count() == 0

            row = (
                container.pool.get()
                .execute(
                    "SELECT status, end_reason FROM conversation_runs "
                    "ORDER BY created_at DESC LIMIT 1"
                )
                .fetchone()
            )
            assert row["status"] == "failed"
            assert row["end_reason"] == "runtime_error"

            page.reload()
            page.wait_for_function("document.documentElement.dataset.appReady === 'true'")
            assert page.locator("#run-status").text_content() == "失败"
            assert "本轮执行失败" in page.locator(".answer").last.text_content()
            assert not page.locator("#btn-stop").is_visible()
            browser.close()

    assert page_errors == []
