"""Domain 层异常树。

所有从 domain / app 层抛出的、表达"业务概念错误"的异常，必须继承自 `DomainError`。
infra 层的技术性异常（如 HTTPError / SQLite OperationalError）由 infra 层捕获并翻译
为 `DomainError` 子类，确保上层只感知业务语义。
"""

from __future__ import annotations


class DomainError(Exception):
    """业务异常根类。"""


# === 身份 / 鉴权 ===


class AuthError(DomainError):
    """鉴权失败统一基类。"""


class InvalidToken(AuthError):
    """JWT 无效、过期或签名错误。"""


class OAuthFlowError(AuthError):
    """OAuth 流程异常：state 不匹配 / code 失效 / provider 拒绝。"""


# === 用户 / 任务 ===


class UserNotFound(DomainError):
    """按 `user_id` 找不到用户。"""


class TaskNotFound(DomainError):
    """按 `task_id`(+`owner_id`) 找不到任务。"""


class OwnerMismatch(DomainError):
    """资源 `owner_id` 与当前请求者不匹配（越权访问）。"""


class ConversationRunConflict(DomainError):
    """对话 run 已被恢复、已结束，或不再允许当前状态转换。"""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"对话执行 {run_id!r} 已被其他请求恢复或已经结束")


class ConversationRunStopped(DomainError):
    """当前 run 已被运行控制终止，不能继续产生副作用或答案。"""

    def __init__(self, run_id: str, *, status: str, reason: str) -> None:
        self.run_id = run_id
        self.status = status
        self.reason = reason
        super().__init__(f"对话执行 {run_id!r} 已结束：{reason}")


# === Document / Processing ===


class DocumentNotFound(DomainError):
    """按 `document_id` 找不到文档或当前用户不可见。"""


class ProcessingJobNotFound(DomainError):
    """按 `job_id` 找不到处理任务或当前用户不可见。"""


class ProcessingJobConflict(DomainError):
    """处理任务 revision 已变化，当前执行器必须重新读取状态。"""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"处理任务 {job_id!r} 已被其他执行器更新，请重新读取后继续")


class UnsupportedDocumentType(DomainError):
    """文件扩展名或实际内容类型不受支持。"""


class InvalidDocumentContent(DomainError):
    """文件内容为空、损坏或与声明类型不一致。"""


class DocumentTooLarge(DomainError):
    """上传文件超过允许大小。"""


class InvalidDocumentTransition(DomainError):
    """文档状态转换不符合领域状态机。"""

    def __init__(self, document_id: str, source: str, target: str) -> None:
        self.document_id = document_id
        self.source = source
        self.target = target
        super().__init__(f"文档 {document_id!r} 不允许从 {source!r} 转换到 {target!r}")


class InvalidProcessingJobTransition(DomainError):
    """文档处理任务状态转换不符合领域状态机。"""

    def __init__(self, job_id: str, source: str, target: str) -> None:
        self.job_id = job_id
        self.source = source
        self.target = target
        super().__init__(f"处理任务 {job_id!r} 不允许从 {source!r} 转换到 {target!r}")
