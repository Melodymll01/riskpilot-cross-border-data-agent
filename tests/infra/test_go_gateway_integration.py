"""Real Go gateway + private Python runtime, isolated SQLite and fake AI model."""

import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from langchain_core.messages import AIMessage

from domain.models import User
from infra.auth.jwt_issuer import JwtIssuer
from infra.storage.sqlite_user_repo import SqliteUserRepo
from tests.browser.test_phase3_browser import CHROME, _free_port, _serve
from tests.fakes import FakeToolCallingModel
from tests.infra.test_conversation_checkpoint_recovery import _container, _settings

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("go") is None, reason="Go is not installed")


@pytest.fixture(scope="module")
def gateway_binary(tmp_path_factory):
    binary = tmp_path_factory.mktemp("go-build") / "riskpilot-api"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/go_tool.py"),
            "build",
            "-o",
            str(binary),
            "./cmd/riskpilot",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return binary


@contextmanager
def stack(tmp_path, gateway_binary):
    key = tmp_path / "internal.key"
    key.write_text("isolated-internal-secret-" * 3)
    model = FakeToolCallingModel(responses=[AIMessage(content="你好")])
    settings = _settings(
        tmp_path, internal_service_required=True, internal_service_key_file=str(key)
    )
    container = _container(settings, model)
    owner = "github:go-integration"
    SqliteUserRepo(container.pool).upsert(
        User(
            user_id=owner,
            provider="github",
            provider_id="go-integration",
            display_name="Gateway Test",
            created_at=1,
            last_active_at=1,
        )
    )
    token = JwtIssuer(settings.jwt_secret).issue(owner)
    port = _free_port()
    log = tmp_path / "gateway.log"
    with _serve(container) as python_url, log.open("wb") as output:
        env = {
            **os.environ,
            "RISKPILOT_ROOT": str(ROOT),
            "GATEWAY_LISTEN": f"127.0.0.1:{port}",
            "PYTHON_SERVICE_URL": python_url,
            "SQLITE_DB_PATH": settings.sqlite_db_path,
            "JWT_SECRET": settings.jwt_secret,
            "INTERNAL_SERVICE_KEY_FILE": str(key),
            "RATE_LIMIT_ENABLED": "false",
            "COOKIE_SECURE": "false",
            "ADMIN_USER_IDS": "",
        }
        process = subprocess.Popen(
            [str(gateway_binary)], cwd=ROOT, env=env, stdout=output, stderr=output
        )
        url = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 10
            with httpx.Client(trust_env=False) as client:
                while time.monotonic() < deadline:
                    assert process.poll() is None, log.read_text()
                    try:
                        if client.get(url + "/api/v2/health/live").status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    time.sleep(0.05)
                else:
                    pytest.fail("Go gateway did not start")
            yield container, model, owner, token, url, python_url
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def test_go_auth_real_memory_sse_owner_isolation_and_private_boundary(tmp_path, gateway_binary):
    with stack(tmp_path, gateway_binary) as (container, model, owner, token, url, python_url):
        with httpx.Client(base_url=url, trust_env=False, timeout=10) as client:
            assert client.get("/api/v2/health/ready").json()["checks"]["ready"] is True
            assert client.get("/api/v2/auth/me").json()["authenticated"] is False
            client.cookies.set(container.settings.cookie_name, token, domain="127.0.0.1", path="/")
            assert client.get("/api/v2/auth/me").json()["user"]["user_id"] == owner
            settings = client.get("/api/v2/memory")
            assert settings.status_code == 200
            assert settings.headers["x-riskpilot-backend"] == "go-python"
            assert settings.json()["enabled"] is False
            created = client.post(
                "/api/v2/memory", json={"kind": "auto", "content": "我叫马乐乐，是学生"}
            )
            assert created.status_code == 201, created.text
            assert created.json()["saved_count"] == 2
            assert client.put("/api/v2/memory/settings", json={"enabled": True}).status_code == 200
            chat = client.post("/api/v2/copilot/chat/stream", json={"message": "我的职业是什么"})
            assert chat.status_code == 200
            assert "event: run_started" in chat.text and "event: answer" in chat.text
            payloads = [
                json.loads(line.removeprefix("data: "))
                for line in chat.text.splitlines()
                if line.startswith("data: ")
            ]
            run_id = next(item["run_id"] for item in payloads if "run_id" in item)
            assert "学生" in chat.text and '"source_type": "memory"' in chat.text
            assert model.calls == []
            assert client.get(f"/api/v2/copilot/runs/{run_id}").json()["status"] == "completed"

            anonymous = client.post("/api/v2/auth/anonymous")
            assert anonymous.status_code == 201
            assert container.user_repo.get(anonymous.json()["user"]["user_id"]) is not None
            assert client.get(f"/api/v2/copilot/runs/{run_id}").status_code == 404
            assert client.get("/api/v2/memory").status_code == 403
            spoof = client.get(
                f"/api/v2/copilot/runs/{run_id}",
                headers={"X-Riskpilot-Owner": owner, "X-Riskpilot-Identity": "forged"},
            )
            assert spoof.status_code == 404
            client.post("/api/v2/auth/logout")
            assert client.get("/api/v2/auth/me").json()["authenticated"] is False
        with httpx.Client(base_url=python_url, trust_env=False) as private:
            assert (
                private.get(
                    "/api/v2/memory",
                    headers={"Cookie": f"{container.settings.cookie_name}={token}"},
                ).status_code
                == 401
            )
            assert private.post("/api/v2/auth/anonymous").status_code == 404


@pytest.mark.skipif(not CHROME.exists(), reason="Google Chrome is not installed")
def test_browser_uses_go_entry_to_manage_profile_and_receive_cited_answer(tmp_path, gateway_binary):
    playwright = pytest.importorskip("playwright.sync_api")
    with (
        stack(tmp_path, gateway_binary) as (container, model, owner, token, url, _),
        playwright.sync_playwright() as manager,
    ):
        browser = manager.chromium.launch(executable_path=str(CHROME), headless=True)
        context = browser.new_context()
        context.add_cookies([{"name": container.settings.cookie_name, "value": token, "url": url}])
        page = context.new_page()
        try:
            page.goto(url)
            page.wait_for_function("document.documentElement.dataset.appReady === 'true'")
            page.locator("#btn-user-menu").click()
            page.locator("#btn-account-memory").click()
            page.wait_for_selector(".memory-placeholder h4")
            page.locator("#memory-content").fill("我叫马乐乐，是学生")
            page.locator("#memory-save").click()
            page.wait_for_function("document.querySelector('#memory-count').textContent === '2'")
            page.locator("#memory-enabled").check()
            page.wait_for_function(
                "document.querySelector('#memory-status').textContent.includes('已开启')"
            )
            page.locator("#memory-close").click()
            page.locator("#composer-input").fill("我的职业是什么")
            page.locator("#btn-send").click()
            page.wait_for_selector(".answer")
            assert "学生" in page.locator(".answer").last.inner_text()
            assert "我的记忆" in page.locator(".citations").inner_text()
            assert model.calls == []
            assert len(container.account_memory.list_memories(owner)) == 2
        finally:
            browser.close()
