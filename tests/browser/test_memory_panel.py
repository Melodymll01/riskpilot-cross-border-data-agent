"""Memory panel layout, inline editing and loading failures in a real browser."""

import pytest
from langchain_core.messages import AIMessage

from tests.browser.test_account_features import _account, _open
from tests.browser.test_phase3_browser import CHROME, _serve
from tests.fakes import FakeToolCallingModel
from tests.infra.test_conversation_checkpoint_recovery import _container, _settings

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="Google Chrome is not installed")


def _open_panel(page, *, mobile=False):
    if mobile:
        page.locator("#btn-sidebar-toggle").click()
    page.locator("#btn-user-menu").click()
    page.locator("#btn-account-memory").click()


@pytest.mark.parametrize(
    ("viewport", "theme"),
    [
        ({"width": 1600, "height": 1000}, "light"),
        ({"width": 1600, "height": 1000}, "dark"),
        ({"width": 390, "height": 844}, "light"),
    ],
)
def test_centered_panel_and_complete_management_flow(tmp_path, viewport, theme):
    playwright = pytest.importorskip("playwright.sync_api")
    expect = playwright.expect
    container = _container(
        _settings(tmp_path), FakeToolCallingModel(responses=[AIMessage(content="你好")])
    )
    owner, token = _account(container)
    with _serve(container) as base_url, playwright.sync_playwright() as manager:
        browser, page = _open(manager, base_url, container, token)
        try:
            page.set_viewport_size(viewport)
            page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
            _open_panel(page, mobile=viewport["width"] < 560)
            expect(page.locator(".memory-placeholder h4")).to_have_text("还没有保存的记忆")
            expect(page.locator("#memory-status")).to_be_hidden()
            expect(page.locator("#memory-enabled")).not_to_be_checked()
            expect(page.locator("#memory-clear")).to_be_disabled()
            expect(page.locator("#memory-save")).to_be_disabled()

            box = page.locator("#memory-dialog").bounding_box()
            assert abs(box["x"] + box["width"] / 2 - viewport["width"] / 2) < 2
            assert abs(box["y"] + box["height"] / 2 - viewport["height"] / 2) < 2
            assert box["x"] >= 10 and box["y"] >= 10
            assert box["x"] + box["width"] <= viewport["width"] - 10
            assert box["y"] + box["height"] <= viewport["height"] - 10
            assert page.evaluate("document.documentElement.scrollWidth") <= viewport["width"]
            empty_image = tmp_path / "memory-empty.png"
            page.screenshot(path=str(empty_image), animations="disabled")
            print(f"MEMORY_UI_SCREENSHOT={empty_image}")

            page.locator("#memory-enabled").check()
            expect(page.locator("#memory-status")).to_contain_text("已开启")

            page.locator("#memory-content").fill("回答优先列出法规出处")
            expect(page.locator("#memory-character-count")).to_have_text("10 / 800")
            page.locator("#memory-save").click()
            expect(page.locator("#memory-count")).to_have_text("1")
            expect(page.locator("#memory-status")).to_have_text("记忆已保存")
            expect(page.locator("#memory-content")).to_have_value("")

            page.locator(".memory-edit").click()
            expect(page.locator(".memory-edit-form textarea")).to_be_focused()
            page.locator(".memory-edit-form textarea").fill("取消这次修改")
            page.locator(".memory-edit-form button").filter(has_text="取消").click()
            assert (
                container.account_memory.list_memories(owner)[0].content == "回答优先列出法规出处"
            )
            page.locator(".memory-edit").click()
            page.locator(".memory-edit-form textarea").fill("回答详细解释")
            page.locator(".memory-edit-form button[type=submit]").click()
            expect(page.locator(".memory-item-content")).to_have_text("回答详细解释")

            page.locator("#memory-kind").select_option("business_context")
            expect(page.locator("#memory-kind-help")).to_contain_text("90 天")
            page.locator("#memory-content").fill("公司使用新加坡CRM")
            page.locator("#memory-save").click()
            expect(page.locator("#memory-count")).to_have_text("2")
            expect(page.locator(".memory-kind-badge[data-kind=business_context]")).to_have_text(
                "业务背景"
            )
            expect(page.locator("#memory-status")).to_have_text("记忆已保存")
            records_image = tmp_path / "memory-records.png"
            page.screenshot(path=str(records_image), animations="disabled")
            print(f"MEMORY_UI_SCREENSHOT={records_image}")

            page.locator("#memory-search").fill("新加坡")
            expect(page.locator(".memory-item")).to_have_count(1)
            page.locator("#memory-search").fill("不存在的关键词")
            expect(page.locator(".memory-placeholder h4")).to_have_text("没有找到匹配的记忆")
            page.locator(".memory-placeholder button").click()
            expect(page.locator(".memory-item")).to_have_count(2)

            page.locator("#memory-enabled").uncheck()
            expect(page.locator("#memory-status")).to_contain_text("已关闭")
            assert container.account_memory.enabled(owner) is False
            expect(page.locator(".memory-item")).to_have_count(2)
            page.locator("#memory-enabled").check()
            expect(page.locator("#memory-status")).to_contain_text("已开启")

            page.locator("#memory-clear").click()
            expect(page.locator("#memory-clear")).to_have_text("确认清空 2 条")
            assert len(container.account_memory.list_memories(owner)) == 2
            page.locator("#memory-clear-cancel").click()
            expect(page.locator("#memory-status")).to_be_hidden()
            page.locator("#memory-clear").click()
            page.locator("#memory-clear").click()
            expect(page.locator("#memory-count")).to_have_text("0")
            expect(page.locator("#memory-clear")).to_be_disabled()
            assert container.account_memory.list_memories(owner) == []
            page.locator("#memory-close").click()
            expect(page.locator("#memory-dialog")).not_to_be_visible()
        finally:
            browser.close()


def test_loading_retry_save_failure_and_memory_text_safety(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    expect = playwright.expect
    container = _container(
        _settings(tmp_path), FakeToolCallingModel(responses=[AIMessage(content="你好")])
    )
    _, token = _account(container)
    with _serve(container) as base_url, playwright.sync_playwright() as manager:
        browser, page = _open(manager, base_url, container, token)

        def failed_read(route):
            route.fulfill(
                status=503, content_type="application/json", body='{"message":"测试读取失败"}'
            )

        def failed_save(route):
            if route.request.method == "POST":
                route.fulfill(
                    status=503, content_type="application/json", body='{"message":"测试保存失败"}'
                )
            else:
                route.continue_()

        try:
            page.route("**/api/v2/memory", failed_read)
            _open_panel(page)
            expect(page.locator(".memory-placeholder h4")).to_have_text("暂时无法加载记忆")
            expect(page.locator("#memory-status")).to_have_text("测试读取失败")
            page.unroute("**/api/v2/memory", failed_read)
            page.locator(".memory-placeholder button").click()
            expect(page.locator(".memory-placeholder h4")).to_have_text("还没有保存的记忆")
            expect(page.locator("#memory-status")).to_be_hidden()

            page.route("**/api/v2/memory", failed_save)
            page.locator("#memory-content").fill("回答简洁一点")
            page.locator("#memory-save").click()
            expect(page.locator("#memory-status")).to_have_text("测试保存失败")
            expect(page.locator("#memory-content")).to_have_value("回答简洁一点")
            expect(page.locator("#memory-count")).to_have_text("0")
            page.unroute("**/api/v2/memory", failed_save)

            malicious = '<img src=x onerror="window.memoryXss=1">'
            page.locator("#memory-content").fill(malicious)
            page.locator("#memory-save").click()
            expect(page.locator(".memory-item-content")).to_have_text(malicious)
            assert page.locator(".memory-item img").count() == 0
            assert page.evaluate("window.memoryXss") is None
        finally:
            browser.close()
