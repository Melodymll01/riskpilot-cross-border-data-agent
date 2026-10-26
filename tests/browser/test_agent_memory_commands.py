"""Chat commands complete the account-memory lifecycle through the browser and API."""

import pytest
from langchain_core.messages import AIMessage

from tests.browser.test_account_features import _account, _open
from tests.browser.test_phase3_browser import CHROME, _serve
from tests.fakes import FakeToolCallingModel
from tests.infra.test_conversation_checkpoint_recovery import _container, _settings

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="Google Chrome is not installed")


def test_memory_commands_save_correct_and_forget_across_conversations(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    model = FakeToolCallingModel(responses=[AIMessage(content="你好")])
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
            assert "已保存" in send(page, "请记住：回答简洁一点")
            assert "没有重复新增" in send(page, "请记住：回答简洁一点")
            assert len(container.account_memory.list_memories(owner)) == 1
            assert model.calls == []

            assert "已开启" in send(page, "开启记忆")

            send(page, "你好")
            assert "回答简洁一点" in str(model.calls[-1])
            assert "已更新" in send(page, "请更新记忆：回答简洁一点 => 回答详细解释")
            send(page, "你好")
            assert "回答详细解释" in str(model.calls[-1])
            assert "回答简洁一点" not in str(model.calls[-1])

            assert "已删除" in send(page, "请忘记：回答详细解释")
            send(page, "你好")
            assert "回答详细解释" not in str(model.calls[-1])
            assert container.account_memory.list_memories(owner) == []
        finally:
            browser.close()
