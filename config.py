"""全局配置模块，基于 pydantic-settings 加载环境变量。"""

import os
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode


class RuntimeConfigurationError(RuntimeError):
    """应用启动配置不完整；模块导入和离线测试不应触发。"""


def _is_placeholder_secret(value: str | None) -> bool:
    if not value:
        return True
    normalized = value.strip().lower()
    return (
        normalized in {"sk-placeholder", "your-api-key-here"}
        or normalized.startswith("your-")
        or normalized.startswith("<")
    )


class Settings(BaseSettings):
    # ── 业务数据库 Profile ────────────────────────────────────────────────────
    storage_backend: Literal["sqlite", "postgres"] = "sqlite"
    vector_backend: Literal["chroma", "pgvector"] = "chroma"
    task_backend: Literal["manual", "celery"] = "manual"
    database_url: str = "postgresql+psycopg://riskpilot:riskpilot@127.0.0.1:5432/riskpilot"

    # ── 通道选择 ──────────────────────────────────────────────────────────────
    # "api"   → 使用远端 API（智谱 / OpenAI 等）
    # "local" → 使用本机 Ollama
    llm_provider: Literal["api", "local"] = "api"  # 对话 & 查询改写
    embed_provider: Literal["api", "local", "deterministic"] = "api"  # Embedding

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

    chat_temperature: float = Field(0.2, ge=0.0, le=2.0)
    chat_max_tokens: int = Field(4096, ge=100, le=8192)

    # 检索参数
    # chunk_size/overlap 由 eval_chunk_params.py 评测确定（top-k=2 命中率 93%，平均 top1 相似度 0.640）
    chunk_size: int = Field(400, ge=100, le=4000)
    chunk_overlap: int = Field(80, ge=0, le=500)
    top_k: int = Field(5, ge=1, le=50)
    max_top_k: int = Field(12, ge=1, le=50)
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

    # 混合检索 / RRF 融合
    enable_bm25_rrf: bool = True  # 启用 BM25 + RRF 融合（替代朴素 union+distance 排序）
    rrf_k: int = Field(60, ge=1, le=200)  # RRF 平滑常数（Cormack 2009 原论文默认 60）
    rrf_vector_weight: float = Field(1.0, ge=0.0, le=5.0)
    rrf_bm25_weight: float = Field(1.0, ge=0.0, le=5.0)

    # Deep Research 参数
    enable_web_search: bool = True  # 质量不足时是否允许联网搜索
    warmup_research_on_startup: bool = True  # 启动时预编译/预热 Research Graph

    # 存储路径
    chroma_persist_dir: str = "./data/chroma_db"
    upload_dir: str = "./data/uploads"
    object_store_backend: Literal["local", "s3"] = "local"
    object_store_dir: str = "./data/objects"
    s3_endpoint_url: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_bucket: str = "riskpilot"
    s3_region: str = "us-east-1"

    # 上传限制
    max_upload_mb: int = Field(50, ge=1, le=500)  # 最大上传文件大小（MB）
    # 小规模多模态图片证据：Chinese-CLIP 懒加载，默认只在图片接口首次使用时加载。
    visual_model_name: str = "OFA-Sys/chinese-clip-vit-base-patch16"
    visual_max_upload_mb: int = Field(10, ge=1, le=50)
    # schema-evidence-risk-profiling 推理服务；留空时 profile 模式明确返回未配置。
    risk_profile_api_base: str | None = None
    risk_profile_api_key: str | None = None
    risk_profile_timeout_seconds: float = Field(30.0, ge=1.0, le=300.0)

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
    prometheus_worker_port: int = Field(9101, ge=1024, le=65535)

    # 仅按显式价格表估算；默认 0 表示“未配置价格”，不伪造成本。
    llm_input_cost_per_1m_tokens: float = Field(0.0, ge=0.0)
    llm_output_cost_per_1m_tokens: float = Field(0.0, ge=0.0)
    llm_cost_currency: str = Field("unspecified", pattern=r"^(unspecified|[A-Z]{3})$")

    # Redis 在 Phase 4 引入 Celery 后成为生产必需依赖；未配置时 readiness 标记 disabled。
    redis_url: str | None = None
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None
    celery_queue: str = "riskpilot.documents"
    celery_max_retries: int = Field(3, ge=0, le=10)
    celery_retry_backoff_max_seconds: int = Field(300, ge=1, le=3600)
    celery_soft_time_limit_seconds: int = Field(900, ge=10, le=7200)
    celery_time_limit_seconds: int = Field(960, ge=11, le=7500)

    # API 限流
    rate_limit_enabled: bool = True  # 是否启用 v2 HTTP 限流（测试可关）
    rate_limit_default: str = "60/minute"  # 普通接口默认限流
    rate_limit_llm: str = "20/minute"  # LLM 相关接口（问答/研究）限流
    rate_limit_ingest: str = "10/minute"  # 入库接口限流

    # 流式超时
    stream_timeout: int = Field(600, ge=10, le=1800)  # SSE 流式生成总超时（秒）

    # CORS
    cors_origins: list[str] = ["*"]

    # 日志
    log_level: str = "INFO"

    # ── 应用层（Step 008 PR-5：DI 容器 + use case） ────────────────────────────
    sqlite_db_path: str = "./data/rag.sqlite3"
    langgraph_checkpoint_db_path: str = "./data/langgraph-checkpoints.sqlite3"
    agent_planner_backend: Literal["langchain", "deterministic"] = "langchain"
    fact_proposal_backend: Literal["langchain", "safe_empty"] = "langchain"
    agent_max_loop_count: int = Field(4, ge=1, le=20)
    agent_max_tool_calls: int = Field(12, ge=1, le=100)
    agent_max_tokens: int = Field(12000, ge=100, le=1_000_000)
    jwt_secret: str = "dev-jwt-secret-please-change-32-chars-minimum-length"
    jwt_ttl_seconds: int = Field(86400, ge=60, le=30 * 86400)  # 1min-30day
    github_client_id: str = "dev-placeholder-client-id"
    github_client_secret: str = "dev-placeholder-client-secret"
    # 默认匹配本地 uvicorn 端口 8765 + Step 010 新路由前缀 /api/v2
    # 生产可通过环境变量 GITHUB_REDIRECT_URI 覆盖
    github_redirect_uri: str = "http://127.0.0.1:8765/api/v2/auth/github/callback"

    # ── 管理员（Step 012-tail：权限基线） ─────────────────────────────────────
    # 命名空间需与 User.user_id 一致，例如 "github:melody-rabbit"。
    # .env 支持两种写法：
    #   逗号分隔：ADMIN_USER_IDS=github:foo,github:bar
    #   JSON 数组：ADMIN_USER_IDS=["github:foo","github:bar"]
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
    demo_login_enabled: bool = False
    demo_login_user_id: str = "github:riskpilot-demo-admin"
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
    # 差异化 TTL（逻辑遗忘）：读取时过滤过期记忆，过期原文/摘要永不注入。
    memory_l1_ttl_days: float = Field(30.0, ge=0.0, le=3650.0)
    memory_l2_ttl_days: float = Field(180.0, ge=0.0, le=3650.0)

    # ── 记忆 L4 固化管线（Step 030c）──────────────────────────────────────────
    # L4 语义事实：提取→验证→巩固（fork 异步）；禁用时退回 L1+L2。
    memory_consolidation_enabled: bool = True
    # 未固化消息数 ≥ 此值才触发一次增量固化（比 summary 阈值高，避免过早提取）。
    memory_consolidation_min_backlog: int = Field(30, ge=2, le=500)
    # task 收尾时固化（当前每轮调度，由 backlog 门控；任务关闭事件后续接入）。
    memory_consolidate_on_task_close: bool = True
    # L4 事实 TTL（天）与衰减系数；0 关闭 TTL 过滤。
    memory_l4_ttl_days: float = Field(365.0, ge=0.0, le=3650.0)
    memory_decay_lambda: float = Field(0.01, ge=0.0, le=10.0)
    # 单 owner 事实容量上限：巩固后超限按衰减分淘汰最低分。
    memory_fact_cap_per_owner: int = Field(500, ge=10, le=100000)
    # 注入 prompt 的 L4 召回事实条数；0 关闭 L4 注入。
    memory_fact_recall_k: int = Field(3, ge=0, le=20)
    # hybrid_v1 先扩大向量候选池，再融合语义/置信度/显著性/新鲜度重排。
    memory_recall_candidate_multiplier: int = Field(4, ge=1, le=20)
    memory_recall_semantic_weight: float = Field(0.65, ge=0.0, le=1.0)
    memory_recall_confidence_weight: float = Field(0.15, ge=0.0, le=1.0)
    memory_recall_salience_weight: float = Field(0.15, ge=0.0, le=1.0)
    memory_recall_freshness_weight: float = Field(0.05, ge=0.0, le=1.0)
    memory_recall_min_semantic_score: float = Field(0.25, ge=0.0, le=1.0)
    memory_recall_min_final_score: float = Field(0.35, ge=0.0, le=1.0)
    memory_recall_freshness_half_life_days: float = Field(90.0, ge=0.0, le=3650.0)
    # 写入门控阈值：显著性低于此值不固化（防污染）。
    memory_fact_salience_threshold: float = Field(0.5, ge=0.0, le=1.0)
    # 去重相似度门控：候选与最近邻 ≥ 此值视为重复（强化置信，不新增）。
    memory_fact_dedup_threshold: float = Field(0.88, ge=0.0, le=1.0)
    # 冲突相似度门控：相似度落在 [conflict, dedup) 视为更新/矛盾 → 旧事实标 superseded。
    memory_fact_conflict_threshold: float = Field(0.72, ge=0.0, le=1.0)

    # ── 记忆 L3 用户画像 + 主动遗忘（Step 030d）───────────────────────────────
    # L3 用户画像：跨 task 的稳定偏好聚合（无 TTL，靠主动遗忘清除）。
    # 起步只支持显式偏好声明 + 系统配置偏好（自动抽取推迟，见设计 §14.5）。
    memory_profile_enabled: bool = True
    # 注入 prompt 的画像偏好条数上限；0 关闭 L3 注入。
    memory_profile_max_facts: int = Field(8, ge=0, le=50)

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
        return self.local_chat_model if self.llm_provider == "local" else self.chat_model

    @property
    def effective_embed_base_url(self) -> str:
        return self.ollama_api_base if self.embed_provider == "local" else self.openai_api_base

    @property
    def effective_embed_api_key(self) -> str:
        if self.embed_provider in {"local", "deterministic"}:
            return "offline"
        return self.openai_api_key

    @property
    def effective_embed_model(self) -> str:
        if self.embed_provider == "deterministic":
            return "deterministic-sha256"
        return (
            self.local_embedding_model if self.embed_provider == "local" else self.embedding_model
        )

    def runtime_configuration_errors(self) -> list[str]:
        """返回真正启动外部 Adapter 前必须修复的配置问题。"""
        errors: list[str] = []
        if self.storage_backend == "postgres" and not self.database_url.startswith(
            ("postgresql+psycopg://", "postgresql://")
        ):
            errors.append("STORAGE_BACKEND=postgres 时 DATABASE_URL 必须是 PostgreSQL URL")
        if (self.storage_backend, self.vector_backend) not in {
            ("sqlite", "chroma"),
            ("postgres", "pgvector"),
        }:
            errors.append("仅支持 sqlite+chroma 本地 Profile 或 postgres+pgvector 生产 Profile")
        if self.vector_backend == "pgvector" and self.embedding_dimensions != 2048:
            errors.append("VECTOR_BACKEND=pgvector 时 EMBEDDING_DIMENSIONS 必须为 2048")
        if self.object_store_backend == "s3":
            if bool(self.s3_access_key_id) != bool(self.s3_secret_access_key):
                errors.append("S3_ACCESS_KEY_ID 与 S3_SECRET_ACCESS_KEY 必须同时配置或同时省略")
            if not self.s3_bucket.strip():
                errors.append("OBJECT_STORE_BACKEND=s3 时必须配置 S3_BUCKET")
        if self.task_backend == "celery":
            broker_url = self.celery_broker_url or self.redis_url
            if not broker_url or not broker_url.startswith(("redis://", "rediss://")):
                errors.append("TASK_BACKEND=celery 时必须配置 Redis CELERY_BROKER_URL 或 REDIS_URL")
            if self.storage_backend != "postgres" or self.vector_backend != "pgvector":
                errors.append("TASK_BACKEND=celery 时必须使用 postgres+pgvector 生产 Profile")
            if self.object_store_backend != "s3":
                errors.append("TASK_BACKEND=celery 时必须使用 S3/MinIO 对象存储")
        if self.celery_time_limit_seconds <= self.celery_soft_time_limit_seconds:
            errors.append("CELERY_TIME_LIMIT_SECONDS 必须大于 CELERY_SOFT_TIME_LIMIT_SECONDS")
        if self.llm_provider == "api" and _is_placeholder_secret(self.effective_chat_api_key):
            errors.append("LLM_PROVIDER=api 时必须配置 CHAT_API_KEY 或 OPENAI_API_KEY")
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
        if (
            self.llm_input_cost_per_1m_tokens > 0 or self.llm_output_cost_per_1m_tokens > 0
        ) and self.llm_cost_currency == "unspecified":
            errors.append("配置非零 LLM token 价格时必须显式配置 LLM_COST_CURRENCY")
        return errors

    def validate_runtime_configuration(self) -> None:
        """显式启动门禁；离线 import、领域测试和 CLI 工具不调用。"""
        errors = self.runtime_configuration_errors()
        if errors:
            details = "\n- ".join(errors)
            raise RuntimeConfigurationError(
                "RiskPilot 运行配置无效：\n"
                f"- {details}\n"
                "请复制 .env.example 为 .env 并配置真实凭据，"
                "或将 LLM_PROVIDER/EMBED_PROVIDER 切换为 local。"
            )


settings = Settings()

# 确保关键目录存在
os.makedirs(settings.upload_dir, exist_ok=True)
os.makedirs(settings.chroma_persist_dir, exist_ok=True)
os.makedirs(settings.object_store_dir, exist_ok=True)
os.makedirs("logs", exist_ok=True)
