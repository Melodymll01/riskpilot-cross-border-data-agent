"""Real browser/API coverage for account memory and the risk-analysis entry."""

from __future__ import annotations

import hashlib
import json
import time

import pytest
from langchain_core.messages import AIMessage

from domain.models import Chunk, KbChunk, User
from tests.browser.test_phase3_browser import CHROME, _serve
from tests.fakes import FakeChat, FakeRetrieve, FakeToolCallingModel
from tests.infra.test_conversation_checkpoint_recovery import _container, _settings

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="Google Chrome is not installed")


def _account(container, user_id="github:features"):
    user = User(
        user_id=user_id,
        provider="github",
        provider_id=user_id.rsplit(":", 1)[-1],
        display_name="Feature Test",
        created_at=time.time(),
        last_active_at=time.time(),
    )
    container.user_repo.upsert(user)
    return user.user_id, container.auth.issue_jwt(user.user_id)


def test_admin_can_open_public_knowledge_base(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    settings = _settings(tmp_path, admin_user_ids=["github:admin"])
    container = _container(settings, FakeToolCallingModel(responses=[AIMessage(content="done")]))
    container.kb_repo.add_chunks(
        [
            KbChunk(
                chunk_id="public-law-1",
                text="公开法规片段",
                source_name="pipl.pdf",
                source_type="file",
                title="个人信息保护法",
                source_url="https://example.test/pipl",
                chunk_index=0,
                category="法规",
                owner_id=None,
            ),
            KbChunk(
                chunk_id="public-law-2",
                text="公开法规片段二",
                source_name="pipl.pdf",
                source_type="file",
                title="个人信息保护法",
                source_url="https://example.test/pipl",
                chunk_index=1,
                category="法规",
                owner_id=None,
            ),
        ],
        [[1.0], [1.0]],
    )
    _, token = _account(container, "github:admin")
    with _serve(container) as base_url, playwright.sync_playwright() as manager:
        browser, page = _open(manager, base_url, container, token)
        try:
            page.locator("#btn-knowledge-base").click()
            page.wait_for_selector("#knowledge-base-view:not(.hidden)")
            page.wait_for_function(
                "document.querySelector('[data-kb-stat=\"documents\"]')?.textContent === '1'"
            )
            assert page.locator("#knowledge-base-title").text_content() == "知识库"
            assert page.locator('[data-kb-stat="documents"]').text_content() == "1"
            assert page.locator('[data-kb-stat="chunks"]').text_content() == "2"
            assert page.locator(".kb-document-name strong").text_content() == "个人信息保护法"
            assert page.locator(".kb-category-tag").text_content() == "法规"
            assert page.locator(".kb-owner").text_content() == "公共知识库"
            page.locator("#knowledge-base-add").click()
            assert page.locator("#knowledge-base-ingest-panel").is_visible()
            page.locator("#knowledge-base-category").select_option("案例")
            assert page.locator("#knowledge-base-category").input_value() == "案例"
            page.locator('[data-source="web"]').click()
            assert page.locator("#knowledge-base-url").is_visible()
            assert page.locator("#knowledge-base-web-submit").is_visible()
        finally:
            browser.close()


def _open(manager, base_url, container, token):
    browser = manager.chromium.launch(executable_path=str(CHROME), headless=True)
    context = browser.new_context()
    context.add_cookies([{"name": container.settings.cookie_name, "value": token, "url": base_url}])
    page = context.new_page()
    page.goto(base_url)
    page.wait_for_function("document.documentElement.dataset.appReady === 'true'")
    return browser, page


def test_attachment_entry_describes_question_scope(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    container = _container(
        _settings(tmp_path), FakeToolCallingModel(responses=[AIMessage(content="done")])
    )
    _, token = _account(container)
    with _serve(container) as base_url, playwright.sync_playwright() as manager:
        browser, page = _open(manager, base_url, container, token)
        try:
            page.set_viewport_size({"width": 2000, "height": 1024})
            assert "v=20261023-ui17" in page.locator('link[rel="stylesheet"]').get_attribute("href")
            assert "v=20261023-ui17" in page.locator('script[src^="/static/app.js"]').get_attribute(
                "src"
            )
            new_task_box = page.locator("#btn-new-task").bounding_box()
            assert new_task_box is not None and new_task_box["height"] < 60
            attachment_input_style = page.locator("#attachment-input").evaluate(
                "element => ({position: getComputedStyle(element).position, "
                "width: getComputedStyle(element).width, clip: getComputedStyle(element).clip})"
            )
            assert attachment_input_style["position"] == "absolute"
            assert attachment_input_style["width"] == "1px"
            assert attachment_input_style["clip"] != "auto"
            assert "添加企业材料" in page.locator(".attachment-picker").text_content()
            help_text = page.locator(".attachment-help").text_content()
            assert "当前对话的补充信息" in help_text
            assert "不会更新公共法规库" in help_text
            assert "仅当前账号可访问" in help_text
            assert page.locator("#attachment-clear").is_hidden()
            assert page.locator("#btn-knowledge-base").is_hidden()
            page.locator("#btn-user-menu").click()
            assert page.locator("#btn-github-login").is_hidden()

            page.set_viewport_size({"width": 390, "height": 844})
            page.locator("#btn-sidebar-toggle").click()
            page.wait_for_function("document.body.classList.contains('sidebar-open')")
            assert page.locator("#btn-sidebar-toggle").get_attribute("aria-expanded") == "true"
            page.locator("#btn-sidebar-close").click()
            page.wait_for_function("!document.body.classList.contains('sidebar-open')")
        finally:
            browser.close()


def test_memory_management_survives_new_conversation_and_deletion(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    model = FakeToolCallingModel(responses=[AIMessage(content="你好")])
    container = _container(_settings(tmp_path), model)
    _, token = _account(container)
    with _serve(container) as base_url, playwright.sync_playwright() as manager:
        browser, page = _open(manager, base_url, container, token)
        try:
            page.locator("#btn-user-menu").click()
            page.locator("#btn-account-memory").click()
            page.locator("#memory-enabled").check()
            page.locator("#memory-content").fill("回答优先列出法规出处")
            page.locator("#memory-form button[type=submit]").click()
            page.wait_for_function("document.querySelectorAll('.memory-item').length === 1")
            page.locator("#memory-close").click()
            page.locator("#composer-input").fill("你好")
            page.locator("#btn-send").click()
            page.wait_for_selector(".answer")
            assert "回答优先列出法规出处" in str(model.calls[-1])
            page.locator("#btn-new-task").click()
            page.locator("#composer-input").fill("你好")
            page.locator("#btn-send").click()
            page.wait_for_selector(".answer")
            assert "回答优先列出法规出处" in str(model.calls[-1])
            page.locator("#btn-account-memory").click()
            page.locator(".memory-item button").filter(has_text="删除").click()
            page.wait_for_function("document.querySelectorAll('.memory-item').length === 0")
            page.locator("#memory-close").click()
            page.locator("#btn-new-task").click()
            page.locator("#composer-input").fill("你好")
            page.locator("#btn-send").click()
            page.wait_for_selector(".answer")
            assert "回答优先列出法规出处" not in str(model.calls[-1])
        finally:
            browser.close()


def test_risk_button_runs_verified_risk_tool_and_renders_result(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_law",
                        "args": {"query": "安全措施"},
                        "id": "law",
                        "type": "tool_call",
                    },
                    {
                        "name": "search_user_docs",
                        "args": {"query": "安全措施"},
                        "id": "doc",
                        "type": "tool_call",
                    },
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[{"name": "assess_risk", "args": {}, "id": "risk", "type": "tool_call"}],
            ),
            AIMessage(content="已完成分析"),
        ]
    )
    retriever = FakeRetrieve(
        [
            Chunk(
                chunk_id="law-38",
                text="应当采取安全措施。",
                source_name="测试法规",
                source_type="law",
                score=0.9,
            ),
            Chunk(
                chunk_id="doc",
                text="材料未说明安全措施。",
                source_name="测试材料",
                source_type="file",
                owner_id="github:features",
                score=0.9,
            ),
        ]
    )
    container = _container(_settings(tmp_path), model, retriever=retriever)
    _, token = _account(container)
    law_id = "SRC-" + hashlib.sha256(b"regulatory:law-38").hexdigest()[:12]
    container.risk_assessment._chat = FakeChat(
        responses=[
            json.dumps(
                {
                    "findings": [
                        {
                            "finding_id": "R1",
                            "text": "需要核对接收方安全措施",
                            "severity": "medium",
                            "citation_ids": [law_id],
                            "condition_message_ids": [],
                            "recommendation": "补充措施材料",
                        }
                    ],
                    "missing_information": [],
                },
                ensure_ascii=False,
            )
        ]
    )
    with _serve(container) as base_url, playwright.sync_playwright() as manager:
        browser, page = _open(manager, base_url, container, token)
        try:
            page.locator("#composer-input").fill("请判断接收方安全措施是否充分")
            page.locator("#btn-risk-analysis").click()
            assert page.locator("#btn-risk-analysis").get_attribute("aria-pressed") == "true"
            assert page.locator("#btn-risk-analysis").evaluate(
                "e => e.classList.contains('active')"
            )
            page.locator("#btn-send").click()
            page.wait_for_selector(".answer")
            text = page.locator(".answer").last.text_content()
            assert "初步风险分析" in text
            assert "需要核对接收方安全措施" in text
            assert "SRC-" in text
            assert page.locator("#run-status").text_content() == "已完成"
            tools = [
                row[0] for row in container.pool.get().execute("SELECT tool_name FROM tool_calls")
            ]
            assert "assess_risk" in tools
        finally:
            browser.close()
