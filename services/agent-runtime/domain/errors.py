"""Runtime domain errors."""

from __future__ import annotations


class DomainError(Exception):
    """业务异常根类。"""


class AuthError(DomainError):
    """鉴权失败统一基类。"""


class RuntimeConflict(DomainError):
    """The requested run/attempt transition is not current or not allowed."""


class RuntimeStopped(DomainError):
    """The current attempt must stop before another side effect or proposal."""

    def __init__(self, run_id: str, *, status: str, reason: str) -> None:
        self.run_id = run_id
        self.status = status
        self.reason = reason
        super().__init__(f"runtime attempt {run_id!r} stopped: {reason}")


class UnsupportedDocumentType(DomainError):
    """文件扩展名或实际内容类型不受支持。"""


class InvalidDocumentContent(DomainError):
    """文件内容为空、损坏或与声明类型不一致。"""
