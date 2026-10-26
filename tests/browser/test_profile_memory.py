"""Profile types, source citations and field-specific retention through the UI."""

import pytest
from langchain_core.messages import AIMessage

from tests.browser.test_account_features import _account, _open
from tests.browser.test_phase3_browser import CHROME, _serve
from tests.fakes import FakeToolCallingModel
from tests.infra.test_conversation_checkpoint_recovery import _container, _settings

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="Google Chrome is not installed")


def test_profile_fields_opt_in_personal_answers_and_correction(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    expect = playwright.expect
    model = FakeToolCallingModel(responses=[AIMessage(content="不应调用")])
    container = _container(_settings(tmp_path), model)
    owner, token = _account(container)

    def send(page, text):
        page.locator("#btn-new-task").click()
        page.locator("#composer-input").fill(text)
        page.locator("#btn-send").click()
        page.wait_for_selector(".answer")
        return page.locator(".answer").last.inner_text()

    with _serve(container) as base_url, playwright.sync_playwright() as manager:
        browser, page = _open(manager, base_url, container, token)
        try:
            page.locator("#btn-user-menu").click()
            page.locator("#btn-account-memory").click()
            expect(page.locator(".memory-placeholder h4")).to_have_text("还没有保存的记忆")
            expect(page.locator("#memory-enabled")).not_to_be_checked()
            expect(page.locator("#memory-kind")).to_have_value("auto")
            page.locator("#memory-content").fill("我是马乐乐，是学生")
            page.locator("#memory-save").click()
            expect(page.locator("#memory-count")).to_have_text("2")
            expect(page.locator("#memory-status")).to_contain_text("拆分为 2 条")
            records = {
                item.profile_field: item for item in container.account_memory.list_memories(owner)
            }
            assert records["name"].expires_at is None
            assert records["role"].retention_days == 365
            page.locator("#memory-close").click()
            assert "已关闭" in send(page, "我的职业是什么")

            page.locator("#btn-account-memory").click()
            page.locator("#memory-enabled").check()
            expect(page.locator("#memory-status")).to_contain_text("已开启")
            page.locator("#memory-close").click()
            assert "学生" in send(page, "我的职业是什么")
            expect(page.locator(".citations")).to_contain_text("我的记忆")
            assert "马乐乐" in send(page, "我叫什么")

            page.locator("#btn-account-memory").click()
            role = page.locator(".memory-item").filter(
                has=page.locator(".memory-profile-value", has_text="当前身份")
            )
            role.locator(".memory-edit").click()
            role.locator(".memory-edit-form textarea").fill("我目前是工程师")
            role.locator(".memory-edit-form button[type=submit]").click()
            expect(page.locator(".memory-profile-value", has_text="工程师")).to_be_visible()
            page.locator("#memory-close").click()
            reply = send(page, "我的职业是什么")
            assert "工程师" in reply and "学生" not in reply
            assert model.calls == []

            page.locator("#btn-account-memory").click()
            page.locator("#memory-kind").select_option("profile")
            expect(page.locator("#memory-profile-field-wrap")).to_be_visible()
            expect(page.locator("#memory-kind-help")).to_contain_text("长期保留")
            page.locator("#memory-profile-field").select_option("role")
            expect(page.locator("#memory-kind-help")).to_contain_text("180")
            assert page.locator('#memory-retention option[value="none"]').is_disabled()
        finally:
            browser.close()
