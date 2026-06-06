"""``Settings.chat_api_key`` / ``chat_api_base`` 单独覆盖逻辑（Step 026b）。

场景：embedding 留智谱（默认 ``OPENAI_API_*``）、chat 改走另一家 provider
（如阿里云百炼 GLM-5 / 通义 Qwen），避免一家 provider 同时承载两类调用
被绑死在同一额度池里。

向后兼容契约：未设 ``CHAT_API_KEY`` / ``CHAT_API_BASE`` 时，chat 仍走
``OPENAI_API_*``，既有用户零改动。
"""

from __future__ import annotations

import pytest

from config import Settings

# 关键变量集合：autouse fixture 在每个测试前从 os.environ 清掉，
# 防其它测试通过 monkeypatch.setenv / load_dotenv 污染本套用例。
# pydantic-settings 优先级 init kwargs > env vars > .env，但若环境里已设
# CHAT_API_KEY 而我们没显式传，仍会被注入 → 所有测试一律先清场。
_ENV_KEYS = (
    "CHAT_API_KEY",
    "CHAT_API_BASE",
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "EMBEDDING_MODEL",
    "CHAT_MODEL",
    "LLM_PROVIDER",
    "EMBED_PROVIDER",
    "OLLAMA_API_BASE",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)


def _mk(**overrides) -> Settings:
    """构造 Settings 实例，不读 .env 文件，避免污染。"""
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


class TestChatApiKeyOverride:
    def test_default_falls_back_to_openai_api_key(self) -> None:
        """缺省（chat_api_key=None）→ chat 走 openai_api_key（兼容路径）。"""
        s = _mk(openai_api_key="sk-zhipu-real")
        assert s.chat_api_key is None
        assert s.effective_chat_api_key == "sk-zhipu-real"

    def test_explicit_override_takes_precedence(self) -> None:
        """显式设了 chat_api_key → 覆盖 openai_api_key。"""
        s = _mk(
            openai_api_key="sk-zhipu-real",
            chat_api_key="sk-bailian-real",
        )
        assert s.effective_chat_api_key == "sk-bailian-real"

    def test_empty_string_treated_as_falsy_and_falls_back(self) -> None:
        """``CHAT_API_KEY=``（空串）应当被视为未设，回退到 openai_api_key。"""
        s = _mk(openai_api_key="sk-zhipu-real", chat_api_key="")
        assert s.effective_chat_api_key == "sk-zhipu-real"

    def test_local_provider_ignores_override(self) -> None:
        """``llm_provider=local`` 时一律返回 "ollama"，chat_api_key 不生效。"""
        s = _mk(
            llm_provider="local",
            openai_api_key="sk-zhipu-real",
            chat_api_key="sk-bailian-real",
        )
        assert s.effective_chat_api_key == "ollama"


class TestChatApiBaseOverride:
    def test_default_falls_back_to_openai_api_base(self) -> None:
        s = _mk(openai_api_base="https://zhipu.example/v1")
        assert s.chat_api_base is None
        assert s.effective_chat_base_url == "https://zhipu.example/v1"

    def test_explicit_override_takes_precedence(self) -> None:
        s = _mk(
            openai_api_base="https://zhipu.example/v1",
            chat_api_base="https://bailian.example/compatible/v1",
        )
        assert s.effective_chat_base_url == "https://bailian.example/compatible/v1"

    def test_empty_string_falls_back(self) -> None:
        s = _mk(openai_api_base="https://zhipu.example/v1", chat_api_base="")
        assert s.effective_chat_base_url == "https://zhipu.example/v1"

    def test_local_provider_ignores_override(self) -> None:
        s = _mk(
            llm_provider="local",
            ollama_api_base="http://localhost:11434/v1",
            openai_api_base="https://zhipu.example/v1",
            chat_api_base="https://bailian.example/v1",
        )
        assert s.effective_chat_base_url == "http://localhost:11434/v1"


class TestEmbedNotAffected:
    """关键不变式：chat_* 覆盖不应当波及 embedding 通道。"""

    def test_embed_api_key_still_reads_openai(self) -> None:
        s = _mk(
            openai_api_key="sk-zhipu-real",
            chat_api_key="sk-bailian-real",
        )
        # embed 必须仍走智谱（用户场景）
        assert s.effective_embed_api_key == "sk-zhipu-real"

    def test_embed_base_url_still_reads_openai(self) -> None:
        s = _mk(
            openai_api_base="https://zhipu.example/v1",
            chat_api_base="https://bailian.example/v1",
        )
        assert s.effective_embed_base_url == "https://zhipu.example/v1"


class TestChatClientUsesEffective:
    """``ChatClient`` 改用 effective_chat_* 后真的拿到了覆盖值（防回归）。"""

    def test_chat_client_reads_overridden_base_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 直接 patch 模块级 settings 属性（最少侵入；ChatClient 在 __init__
        # 里读 settings.effective_chat_*）
        from retrieval.generation import chat_client as cc

        monkeypatch.setattr(cc.settings, "openai_api_key", "sk-zhipu", raising=False)
        monkeypatch.setattr(
            cc.settings, "openai_api_base", "https://zhipu.example/v1", raising=False
        )
        monkeypatch.setattr(cc.settings, "chat_api_key", "sk-bailian", raising=False)
        monkeypatch.setattr(
            cc.settings,
            "chat_api_base",
            "https://bailian.example/compatible/v1",
            raising=False,
        )
        monkeypatch.setattr(cc.settings, "chat_model", "glm-5", raising=False)
        monkeypatch.setattr(cc.settings, "llm_provider", "api", raising=False)

        client = cc.ChatClient()
        # OpenAI SDK 把 base_url 标准化为带末尾 / 的 httpx URL 对象
        assert "bailian.example" in str(client.client.base_url)
        assert client.model == "glm-5"
