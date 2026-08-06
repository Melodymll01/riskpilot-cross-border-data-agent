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


# === V2 Workspace / Case ===


class WorkspaceNotFound(DomainError):
    """按 `workspace_id` 找不到工作空间或当前用户不可见。"""


class WorkspaceAccessDenied(DomainError):
    """用户属于 Workspace，但角色不足以执行当前操作。"""

    def __init__(self, workspace_id: str, user_id: str, action: str) -> None:
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.action = action
        super().__init__(f"用户 {user_id!r} 无权在工作空间 {workspace_id!r} 执行 {action!r}")


class CaseNotFound(DomainError):
    """按 `case_id` 找不到案件或当前用户不可见。"""


class CaseArchived(DomainError):
    """归档案件不允许继续修改。"""

    def __init__(self, case_id: str) -> None:
        self.case_id = case_id
        super().__init__(f"案件 {case_id!r} 已归档，不允许继续修改")


class InvalidCaseTransition(DomainError):
    """案件状态转换不符合领域状态机。"""

    def __init__(self, case_id: str, source: str, target: str) -> None:
        self.case_id = case_id
        self.source = source
        self.target = target
        super().__init__(f"案件 {case_id!r} 不允许从 {source!r} 转换到 {target!r}")


# === V2 Document / Processing ===


class DocumentNotFound(DomainError):
    """按 `document_id` 找不到文档或当前用户不可见。"""


class ProcessingJobNotFound(DomainError):
    """按 `job_id` 找不到处理任务或当前用户不可见。"""


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


# === V2 Fact / Policy ===


class CaseFactNotFound(DomainError):
    """按 `fact_id` 找不到案件事实或当前用户不可见。"""


class InvalidCaseFactTransition(DomainError):
    """案件事实状态转换不符合领域状态机。"""

    def __init__(self, fact_id: str, source: str, target: str) -> None:
        self.fact_id = fact_id
        self.source = source
        self.target = target
        super().__init__(f"案件事实 {fact_id!r} 不允许从 {source!r} 转换到 {target!r}")


class PolicyRuleNotFound(DomainError):
    """按 rule_id + ruleset_version 找不到规则。"""


class AssessmentNotFound(DomainError):
    """按 assessment_id 找不到评估或当前用户不可见。"""


class InvalidAssessmentTransition(DomainError):
    """Assessment 状态转换不符合领域状态机。"""

    def __init__(self, assessment_id: str, source: str, target: str) -> None:
        self.assessment_id = assessment_id
        self.source = source
        self.target = target
        super().__init__(f"Assessment {assessment_id!r} 不允许从 {source!r} 转换到 {target!r}")


# === Tool / Agent ===


class ToolNotFound(DomainError):
    """Agent 想调用的工具未注册。"""


class ToolExecutionError(DomainError):
    """工具执行期错误，应携带 `tool_name` 与 `cause`。"""


# === 检索 / 记忆 / 外部服务 ===


class RetrievalError(DomainError):
    """检索链路失败（向量库 / BM25 / RRF / Reranker 任一环节）。"""


class MemoryError(DomainError):  # noqa: A001 - 明确覆盖语义；不与 builtins.MemoryError 混用
    """记忆层读写失败。"""


class EvidenceServiceError(DomainError):
    """风险画像 evidence 服务不可用或返回非法 schema。"""


class RiskProfileNotReady(DomainError):
    """风险画像模型尚未就绪（``schema-evidence-risk-profiling`` 仍在训练阶段）。

    占位适配器在被调用时应抛出此异常，由上层路由捕获并转成用户可读的"敬请期待"
    回执，避免误把占位 ``not_disclosed`` 当作真实模型输出。
    """


class WebSearchError(DomainError):
    """联网检索失败。"""
