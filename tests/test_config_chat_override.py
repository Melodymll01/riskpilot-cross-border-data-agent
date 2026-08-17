"""``Settings.chat_api_key`` / ``chat_api_base`` 单独覆盖逻辑（Step 026b）。

场景：embedding 留智谱（默认 ``OPENAI_API_*``）、chat 改走另一家 provider
（如阿里云百炼 GLM-5 / 通义 Qwen），避免一家 provider 同时承载两类调用
被绑死在同一额度池里。

向后兼容契约：未设 ``CHAT_API_KEY`` / ``CHAT_API_BASE`` 时，chat 仍走
``OPENAI_API_*``，既有用户零改动。
"""

from __future__ import annotations

import pytest

from config import RuntimeConfigurationError, Settings

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
    "STORAGE_BACKEND",
    "DATABASE_URL",
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


class TestLangChainModelUsesEffective:
    """LangChain ChatModel 工厂读取 chat 通道覆盖值。"""

    def test_chat_client_reads_overridden_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from infra.agents.model import build_langchain_chat_model

        model = build_langchain_chat_model(
            model="glm-5",
            api_key="sk-bailian",
            base_url="https://bailian.example/compatible/v1",
            temperature=0.1,
            max_tokens=1024,
        )
        assert "bailian.example" in str(model.root_client.base_url)
        assert model.model_name == "glm-5"


class TestRuntimeConfiguration:
    def test_import_safe_defaults_report_errors_without_raising(self) -> None:
        settings = _mk()

        assert settings.runtime_configuration_errors() == [
            "LLM_PROVIDER=api 时必须配置 CHAT_API_KEY 或 OPENAI_API_KEY",
            "EMBED_PROVIDER=api 时必须配置 OPENAI_API_KEY",
        ]

    def test_explicit_runtime_validation_rejects_placeholder_keys(self) -> None:
        settings = _mk()

        with pytest.raises(RuntimeConfigurationError, match="运行配置无效"):
            settings.validate_runtime_configuration()

    def test_local_profile_needs_no_external_key(self) -> None:
        settings = _mk(llm_provider="local", embed_provider="local")

        settings.validate_runtime_configuration()

    def test_nonzero_price_requires_explicit_currency(self) -> None:
        settings = _mk(
            llm_provider="local",
            embed_provider="local",
            llm_input_cost_per_1m_tokens=2.0,
        )

        assert settings.runtime_configuration_errors() == [
            "配置非零 LLM token 价格时必须显式配置 LLM_COST_CURRENCY"
        ]

    def test_nonzero_price_accepts_three_letter_currency(self) -> None:
        settings = _mk(
            llm_provider="local",
            embed_provider="local",
            llm_input_cost_per_1m_tokens=2.0,
            llm_cost_currency="CNY",
        )

        settings.validate_runtime_configuration()

    def test_split_api_credentials_validate_independently(self) -> None:
        settings = _mk(
            llm_provider="api",
            embed_provider="api",
            chat_api_key="sk-chat-real",
            openai_api_key="sk-embed-real",
        )

        settings.validate_runtime_configuration()

    def test_langsmith_requires_key_and_hash_salt_when_enabled(self) -> None:
        settings = _mk(
            llm_provider="local",
            embed_provider="local",
            risk_pilot_langsmith_enabled=True,
        )

        assert settings.runtime_configuration_errors() == [
            "启用 LangSmith 时必须配置 LANGSMITH_API_KEY",
            "启用 LangSmith 时 LANGSMITH_HASH_SALT 至少需要 16 个字符",
        ]

    def test_postgres_profile_requires_postgres_database_url(self) -> None:
        settings = _mk(
            llm_provider="local",
            embed_provider="local",
            storage_backend="postgres",
            vector_backend="pgvector",
            database_url="sqlite:///wrong.db",
        )

        assert settings.runtime_configuration_errors() == [
            "STORAGE_BACKEND=postgres 时 DATABASE_URL 必须是 PostgreSQL URL"
        ]

    @pytest.mark.parametrize(
        ("storage_backend", "vector_backend"),
        [("sqlite", "pgvector"), ("postgres", "chroma")],
    )
    def test_rejects_split_brain_storage_profiles(
        self,
        storage_backend: str,
        vector_backend: str,
    ) -> None:
        settings = _mk(
            llm_provider="local",
            embed_provider="local",
            storage_backend=storage_backend,
            vector_backend=vector_backend,
            database_url="postgresql+psycopg://riskpilot@localhost/riskpilot",
        )

        assert (
            "仅支持 sqlite+chroma 本地 Profile 或 postgres+pgvector 生产 Profile"
            in settings.runtime_configuration_errors()
        )

    def test_pgvector_requires_the_indexed_embedding_dimension(self) -> None:
        settings = _mk(
            llm_provider="local",
            embed_provider="local",
            storage_backend="postgres",
            vector_backend="pgvector",
            database_url="postgresql+psycopg://riskpilot@localhost/riskpilot",
            embedding_dimensions=1024,
        )

        assert (
            "VECTOR_BACKEND=pgvector 时 EMBEDDING_DIMENSIONS 必须为 2048"
            in settings.runtime_configuration_errors()
        )

    def test_s3_credentials_must_be_complete_pairs(self) -> None:
        settings = _mk(
            llm_provider="local",
            embed_provider="local",
            object_store_backend="s3",
            s3_access_key_id="only-access-key",
            s3_secret_access_key=None,
        )

        assert "S3_ACCESS_KEY_ID 与 S3_SECRET_ACCESS_KEY 必须同时配置或同时省略" in (
            settings.runtime_configuration_errors()
        )

    def test_celery_requires_redis_and_shared_production_storage(self) -> None:
        settings = _mk(
            llm_provider="local",
            embed_provider="local",
            task_backend="celery",
        )

        errors = settings.runtime_configuration_errors()
        assert "TASK_BACKEND=celery 时必须配置 Redis CELERY_BROKER_URL 或 REDIS_URL" in errors
        assert "TASK_BACKEND=celery 时必须使用 postgres+pgvector 生产 Profile" in errors
        assert "TASK_BACKEND=celery 时必须使用 S3/MinIO 对象存储" in errors

    def test_celery_accepts_complete_production_profile(self) -> None:
        settings = _mk(
            llm_provider="local",
            embed_provider="local",
            task_backend="celery",
            storage_backend="postgres",
            vector_backend="pgvector",
            object_store_backend="s3",
            database_url="postgresql+psycopg://riskpilot@localhost/riskpilot",
            redis_url="redis://localhost:6379/0",
        )

        settings.validate_runtime_configuration()
