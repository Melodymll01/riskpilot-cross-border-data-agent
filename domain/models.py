"""Domain 层核心数据模型。

所有模型遵循统一约定：
- 继承 `BaseDomainModel`，自动启用 `frozen=True` + `extra="forbid"` + `validate_assignment=True`。
- 时间戳统一使用 Unix epoch seconds（float），不掺杂 datetime / 字符串。
- 不导入任何 infra / app / api 层模块；不允许在此层引入 IO 或网络依赖。
- `owner_id` 命名空间见 ADR-008：`"anon:{uuid}"` / `"github:{login}"` / `"google:{email}"` / `"email:{email}"`。

"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# === 公共类型别名 ===

Provider = Literal["github", "google", "magic_link", "anonymous"]
TaskState = Literal["planning", "gathering", "evaluating", "answering", "done"]
TaskMode = Literal["qa", "research", "profile"]
"""Task 业务模式：
- ``qa``       简单合规知识问答（默认；走 qa_chain 或 agent 浅路径）
- ``research`` 深度研究（agentic_rag + report_generator 长报告）
- ``profile``  企业风险画像（表单引导 → 结构化合规评估报告）
"""
MessageRole = Literal["user", "assistant", "tool", "system"]
ToolCallStatus = Literal["pending", "success", "failed", "timeout"]
Corpus = Literal["law", "user_docs"]


class BaseDomainModel(BaseModel):
    """所有 domain 模型的统一基类。"""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=False,
    )


# === 身份 ===


class User(BaseDomainModel):
    """统一身份模型。

    `user_id` 必须形如 `"<namespace>:<id>"`，命名空间见 ADR-008。
    匿名用户的 `provider` 为 `"anonymous"`，`provider_id` 与 `user_id` 中 uuid 部分一致。
    """

    user_id: str = Field(min_length=1)
    provider: Provider
    provider_id: str = Field(min_length=1)
    email: str | None = None
    display_name: str = Field(min_length=1)
    avatar_url: str | None = None
    created_at: float
    last_active_at: float


# === 任务 / 消息 / 工具调用 / 工件 ===


class Citation(BaseDomainModel):
    """检索/外部信息引用元数据，附挂在 `Message.citations` 中。"""

    source_type: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    title: str = ""
    source_url: str | None = None
    text_snippet: str = ""


class Message(BaseDomainModel):
    """对话历史中的单条消息。

    `tool_call_id` 仅在 `role="tool"` 时携带，关联到 `ToolCall.tool_call_id`。
    """

    msg_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    role: MessageRole
    content: str
    tool_call_id: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    created_at: float = Field(default_factory=lambda: time.time())


class Task(BaseDomainModel):
    """对话任务（取代 v1.0 的 conversation 概念）。

    一个 Task 对应一个完整的合规咨询主题，跨多轮消息；`owner_id` 必填用于权属过滤。
    `mode` 决定该 Task 走哪条业务路径；首次创建时确定，之后不再切换。
    """

    task_id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    title: str = ""
    state: TaskState = "planning"
    mode: TaskMode = "qa"
    user_goal: str = ""
    collected_facts: dict[str, Any] = Field(default_factory=dict)
    created_at: float
    updated_at: float


class ToolCall(BaseDomainModel):
    """Agent 一次工具调用的完整快照（输入 / 输出 / 状态 / 耗时）。"""

    tool_call_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    input_json: dict[str, Any] = Field(default_factory=dict)
    output_json: dict[str, Any] | None = None
    status: ToolCallStatus = "pending"
    duration_ms: int | None = Field(default=None, ge=0)
    created_at: float = Field(default_factory=lambda: time.time())


class Artifact(BaseDomainModel):
    """Agent 中间产出，独立于消息文本之外。

    示例 `artifact_type`：`"risk_profile"` / `"checklist"` / `"search_result"`。
    """

    artifact_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    artifact_type: str = Field(min_length=1)
    payload_json: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=lambda: time.time())


class MessageFeedback(BaseDomainModel):
    """用户对某条 assistant 回答的点赞/点踩反馈（供后台统计）。

    一条 assistant 消息至多一条反馈（按 `msg_id` 唯一）；`rating` 取 `"up"` / `"down"`。
    `owner_id` 用于权属隔离与按用户聚合。
    """

    msg_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    rating: Literal["up", "down"]
    created_at: float = Field(default_factory=lambda: time.time())
    updated_at: float = Field(default_factory=lambda: time.time())


# === 检索 / 外部搜索 / 风险画像 ===


class Chunk(BaseDomainModel):
    """检索返回的最小语义片段。

    `score` 越大越相关（已统一方向，infra 层负责把"距离"翻译为"相似度"）。
    `metadata` 用于附挂 source_url / category / chunk_index 等可选信息，不强 schema。
    `owner_id` 为 Step 025a 多租户隔离字段：None 表示公共语料（admin 入库），非空表示仅该 owner 可见。
    """

    chunk_id: str = Field(min_length=1)
    text: str
    source_type: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    title: str = ""
    source_url: str | None = None
    category: str = ""
    owner_id: str | None = None
    score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class WebResult(BaseDomainModel):
    """联网搜索单条结果。"""

    title: str
    url: str = Field(min_length=1)
    snippet: str = ""


# === 风险画像（schema-evidence-risk-profiling） ===

EvidenceState = Literal[
    "supported",
    "contradicted",
    "not_disclosed",
    "insufficiently_disclosed",
    "irrelevant",
]
"""evidence-state 五分类（与 schemas/evidence_v1/sample_schema_v1.json 对齐）。

- ``supported``                  文档显式支持目标命题
- ``contradicted``               文档反驳目标命题
- ``not_disclosed``              文档未涉及（≠ 事实为假）
- ``insufficiently_disclosed``   文档涉及但信息不足
- ``irrelevant``                 文档与目标命题无关
"""


class EvidenceSpan(BaseDomainModel):
    """证据 span：text 必填，start/end 可空（PrivacyQA 等无字符级偏移的来源）。"""

    text: str = Field(min_length=1)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)


class RiskProfile(BaseDomainModel):
    """风险画像评估结果（按 evidence-state v1 形态输出）。

    输入语义：``target`` 是用户提出的命题/问题/场景描述，``document`` 是可选的
    待对照文档（缺省时由上游检索器先拉证据再调用，或由模型直接返回 not_disclosed）。

    输出语义：
    - ``evidence_state`` 取自 ``EvidenceState`` 五分类
    - ``evidence_spans`` 支持文档证据时非空；其余分类可为空
    - ``explanation`` 是模型生成的中文解释，面向终端用户
    """

    target: str = Field(min_length=1)
    evidence_state: EvidenceState
    evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    explanation: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


# === LangGraph 深度研究 ===


class ResearchStep(BaseDomainModel):
    """Deep Research Graph 的单个可观察阶段。"""

    step_name: str = Field(min_length=1)
    description: str = ""
    result_summary: str = ""


class ResearchReport(BaseDomainModel):
    """LangGraph Deep Research 产出的结构化报告与阶段元数据。"""

    answer: str = ""
    citations: list[Citation] = Field(default_factory=list)
    question_type: str = ""
    question_type_label: str = ""
    retrieval_rounds: int = Field(default=0, ge=0)
    total_docs: int = Field(default=0, ge=0)
    web_search_used: bool = False
    refused: bool = False
    steps: list[ResearchStep] = Field(default_factory=list)


# === 知识库管理面（Step 016a） ===

KbSourceType = Literal["file", "web"]
"""知识库 source 来源类型：``file`` 上传文件 / ``web`` 抓取网页。"""


class KbDocument(BaseDomainModel):
    """知识库中按 ``source_name`` 聚合的"文档"视角。

    与 ``Chunk`` 区别：``Chunk`` 是检索返回的最小语义片段（含 score），
    本模型是管理面的"文档级聚合"，由 ``KbDocumentRepoPort.list_documents``
    返回，给前端 KB 面板渲染列表与总览。

    字段语义对齐现有 ``retrieval/search/vector_store.py:VectorStore.get_all_sources``
    输出，仅做 frozen + extra=forbid 的 schema 收紧。

    Step 025a 加 ``owner_id``：None 表示公共文档（admin 入库），非空表示私人文档。
    """

    source_name: str = Field(min_length=1)
    source_type: KbSourceType
    title: str = ""
    source_url: str | None = None
    chunk_count: int = Field(ge=0)
    category: str = ""
    owner_id: str | None = None


class KbChunk(BaseDomainModel):
    """待入库的 chunk（domain 视角；不含 embedding，由调用方并行提供）。

    与 ``Chunk`` 区别：本模型只用于写侧（入库），不带 ``score`` / ``metadata``
    自由字段；字段集合与 ``processing/metadata.py:ChunkWithMetadata`` 一一对齐，
    由 ``infra/kb/chroma_kb_repo.py`` 做形态转换。

    Step 025a 加 ``owner_id``：与 KbDocument 语义一致。
    """

    chunk_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    source_type: KbSourceType
    title: str = ""
    source_url: str | None = None
    chunk_index: int = Field(ge=0)
    category: str = ""
    owner_id: str | None = None

# === 记忆 ===


class SessionProfile(BaseDomainModel):
    """跨 task 的用户画像快照（按 `owner_id`）。

    `facts` 是 Agent 通过 ask_user / 推断累积出的事实集合；schema 故意宽松，
    避免在 PR-2 阶段过早收敛字段。
    """

    owner_id: str = Field(min_length=1)
    facts: dict[str, Any] = Field(default_factory=dict)
    updated_at: float = Field(default_factory=lambda: time.time())


class TaskSummary(BaseDomainModel):
    """L2 情景记忆：单个 task 的滚动摘要（Step 030b）。

    `msg_watermark` 记录"已摘要到第几条消息"（按 `list_messages` 顺序的索引），
    用于增量精炼的幂等：重试不重复摘要、漏摘下一轮按差额自动补。
    """

    task_id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    summary: str = ""
    msg_watermark: int = Field(default=0, ge=0)
    updated_at: float = Field(default_factory=lambda: time.time())


class Fact(BaseDomainModel):
    """语义记忆中的单条事实（按 `owner_id`，跨 task / 跨设备可召回）。

    Step 030c 固化字段：
    - ``confidence`` 置信度 [0,1]：首次提取 tentative（低），反复印证 reinforcement（升）。
    - ``salience``   显著性 [0,1]：写入门控（低于阈值不固化）+ 衰减打分输入。
    - ``last_used_at`` 最近被召回时间：衰减/容量淘汰用（recency）。
    - ``superseded_by`` 被哪条新事实取代（冲突遗忘）：非 None 即逻辑删除，永不召回。
    - ``source_episode`` 来源情景（task_id / summary 引用）：可解释性溯源。
    - ``source_message_id`` / ``source_quote``：AI 提取时经服务端逐字核验的用户原话。
    """

    fact_id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    salience: float = Field(default=0.5, ge=0.0, le=1.0)
    created_at: float = Field(default_factory=lambda: time.time())
    last_used_at: float = Field(default_factory=lambda: time.time())
    superseded_by: str | None = None
    source_episode: str = ""
    source_message_id: str = ""
    source_quote: str = ""


class MemoryRecallHit(BaseDomainModel):
    """单条长期记忆召回的可解释评分明细。"""

    rank: int = Field(ge=1)
    fact: Fact
    semantic_score: float = Field(ge=0.0, le=1.0)
    confidence_score: float = Field(ge=0.0, le=1.0)
    salience_score: float = Field(ge=0.0, le=1.0)
    freshness_score: float = Field(ge=0.0, le=1.0)
    final_score: float = Field(ge=0.0, le=1.0)


class MemoryRecallTrace(BaseDomainModel):
    """一次 L4 召回的决策轨迹，不包含向量、Prompt 或其他 owner 数据。"""

    owner_id: str = Field(min_length=1)
    query: str
    strategy_version: str = Field(min_length=1)
    candidate_count: int = Field(ge=0)
    eligible_count: int = Field(ge=0)
    rejected_counts: dict[str, int] = Field(default_factory=dict)
    hits: list[MemoryRecallHit] = Field(default_factory=list)


class ConsolidationState(BaseDomainModel):
    """L4 固化进度水位（按 task_id，Step 030c）。

    ``msg_watermark`` 记录"已固化到第几条消息"（按 ``list_messages`` 顺序），
    与 L2 摘要的 watermark 同构：重试不重复写 fact、漏固化下一轮按差额自动补。
    """

    task_id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    msg_watermark: int = Field(default=0, ge=0)
    updated_at: float = Field(default_factory=lambda: time.time())


class ForgetResult(BaseDomainModel):
    """主动遗忘（被遗忘权）执行结果：各层删除计数，供审计与可解释（Step 030d）。

    只记"删了多少条/哪些范围"，不回存被删内容——审计自身也守数据最小化。
    ``scope`` 标识遗忘范围：``"memory"`` 只清派生记忆（L2/L3/L4），
    ``"all"`` 额外级联删 L1 原始 task（连同消息）。
    """

    owner_id: str = Field(min_length=1)
    scope: str = "memory"
    summaries_deleted: int = Field(default=0, ge=0)
    profile_deleted: int = Field(default=0, ge=0)
    facts_deleted: int = Field(default=0, ge=0)
    states_deleted: int = Field(default=0, ge=0)
    tasks_deleted: int = Field(default=0, ge=0)

    @property
    def total_deleted(self) -> int:
        return (
            self.summaries_deleted
            + self.profile_deleted
            + self.facts_deleted
            + self.states_deleted
            + self.tasks_deleted
        )


class MemorySettings(BaseDomainModel):
    """每用户记忆偏好开关（按 `owner_id`，Step 031a）。

    开关：
    - ``use_saved_memory``：是否在回复时参考"保存的记忆"（L3 画像 + L4 语义事实），缺省开。
    当前对话内的上下文（L1 最近原文 + L2 本任务摘要）始终注入，不受开关控制。
    关掉只影响"注入与否"，不删除已存记忆（删除走主动遗忘 `forget`）。
    """

    owner_id: str = Field(min_length=1)
    use_saved_memory: bool = True
    updated_at: float = Field(default_factory=lambda: time.time())



# === 审计（Step 021） ===


class AuditEntry(BaseDomainModel):
    """admin 操作审计记录：不可变七元组流水账。

    用于满足合规审计要求（PIPL §55 等"日志留存"），让"谁、什么时间、对什么
    资源、做了什么、是否成功"可追溯。

    字段语义：
    - ``actor_id``    操作者，遵循 ``"<namespace>:<id>"`` 命名空间（与 ``User.user_id`` 一致）
    - ``action``      操作类型。当前枚举见 ``AuditAction`` 常量；保持 str 以便后续扩展
    - ``resource``    被操作资源标识，如 ``source_name``、URL 等业务主键
    - ``timestamp``   Unix epoch seconds（与 ``Task`` / ``Message`` 时间戳约定一致）
    - ``request_id``  关联 SSE / API 请求的 trace id；当前可为 ``None``，留位给后续 tracing
    - ``success``     ``True`` 表示主操作成功；失败时也要记一条（携带 ``error``）
    - ``error``       失败时的简短描述；``success=True`` 时应为 ``None``
    - ``extra_json``  扩展业务量化字段（如 ``chunk_count`` / ``file_size_bytes``）

    设计取舍：之所以**不**做单独的 ``ActionLiteral``，是为了让未来新增 action
    （如 ``"user.merge_owner"`` / ``"task.delete"``）零侵入；但 ``AuditAction``
    模块常量集中在一处便于检索。
    """

    actor_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    resource: str = Field(min_length=1)
    timestamp: float = Field(default_factory=lambda: time.time())
    request_id: str | None = None
    success: bool
    error: str | None = None
    extra_json: dict[str, Any] = Field(default_factory=dict)


class AuditAction:
    """审计 ``action`` 字段的预定义常量集。新 action 在此集中维护便于检索。"""

    KB_DELETE = "kb.delete"
    KB_INGEST_FILE = "kb.ingest_file"
    KB_INGEST_WEB = "kb.ingest_web"
    # ── Step 025c：登录侧 ────────────────────────────────────────────
    AUTH_LOGIN_SUCCESS = "auth.login_success"
    AUTH_LOGIN_FAILURE = "auth.login_failure"
    AUTH_ANONYMOUS_CREATE = "auth.anonymous_create"
    # ── Step 025e：登出侧 ────────────────────────────────────────────
    AUTH_LOGOUT = "auth.logout"
    # ── Step 030d：记忆侧（主动遗忘 / 画像更新）─────────────────────
    MEMORY_FORGET = "memory.forget"
    MEMORY_PROFILE_UPDATE = "memory.profile_update"
    # ── Step 031a：记忆开关（用户同意 / 偏好变更）────────────────────
    MEMORY_SETTINGS_UPDATE = "memory.settings_update"
    # ── Step 034：单条事实删除（被遗忘权细粒度）──────────────────────
    MEMORY_FACT_DELETE = "memory.fact_delete"
