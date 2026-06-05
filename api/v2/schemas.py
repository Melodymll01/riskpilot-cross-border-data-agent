"""HTTP 层 Pydantic 请求/响应模型。

与 ``api/schemas.py`` （老版） 不复用 —— 命名空间隔离。
与 ``domain.models`` 不共用 —— domain 是 frozen + extra=forbid，HTTP 层需要
更宽松的输入校验和向后兼容的字段演进。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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
