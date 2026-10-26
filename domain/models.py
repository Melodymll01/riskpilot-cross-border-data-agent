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

from pydantic import BaseModel, ConfigDict, Field, model_validator

# === 公共类型别名 ===

Provider = Literal["github", "google", "magic_link", "anonymous"]
TaskState = Literal["planning", "gathering", "evaluating", "answering", "done"]
MessageRole = Literal["user", "assistant", "tool", "system"]
ToolCallStatus = Literal[
    "pending",
    "success",
    "failed",
    "timeout",
    "cancelled",
    "budget_exhausted",
]
Corpus = Literal["law", "user_docs"]
ConversationRunStatus = Literal[
    "running",
    "interrupted",
    "cancel_requested",
    "cancelled",
    "timed_out",
    "budget_exhausted",
    "completed",
    "failed",
]
ConversationRunStage = Literal[
    "agent",
    "tool",
    "generation",
    "verification",
    "completed",
    "cancelled",
    "timed_out",
    "budget_exhausted",
    "failed",
]


class BaseDomainModel(BaseModel):
    """所有 domain 模型的统一基类。"""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=False,
    )


class ChatResponse(BaseDomainModel):
    content: str
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    token_usage: int = Field(default=0, ge=0)
    usage_available: bool = False

    @model_validator(mode="after")
    def validate_usage(self) -> ChatResponse:
        if self.token_usage < self.input_tokens + self.output_tokens:
            raise ValueError("token_usage 不能小于 input_tokens + output_tokens")
        return self


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

    citation_id: str = ""
    source_id: str = ""
    source_type: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    title: str = ""
    source_url: str | None = None
    source_version: str | None = None
    text_snippet: str = ""
    chunk_id: str | None = None
    document_id: str | None = None
    document_version_id: str | None = None
    page_number: int | None = Field(default=None, ge=1)
    source_sha256: str | None = None
    clause_id: str | None = None


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
    """跨多轮消息的对话；``owner_id`` 用于权属隔离。"""

    task_id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    title: str = ""
    state: TaskState = "planning"
    user_goal: str = ""
    collected_facts: dict[str, Any] = Field(default_factory=dict)
    created_at: float
    updated_at: float


class ToolCall(BaseDomainModel):
    """Agent 一次工具调用的完整快照（输入 / 输出 / 状态 / 耗时）。"""

    tool_call_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    run_id: str | None = None
    tool_name: str = Field(min_length=1)
    input_json: dict[str, Any] = Field(default_factory=dict)
    output_json: dict[str, Any] | None = None
    status: ToolCallStatus = "pending"
    duration_ms: int | None = Field(default=None, ge=0)
    created_at: float = Field(default_factory=lambda: time.time())


class ConversationRun(BaseDomainModel):
    """一轮可恢复的对话执行；客户端只能持有 ``run_id``。"""

    run_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=8000)
    input_message_id: str = Field(min_length=1)
    attachment_doc_ids: list[str] = Field(default_factory=list, max_length=20)
    status: ConversationRunStatus = "running"
    stage: ConversationRunStage = "agent"
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    verified_citation_ids: list[str] = Field(default_factory=list)
    verified_context_message_ids: list[str] = Field(default_factory=list)
    clarification_fields: list[str] = Field(default_factory=list)
    max_duration_seconds: float = Field(default=120.0, gt=0)
    remaining_seconds: float = Field(default=120.0, ge=0)
    deadline_at: float | None = None
    max_model_calls: int = Field(default=12, ge=1)
    max_tool_calls: int = Field(default=6, ge=1)
    model_call_count: int = Field(default=0, ge=0)
    tool_call_count: int = Field(default=0, ge=0)
    model_retry_count: int = Field(default=0, ge=0)
    tool_retry_count: int = Field(default=0, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    usage_reported_model_calls: int = Field(default=0, ge=0)
    answer_message_id: str | None = None
    error_type: str | None = None
    end_reason: str | None = None
    cancel_requested_at: float | None = None
    finished_at: float | None = None
    created_at: float
    updated_at: float

    @model_validator(mode="after")
    def validate_run(self) -> ConversationRun:
        if len(self.attachment_doc_ids) != len(set(self.attachment_doc_ids)):
            raise ValueError("attachment_doc_ids 不能重复")
        if len(self.verified_citation_ids) != len(set(self.verified_citation_ids)):
            raise ValueError("verified_citation_ids 不能重复")
        if len(self.verified_context_message_ids) != len(set(self.verified_context_message_ids)):
            raise ValueError("verified_context_message_ids 不能重复")
        if len(self.clarification_fields) != len(set(self.clarification_fields)):
            raise ValueError("clarification_fields 不能重复")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at 不能早于 created_at")
        if self.remaining_seconds > self.max_duration_seconds:
            raise ValueError("remaining_seconds 不能超过 max_duration_seconds")
        if self.usage_reported_model_calls > self.model_call_count:
            raise ValueError("usage_reported_model_calls 不能超过 model_call_count")
        if self.status == "completed" and not self.answer_message_id:
            raise ValueError("completed run 必须记录 answer_message_id")
        return self


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


# === 检索 / 外部搜索 ===


class Chunk(BaseDomainModel):
    """检索返回的最小语义片段。

    `score` 越大越相关（已统一方向，infra 层负责把"距离"翻译为"相似度"）。
    `metadata` 用于附挂 source_url / category / chunk_index 等可选信息，不强 schema。
    `owner_id` 为多租户隔离字段：None 表示公共语料，非空表示仅该 owner 可见。
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


# === 知识材料 ===

KbSourceType = Literal["file", "web"]
"""知识库 source 来源类型：``file`` 上传文件 / ``web`` 抓取网页。"""


class KbDocument(BaseDomainModel):
    """知识库中按 ``source_name`` 聚合的"文档"视角。

    与 ``Chunk`` 区别：``Chunk`` 是检索返回的最小语义片段（含 score），
    本模型是管理面的"文档级聚合"，由 ``KbDocumentRepoPort.list_documents``
    返回，给前端 KB 面板渲染列表与总览。

    字段语义对齐现有 ``retrieval/search/vector_store.py:VectorStore.get_all_sources``
    输出，仅做 frozen + extra=forbid 的 schema 收紧。

    ``owner_id`` 为 None 表示公共文档，非空表示私人文档。
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

    ``owner_id`` 与 KbDocument 语义一致。
    """

    chunk_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    embedding_text: str | None = Field(default=None, min_length=1)
    source_name: str = Field(min_length=1)
    source_type: KbSourceType
    title: str = ""
    source_url: str | None = None
    chunk_index: int = Field(ge=0)
    category: str = ""
    owner_id: str | None = None
    source_metadata: dict[str, str] = Field(default_factory=dict)


# === 记忆 ===


class TaskSummary(BaseDomainModel):
    """单个 task 的滚动摘要。

    `msg_watermark` 记录"已摘要到第几条消息"（按 `list_messages` 顺序的索引），
    用于增量精炼的幂等：重试不重复摘要、漏摘下一轮按差额自动补。
    """

    task_id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    summary: str = ""
    msg_watermark: int = Field(default=0, ge=0)
    updated_at: float = Field(default_factory=lambda: time.time())


# === 审计 ===


class AuditEntry(BaseDomainModel):
    """操作审计记录：不可变七元组流水账。

    用于满足合规审计要求（PIPL §55 等"日志留存"），让"谁、什么时间、对什么
    资源、做了什么、是否成功"可追溯。

    字段语义：
    - ``actor_id``    操作者，遵循 ``"<namespace>:<id>"`` 命名空间（与 ``User.user_id`` 一致）
    - ``action``      操作类型，当前枚举见 ``AuditAction`` 常量
    - ``resource``    被操作资源标识，如 ``source_name``、URL 等业务主键
    - ``timestamp``   Unix epoch seconds（与 ``Task`` / ``Message`` 时间戳约定一致）
    - ``request_id``  关联 SSE / API 请求，可为 ``None``
    - ``success``     ``True`` 表示主操作成功；失败时也要记一条（携带 ``error``）
    - ``error``       失败时的简短描述；``success=True`` 时应为 ``None``
    - ``extra_json``  扩展业务量化字段（如 ``chunk_count`` / ``file_size_bytes``）

    ``AuditAction`` 集中维护当前动作名称。
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
    AUTH_LOGIN_SUCCESS = "auth.login_success"
    AUTH_LOGIN_FAILURE = "auth.login_failure"
    AUTH_ANONYMOUS_CREATE = "auth.anonymous_create"
    AUTH_LOGOUT = "auth.logout"
