"""全局配置模块，基于 pydantic-settings 加载环境变量。"""

import hashlib
import json
import os
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode


class RuntimeConfigurationError(RuntimeError):
    """应用启动配置不完整；模块导入和离线测试不应触发。"""


def _is_placeholder_secret(value: str | None) -> bool:
    if not value or not value.strip():
        return True
    normalized = value.strip().lower()
    return (
        normalized in {"sk-placeholder", "your-api-key-here"}
        or normalized.startswith("your-")
        or normalized.startswith("<")
    )


class Settings(BaseSettings):
    # ── 业务数据库 Profile ────────────────────────────────────────────────────

    # ── 通道选择 ──────────────────────────────────────────────────────────────
    # "api"   → 使用远端 API（智谱 / OpenAI 等）
    # "local" → 使用本机 Ollama
    # "traex" → 复用 Trae CLI 认证，不使用 API Key
    llm_provider: Literal["api", "local", "traex"] = "traex"
    traex_bin: str = "/Users/bytedance/.local/bin/traex"
    traex_model: str = "gpt-5.4"
    traex_timeout_seconds: float = Field(90.0, gt=0, le=600)
    embed_provider: Literal["api", "local", "sentence_transformer"] = "sentence_transformer"
    local_sentence_embedding_model: str = "BAAI/bge-small-zh-v1.5"

    # ── 远端 API 配置（智谱 / OpenAI 兼容）────────────────────────────────────
    openai_api_key: str = "sk-placeholder"
    openai_api_base: str = "https://open.bigmodel.cn/api/paas/v4"
    embedding_model: str = "embedding-3"
    embedding_dimensions: int | None = Field(
        2048,
        description="Embedding 向量维度，智谱 embedding-3 支持 256/512/1024/2048，None 则使用模型默认值",
    )
    chat_model: str = "glm-4-flash"

    # ── Chat 通道单独覆盖（Step 026b）────────────────────────────────────────
    # 留空 → chat 走 openai_api_key/openai_api_base（向后兼容）
    # 设置 → chat 走 chat_api_key/chat_api_base；embedding 仍走 openai_*
    # 典型场景：embedding 留智谱、chat 换百炼 GLM-5 / 通义 Qwen 拿免费额度
    chat_api_key: str | None = None
    chat_api_base: str | None = None

    # ── 本地 Ollama 配置 ──────────────────────────────────────────────────────
    ollama_api_base: str = "http://localhost:11434/v1"
    local_embedding_model: str = "nomic-embed-text"
    local_chat_model: str = "qwen2.5:7b"

    chat_max_tokens: int = Field(4096, ge=100, le=8192)

    # 检索参数
    # BGE-small 的 max_seq_length=512；字符级切分保留特殊 token 余量。
    # 420/84 在冻结法规问题集上达到 14/15 Top-5 命中，且不会把 600 字符块静默截断。
    chunk_size: int = Field(420, ge=100, le=4000)
    chunk_overlap: int = Field(84, ge=0, le=500)
    index_schema_version: str = "parent-child-v1"
    embedding_input_strategy: str = "section-path-prefix-v1"
    embedding_max_input_tokens: int = Field(
        480,
        ge=64,
        le=8192,
        description="单块 Embedding token 上限；BGE-small 的安全值为 480",
    )
    top_k: int = Field(5, ge=1, le=50)
    distance_threshold: float = Field(0.6, ge=0.0, le=1.0)  # 余弦距离阈值
    text_overlap_threshold: float = Field(0.7, ge=0.0, le=1.0)  # 文本去重重叠率阈值

    # 领域关键词（用于混合检索精确匹配，可在 .env 中覆盖）
    domain_terms: list[str] = [
        "数据出境",
        "安全评估",
        "个人信息保护",
        "数据安全",
        "跨境传输",
        "标准合同",
        "保护认证",
        "关键信息基础设施",
        "网络安全审查",
        "重要数据",
        "敏感个人信息",
        "数据处理者",
    ]

    # RAG 增强参数
    enable_query_rewrite: bool = True  # 是否启用查询改写（会多一次 LLM 调用）
    enable_reranker: bool = True  # 是否启用 Cross-Encoder 重排序
    reranker_model: str = "BAAI/bge-reranker-base"  # 中文友好，首次启动从 HF 下载约 1.1GB
    reranker_device: str = "auto"  # cuda / cpu / auto（auto 优先 GPU）
    reranker_score_threshold: float | None = None  # 分数阈值，None 表示不过滤
    context_window_size: int = Field(1, ge=0, le=5)  # 上下文窗口：前后各拉取 N 个相邻 chunk
    retrieval_vector_candidate_multiplier: int = Field(4, ge=1, le=20)
    retrieval_bm25_candidate_multiplier: int = Field(6, ge=1, le=20)
    retrieval_candidate_max: int = Field(100, ge=10, le=500)
    section_expansion_max_chars: int = Field(3600, ge=500, le=12000)
    retrieval_context_total_max_chars: int = Field(12000, ge=1000, le=50000)

    # 混合检索 / RRF 融合
    enable_bm25_rrf: bool = True  # 启用 BM25 + RRF 融合（替代朴素 union+distance 排序）
    rrf_k: int = Field(60, ge=1, le=200)  # RRF 平滑常数（Cormack 2009 原论文默认 60）
    rrf_vector_weight: float = Field(1.0, ge=0.0, le=5.0)
    rrf_bm25_weight: float = Field(1.0, ge=0.0, le=5.0)

    # 存储路径
    chroma_persist_dir: str = "./data/chroma_db"
    upload_dir: str = "./data/uploads"
    object_store_dir: str = "./data/objects"

    # 上传限制
    max_upload_mb: int = Field(50, ge=1, le=500)  # 最大上传文件大小（MB）

    # LangSmith 仅用于 AI Trace；默认关闭，且 Adapter 强制隐藏输入/输出。
    risk_pilot_langsmith_enabled: bool = False
    langsmith_api_key: str | None = None
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_project: str = "riskpilot"
    langsmith_sampling_rate: float = Field(0.1, ge=0.0, le=1.0)
    langsmith_hash_salt: str | None = None

    # OpenTelemetry 默认关闭；只有显式启用且配置 OTLP endpoint 才会联网。
    otel_enabled: bool = False
    otel_service_name: str = "riskpilot-api"
    otel_exporter_otlp_endpoint: str | None = None
    otel_sampling_rate: float = Field(1.0, ge=0.0, le=1.0)
    observability_hash_salt: str = "dev-observability-salt-change-me"
    json_logs_enabled: bool = True
    prometheus_enabled: bool = True

    # 仅按显式价格表估算；默认 0 表示“未配置价格”，不伪造成本。

    # API 限流
    rate_limit_enabled: bool = True  # 是否启用 v2 HTTP 限流（测试可关）
    rate_limit_default: str = "60/minute"  # 普通接口默认限流
    rate_limit_llm: str = "20/minute"  # LLM 相关接口（问答/研究）限流
    rate_limit_ingest: str = "10/minute"  # 入库接口限流

    # 流式超时
    conversation_run_timeout_seconds: float = Field(120.0, ge=0.01, le=1800.0)
    conversation_model_call_timeout_seconds: float = Field(45.0, ge=0.1, le=300.0)
    conversation_tool_call_timeout_seconds: float = Field(20.0, ge=0.1, le=300.0)
    conversation_max_model_calls: int = Field(12, ge=1, le=50)
    conversation_max_tool_calls: int = Field(6, ge=1, le=50)
    conversation_control_poll_seconds: float = Field(0.05, ge=0.01, le=1.0)

    # CORS
    cors_origins: list[str] = ["*"]

    # 日志
    log_level: str = "INFO"

    # ── 应用层（Step 008 PR-5：DI 容器 + use case） ────────────────────────────
    sqlite_db_path: str = "./data/rag.sqlite3"
    langgraph_checkpoint_db_path: str = "./data/conversation-checkpoints.sqlite3"
    jwt_secret: str = "dev-jwt-secret-please-change-32-chars-minimum-length"
    jwt_ttl_seconds: int = Field(86400, ge=60, le=30 * 86400)  # 1min-30day
    github_client_id: str = "dev-placeholder-client-id"
    github_client_secret: str = "dev-placeholder-client-secret"
    # 默认匹配本地 uvicorn 端口 8765 + Step 010 新路由前缀 /api/v2
    # 生产可通过环境变量 GITHUB_REDIRECT_URI 覆盖
    github_redirect_uri: str = "http://127.0.0.1:8765/api/v2/auth/github/callback"
    internal_service_required: bool = False
    internal_service_key_file: str = "./data/internal-service.key"
    # Shared dev-stack settings; consumed by the Go service and launch scripts.
    go_sdkroot: str | None = None
    python_service_url: str = "http://127.0.0.1:8766"
    gateway_max_active_requests: int = Field(16, ge=1, le=1024)
    gateway_upstream_timeout_seconds: int = Field(510, ge=1, le=86400)
    gateway_rate_per_minute: int = Field(60, ge=1, le=100000)

    # ── 管理员（Step 012-tail：权限基线） ─────────────────────────────────────
    # 命名空间需与 User.user_id 一致；当前 GitHub OAuth 使用 "github:id:<数字 ID>"。
    # .env 支持两种写法：
    #   逗号分隔：ADMIN_USER_IDS=github:id:123,github:id:456
    #   JSON 数组：ADMIN_USER_IDS=["github:id:123","github:id:456"]
    # 命中此列表的用户：UserOut.is_admin=True，可通过 require_admin 访问管理接口。
    # 允许逗号分隔 或 JSON 数组；NoDecode 跳过 pydantic-settings 默认的 JSON 预解析，走下面的 validator。
    admin_user_ids: Annotated[list[str], NoDecode] = []

    @field_validator("admin_user_ids", mode="before")
    @classmethod
    def _split_admin_user_ids(cls, v):
        """允许 .env 用逗号分隔写法（避免用户被迫写 JSON 数组语法）。

        同时兼容 JSON 写法。空字符串返回空列表。
        """
        if v is None or v == "":
            return []
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("["):
                return v  # 交给 pydantic 默认 JSON 解析
            return [item.strip() for item in s.split(",") if item.strip()]
        return v

    # ── API v2（Step 010 PR-6：FastAPI 路由 + SSE） ────────────────────────────
    cookie_name: str = "copilot_session"
    cookie_secure: bool = False  # 生产 https 下置 True
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    # SSE 心跳间隔：长 SSE 流期间每 N 秒发一个注释行，防中间代理超时断开
    sse_keepalive_seconds: int = Field(15, ge=5, le=120)

    # ── 记忆系统（Step 030：分层记忆，S-030a 仅 L1 短期）────────────────────────
    # memory_enabled=False 时容器装配 memory=None，agent 退回无状态旧行为。
    memory_enabled: bool = True
    # 注入 prompt 的最近 N 条历史消息（不含当前轮）；0 等价于关闭注入。
    memory_recent_n: int = Field(6, ge=0, le=50)
    # 记忆块 token 预算上限（字符数近似）；超出时从最旧消息开始丢弃。
    memory_token_budget: int = Field(1500, ge=0, le=8000)

    # ── 记忆 L2 摘要 + TTL（Step 030b）─────────────────────────────────────────
    # L2 滚动摘要：未摘要消息数 ≥ 阈值时，后台 LLM 增量精炼出会话摘要。
    memory_summary_enabled: bool = True
    memory_summary_threshold: int = Field(20, ge=2, le=200)
    memory_summary_max_tokens: int = Field(800, ge=100, le=4096)
    # 差异化 TTL（逻辑遗忘）：读取时过滤过期记忆，过期原文/摘要永不注入。
    memory_l1_ttl_days: float = Field(30.0, ge=0.0, le=3650.0)
    memory_l2_ttl_days: float = Field(180.0, ge=0.0, le=3650.0)

    memory_business_ttl_days: float = Field(90.0, gt=0, le=3650)
    memory_profile_ttl_days: float = Field(365.0, gt=0, le=3650)
    memory_profile_review_days: float = Field(180.0, gt=0, le=3650)
    memory_account_context_chars: int = Field(600, ge=0, le=8000)

    # 单 owner 事实容量上限：巩固后超限按衰减分淘汰最低分。
    # hybrid_v1 先扩大向量候选池，再融合语义/置信度/显著性/新鲜度重排。
    # 去重相似度门控：候选与最近邻 ≥ 此值视为重复（强化置信，不新增）。
    # 冲突相似度门控：相似度落在 [conflict, dedup) 视为更新/矛盾 → 旧事实标 superseded。

    # 起步只支持显式偏好声明 + 系统配置偏好（自动抽取推迟，见设计 §14.5）。

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

    # ── 运行时读取有效配置（根据 provider 自动选择） ───────────────────────────
    @property
    def effective_chat_base_url(self) -> str:
        if self.llm_provider == "local":
            return self.ollama_api_base
        # chat 通道单独覆盖优先（Step 026b）
        return self.chat_api_base or self.openai_api_base

    @property
    def effective_chat_api_key(self) -> str:
        if self.llm_provider == "local":
            return "ollama"
        return self.chat_api_key or self.openai_api_key

    @property
    def effective_chat_model(self) -> str:
        if self.llm_provider == "traex":
            return self.traex_model
        return self.local_chat_model if self.llm_provider == "local" else self.chat_model

    @property
    def effective_embed_base_url(self) -> str:
        return self.ollama_api_base if self.embed_provider == "local" else self.openai_api_base

    @property
    def effective_embed_api_key(self) -> str:
        if self.embed_provider == "local":
            return "offline"
        return self.openai_api_key

    @property
    def effective_embed_model(self) -> str:
        if self.embed_provider == "sentence_transformer":
            return self.local_sentence_embedding_model
        return (
            self.local_embedding_model if self.embed_provider == "local" else self.embedding_model
        )

    @property
    def embedding_identity(self) -> str:
        endpoint = (
            ""
            if self.embed_provider == "sentence_transformer"
            else self.effective_embed_base_url.rstrip("/")
        )
        return json.dumps(
            {
                "provider": self.embed_provider,
                "model": self.effective_embed_model,
                "endpoint_sha256": hashlib.sha256(endpoint.encode()).hexdigest(),
                "requested_dimensions": self.embedding_dimensions
                if self.embed_provider != "sentence_transformer"
                else None,
                "embedding_max_input_tokens": self.embedding_max_input_tokens,
                "chunk_size": self.chunk_size,
                "chunk_overlap": self.chunk_overlap,
                "index_schema_version": self.index_schema_version,
                "embedding_input_strategy": self.embedding_input_strategy,
            },
            sort_keys=True,
        )

    def chat_configuration_errors(self) -> list[str]:
        """只检查被选中的语言模型通道，不要求其他通道的凭据。"""
        errors: list[str] = []
        if not self.effective_chat_model.strip():
            errors.append("所选语言模型的模型名称不能为空")
        if self.llm_provider == "traex":
            if not self.traex_bin or Path(self.traex_bin).name != "traex":
                errors.append("LLM_PROVIDER=traex 时 TRAEX_BIN 必须指向 traex 可执行文件")
            return errors
        if self.llm_provider == "api" and _is_placeholder_secret(self.effective_chat_api_key):
            errors.append("LLM_PROVIDER=api 时必须配置 CHAT_API_KEY 或 OPENAI_API_KEY")
        try:
            endpoint = urlsplit(self.effective_chat_base_url)
            valid_endpoint = endpoint.scheme in {"http", "https"} and bool(endpoint.netloc)
        except ValueError:
            valid_endpoint = False
        if not valid_endpoint:
            errors.append("API/本地语言模型的接口地址必须是有效的 HTTP(S) URL")
        return errors

    def runtime_configuration_errors(self) -> list[str]:
        """返回真正启动外部 Adapter 前必须修复的配置问题。"""
        errors = self.chat_configuration_errors()
        if self.embed_provider == "api" and _is_placeholder_secret(self.openai_api_key):
            errors.append("EMBED_PROVIDER=api 时必须配置 OPENAI_API_KEY")
        if self.risk_pilot_langsmith_enabled:
            if _is_placeholder_secret(self.langsmith_api_key):
                errors.append("启用 LangSmith 时必须配置 LANGSMITH_API_KEY")
            if not self.langsmith_hash_salt or len(self.langsmith_hash_salt) < 16:
                errors.append("启用 LangSmith 时 LANGSMITH_HASH_SALT 至少需要 16 个字符")
        if self.otel_enabled and not self.otel_exporter_otlp_endpoint:
            errors.append("启用 OpenTelemetry 时必须配置 OTEL_EXPORTER_OTLP_ENDPOINT")
        if len(self.observability_hash_salt) < 16:
            errors.append("OBSERVABILITY_HASH_SALT 至少需要 16 个字符")
        return errors

    def validate_runtime_configuration(self) -> None:
        """显式启动门禁；离线 import、领域测试和 CLI 工具不调用。"""
        errors = self.runtime_configuration_errors()
        if errors:
            details = "\n- ".join(errors)
            raise RuntimeConfigurationError(
                "RiskPilot 运行配置无效：\n"
                f"- {details}\n"
                "请按 .env.example 配置所选模型通道；"
                "API 使用 API Key，Traex 使用已登录的 Trae CLI，Embedding 单独配置。"
            )


settings = Settings()

# 确保关键目录存在
os.makedirs(settings.upload_dir, exist_ok=True)
os.makedirs(settings.chroma_persist_dir, exist_ok=True)
os.makedirs(settings.object_store_dir, exist_ok=True)
os.makedirs("logs", exist_ok=True)
