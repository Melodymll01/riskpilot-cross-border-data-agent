"""Domain 层公开 API。

下游模块（app / infra / api / tests）只从本模块导入；不要绕过本模块直接访问子模块。
"""

from __future__ import annotations

from domain.errors import (
    AuthError,
    DomainError,
    EvidenceServiceError,
    InvalidToken,
    MemoryError,
    OAuthFlowError,
    OwnerMismatch,
    RetrievalError,
    TaskNotFound,
    ToolExecutionError,
    ToolNotFound,
    UserNotFound,
    WebSearchError,
)
from domain.models import (
    Artifact,
    BaseDomainModel,
    Chunk,
    Citation,
    Corpus,
    EvidenceJudgement,
    Fact,
    Message,
    MessageRole,
    Provider,
    SessionProfile,
    Task,
    TaskState,
    ToolCall,
    ToolCallStatus,
    User,
    WebResult,
)
from domain.ports import (
    AuthPort,
    ChatPort,
    EmbedPort,
    EvidencePort,
    MemoryPort,
    RetrievePort,
    TaskRepoPort,
    UserRepoPort,
    WebSearchPort,
)

__all__ = [
    # errors
    "DomainError",
    "AuthError",
    "InvalidToken",
    "OAuthFlowError",
    "UserNotFound",
    "TaskNotFound",
    "OwnerMismatch",
    "ToolNotFound",
    "ToolExecutionError",
    "RetrievalError",
    "MemoryError",
    "EvidenceServiceError",
    "WebSearchError",
    # models
    "BaseDomainModel",
    "User",
    "Task",
    "Message",
    "ToolCall",
    "Artifact",
    "Citation",
    "Chunk",
    "WebResult",
    "EvidenceJudgement",
    "SessionProfile",
    "Fact",
    "Provider",
    "TaskState",
    "MessageRole",
    "ToolCallStatus",
    "Corpus",
    # ports
    "AuthPort",
    "UserRepoPort",
    "TaskRepoPort",
    "EmbedPort",
    "ChatPort",
    "RetrievePort",
    "EvidencePort",
    "WebSearchPort",
    "MemoryPort",
]
