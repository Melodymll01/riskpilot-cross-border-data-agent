"""Domain 层核心数据模型。

所有模型遵循统一约定：
- 继承 `BaseDomainModel`，自动启用 `frozen=True` + `extra="forbid"` + `validate_assignment=True`。
- 时间戳统一使用 Unix epoch seconds（float），不掺杂 datetime / 字符串。
- 不导入任何 infra / app / api 层模块；不允许在此层引入 IO 或网络依赖。
- `owner_id` 命名空间见 ADR-008：`"anon:{uuid}"` / `"github:{login}"` / `"google:{email}"` / `"email:{email}"`。

字段语义以 `docs/experiment_v1.md` §4.1 为准；本文件是 §4.1 的可执行落地。
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# === 公共类型别名 ===

Provider = Literal["github", "google", "magic_link", "anonymous"]
TaskState = Literal["planning", "gathering", "evaluating", "answering", "done"]
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
    """

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


# === 检索 / 外部搜索 / 风险画像 ===


class Chunk(BaseDomainModel):
    """检索返回的最小语义片段。

    `score` 越大越相关（已统一方向，infra 层负责把"距离"翻译为"相似度"）。
    `metadata` 用于附挂 source_url / category / chunk_index 等可选信息，不强 schema。
    """

    chunk_id: str = Field(min_length=1)
    text: str
    source_type: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    title: str = ""
    source_url: str | None = None
    category: str = ""
    score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class WebResult(BaseDomainModel):
    """联网搜索单条结果。"""

    title: str
    url: str = Field(min_length=1)
    snippet: str = ""


class EvidenceJudgement(BaseDomainModel):
    """风险画像服务对单条 factor 的判定快照。

    与 `risk/schema.py` 保持兼容；此处只暴露 domain 视角必需的字段，详细规则与
    factor 列表（F1..F6）由 `risk/factors.py` 维护，不进 domain。
    """

    factor_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    rationale: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


# === 记忆 ===


class SessionProfile(BaseDomainModel):
    """跨 task 的用户画像快照（按 `owner_id`）。

    `facts` 是 Agent 通过 ask_user / 推断累积出的事实集合；schema 故意宽松，
    避免在 PR-2 阶段过早收敛字段。
    """

    owner_id: str = Field(min_length=1)
    facts: dict[str, Any] = Field(default_factory=dict)
    updated_at: float = Field(default_factory=lambda: time.time())


class Fact(BaseDomainModel):
    """语义记忆中的单条事实（按 `owner_id`，跨 task / 跨设备可召回）。"""

    fact_id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=lambda: time.time())
