"""HTTP 层 Pydantic 请求/响应模型。

与 ``api/schemas.py`` （老版） 不复用 —— 命名空间隔离。
与 ``domain.models`` 不共用 —— domain 是 frozen + extra=forbid，HTTP 层需要
更宽松的输入校验和向后兼容的字段演进。
"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── 通用 ────────────────────────────────────────────────────────────────


class ErrorResponse(BaseModel):
    """统一错误响应（4xx/5xx 通用）。"""

    error_code: str
    message: str
    details: dict[str, Any] | None = None


class OkResponse(BaseModel):
    ok: bool = True


# ── Auth ────────────────────────────────────────────────────────────────


class UserOut(BaseModel):
    """对外暴露的用户视图（不含敏感字段）。"""

    user_id: str
    provider: str
    display_name: str | None = None
    email: str | None = None
    avatar_url: str | None = None
    is_admin: bool = False  # 命中 settings.admin_user_ids 时为 True；前端据此渲染管理入口


class WhoAmIResponse(BaseModel):
    """``GET /auth/me`` 返回；未登录时 ``user`` 字段为 None。"""

    authenticated: bool
    user: UserOut | None = None


class AnonymousLoginResponse(BaseModel):
    user: UserOut


class GithubLoginResponse(BaseModel):
    """``GET /auth/github/login`` 返回授权 URL（前端跳转）。"""

    authorize_url: str
    state: str


# ── Tasks ───────────────────────────────────────────────────────────────


class TaskOut(BaseModel):
    task_id: str
    owner_id: str
    title: str
    state: Literal["planning", "gathering", "evaluating", "answering", "done"]
    mode: Literal["qa", "research", "profile"] = "qa"
    user_goal: str
    collected_facts: dict[str, Any]
    created_at: float
    updated_at: float


class TaskListResponse(BaseModel):
    tasks: list[TaskOut]


class TaskCitationOut(BaseModel):
    source_type: str
    source_name: str
    title: str = ""
    source_url: str | None = None
    text_snippet: str = ""


class MessageOut(BaseModel):
    msg_id: str
    role: Literal["user", "assistant", "tool", "system"]
    content: str
    citations: list[TaskCitationOut] = Field(default_factory=list)
    created_at: float


class TaskDetailResponse(BaseModel):
    task: TaskOut
    messages: list[MessageOut]


class UpdateTaskRequest(BaseModel):
    """PATCH /tasks/{id}：当前只支持改 title。"""

    title: str | None = None
    collected_facts: dict[str, Any] | None = None


# ── Feedback（消息点赞/点踩） ─────────────────────────────────────────────


class FeedbackRequest(BaseModel):
    """POST /feedback：对某条 assistant 回答提交点赞/点踩。"""

    msg_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    # "up"=点赞 / "down"=点踩 / "none"=撤销
    rating: Literal["up", "down", "none"]


class FeedbackResponse(BaseModel):
    """提交后回传生效的 rating（撤销后为 None）。"""

    msg_id: str
    rating: Literal["up", "down"] | None = None


class FeedbackMapResponse(BaseModel):
    """GET /feedback?task_id=：返回该 task 下 {msg_id: rating} 映射。"""

    ratings: dict[str, str] = Field(default_factory=dict)


# ── Copilot ─────────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    """POST /copilot/chat[/stream] 请求体。"""

    model_config = ConfigDict(extra="ignore")

    task_id: str | None = None
    message: str = Field(min_length=1, max_length=8000)
    attachment_doc_ids: list[str] = Field(default_factory=list, max_length=20)
    # 仅在首条消息创建新 task 时生效；后续轮次服从已有 task.mode。
    mode: Literal["qa", "research", "profile"] = "qa"


class ChatEventOut(BaseModel):
    """同步模式返回；流式模式直接走 SSE 不用该模型。"""

    event_type: str
    payload: dict[str, Any]


class ChatResponse(BaseModel):
    task_id: str
    events: list[ChatEventOut]


# ── Documents (KB management, Step 016c) ───────────────────────────────


class KbDocumentOut(BaseModel):
    """对外的 KB 文档视图（按 ``source_name`` 聚合）。"""

    source_name: str
    source_type: Literal["file", "web"]
    title: str = ""
    source_url: str | None = None
    chunk_count: int = Field(ge=0)
    category: str = ""
    owner_id: str | None = None  # Step 025a: None=公共，非空=私人


class KbDocumentListResponse(BaseModel):
    documents: list[KbDocumentOut]
    total_chunks: int = Field(ge=0)


class KbDocumentStatsResponse(BaseModel):
    """``GET /documents/stats`` 返回：知识库总览统计。"""

    document_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)


class KbIngestResponse(BaseModel):
    """``POST /documents/file`` 与 ``POST /documents/web`` 共享返回结构。"""

    success: bool
    source_name: str = ""
    chunk_count: int = 0
    message: str = ""


class DeleteDocumentResponse(BaseModel):
    ok: bool = True
    source_name: str
    deleted_count: int = Field(ge=0)


class WebIngestRequest(BaseModel):
    """``POST /documents/web`` 请求体。"""

    url: str = Field(min_length=1, max_length=2000)
    category: str = Field(default="", max_length=100)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            msg = "仅支持 http/https 协议的 URL"
            raise ValueError(msg)
        if not parsed.netloc:
            msg = "URL 缺少域名"
            raise ValueError(msg)
        return v


# ── Audit log (Step 021) ───────────────────────────────────────────────


class AuditEntryOut(BaseModel):
    """对外的审计记录视图（HTTP 层 schema，与 ``domain.AuditEntry`` 对齐）。"""

    actor_id: str
    action: str
    resource: str
    timestamp: float
    request_id: str | None = None
    success: bool
    error: str | None = None
    extra_json: dict[str, Any] = Field(default_factory=dict)


class AuditLogListResponse(BaseModel):
    """``GET /audit/logs`` 返回：审计记录列表（按时间倒序）。"""

    entries: list[AuditEntryOut]
    count: int = Field(ge=0)


# ── Memory（Step 030d）──────────────────────────────────────────────────


class ProfileResponse(BaseModel):
    """``GET /memory/profile`` 返回：当前 owner 的 L3 用户画像。"""

    owner_id: str
    facts: dict[str, Any] = Field(default_factory=dict)
    updated_at: float


class ForgetRequest(BaseModel):
    """``POST /memory/forget`` 请求体：遗忘范围。

    ``scope="memory"`` 只清派生记忆（L2/L3/L4）；``"all"`` 额外删 L1 原始 task。
    """

    scope: Literal["memory", "all"] = "memory"


class ForgetResponse(BaseModel):
    """``POST /memory/forget`` 返回：各层删除计数（被遗忘权回执）。"""

    owner_id: str
    scope: str
    summaries_deleted: int = Field(ge=0)
    profile_deleted: int = Field(ge=0)
    facts_deleted: int = Field(ge=0)
    states_deleted: int = Field(ge=0)
    tasks_deleted: int = Field(ge=0)
    total_deleted: int = Field(ge=0)


# ── Memory Settings（Step 031a）─────────────────────────────────────────


class MemorySettingsResponse(BaseModel):
    """``GET/PUT /memory/settings`` 返回：当前 owner 的记忆开关。"""

    use_saved_memory: bool
    updated_at: float


class UpdateMemorySettingsRequest(BaseModel):
    """``PUT /memory/settings`` 请求体：部分更新（None 字段保持原值）。"""

    use_saved_memory: bool | None = None


class MemoryFactItem(BaseModel):
    """单条长期事实（管理面板展示）。"""

    fact_id: str
    text: str
    tags: list[str] = Field(default_factory=list)
    source_message_id: str
    source_quote: str
    created_at: float


class MemoryFactsResponse(BaseModel):
    """``GET /memory/facts`` 返回：当前生效的长期事实 + 容量信息。"""

    facts: list[MemoryFactItem] = Field(default_factory=list)
    count: int = Field(ge=0)
    cap: int = Field(ge=0)


class MemoryRecallExplainRequest(BaseModel):
    """``POST /memory/recall/explain`` 请求：解释指定问题会召回哪些长期事实。"""

    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=3, ge=1, le=20)


class MemoryRecallHitResponse(BaseModel):
    """单条召回命中的安全解释，不返回向量或内部 Prompt。"""

    rank: int = Field(ge=1)
    fact_id: str
    text: str
    tags: list[str] = Field(default_factory=list)
    source_message_id: str
    source_quote: str
    semantic_score: float = Field(ge=0.0, le=1.0)
    confidence_score: float = Field(ge=0.0, le=1.0)
    salience_score: float = Field(ge=0.0, le=1.0)
    freshness_score: float = Field(ge=0.0, le=1.0)
    final_score: float = Field(ge=0.0, le=1.0)


class MemoryRecallExplainResponse(BaseModel):
    """一次长期记忆召回轨迹。"""

    strategy_version: str
    candidate_count: int = Field(ge=0)
    eligible_count: int = Field(ge=0)
    rejected_counts: dict[str, int] = Field(default_factory=dict)
    hits: list[MemoryRecallHitResponse] = Field(default_factory=list)
