"""隐私优先的 AI Trace 适配器。"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Literal

from langsmith import Client
from langsmith.run_helpers import trace, tracing_context

from domain.ports import TraceSpanPort

_ID_KEYS = {
    "actor_id",
    "assessment_id",
    "case_id",
    "owner_id",
    "run_id",
    "ruleset_version",
    "task_id",
    "thread_id",
    "workspace_id",
}
_HASHED_ID_KEYS = {f"{key}_hash" for key in _ID_KEYS}
_STRING_KEYS = {
    "decision",
    "error_type",
    "evidence_state",
    "framework",
    "interrupt_kind",
    "langgraph_node",
    "ls_method",
    "ls_model_name",
    "ls_model_type",
    "ls_provider",
    "mode",
    "model",
    "operation",
    "stage",
    "status",
    "workflow",
}
_NUMBER_KEYS = {
    "attachment_count",
    "completed_stage_count",
    "document_count",
    "document_length",
    "evidence_count",
    "langgraph_step",
    "message_length",
    "missing_fact_count",
    "pending_document_count",
    "query_length",
    "ready_document_count",
    "retrieval_rounds",
    "target_length",
    "tool_count",
    "top_k",
}
_BOOLEAN_KEYS = {
    "completed",
    "enable_web_search",
    "has_attachments",
    "interrupted",
    "refused",
    "resumed",
    "risk_profile_configured",
    "web_search_used",
}
_SAFE_STRING = re.compile(r"^[A-Za-z0-9_.:/ -]{1,120}$")
_REDACTED_ERROR = "[redacted by RiskPilot privacy policy]"
_ALLOWED_RUN_NAMES = {
    "agent",
    "assess",
    "generate",
    "model",
    "plan",
    "retrieve",
    "risk_profile_assess",
    "riskpilot.case_assessment.resume",
    "riskpilot.case_assessment.start",
    "riskpilot.copilot.run",
    "riskpilot.deep_research.run",
    "riskpilot.risk_profile.assess",
    "riskpilot_compliance_copilot",
    "search_law",
    "search_user_docs",
    "tools",
    "web_search",
}


class _NoopTraceSpan:
    def add_metadata(self, metadata: Mapping[str, Any]) -> None:
        del metadata


class NoopTraceAdapter:
    """默认 Trace 实现：不记录、不联网。"""

    @contextmanager
    def span(
        self,
        name: str,
        *,
        run_type: Literal["chain", "llm", "tool", "retriever"] = "chain",
        metadata: Mapping[str, Any] | None = None,
    ) -> Iterator[TraceSpanPort]:
        del name, run_type, metadata
        with tracing_context(enabled=False, parent=False):
            yield _NoopTraceSpan()


class _LangSmithTraceSpan:
    def __init__(self, run: Any, *, hash_salt: str) -> None:
        self._run = run
        self._hash_salt = hash_salt

    def add_metadata(self, metadata: Mapping[str, Any]) -> None:
        self._run.add_metadata(sanitize_trace_metadata(metadata, hash_salt=self._hash_salt))


class _PrivacySafeClient(Client):
    """在 LangSmith SDK 序列化完成后执行最终出站裁剪。"""

    def _run_transform(
        self,
        run: Any,
        update: bool = False,
        copy: bool = False,
    ) -> dict[str, Any]:
        transformed = super()._run_transform(run, update=update, copy=copy)
        transformed.pop("serialized", None)
        transformed.pop("events", None)
        transformed.pop("attachments", None)
        transformed["name"] = _safe_run_name(str(transformed.get("name") or ""))
        transformed["tags"] = [
            tag for tag in transformed.get("tags") or [] if tag in {"privacy-redacted", "riskpilot"}
        ]
        metadata = {}
        extra = transformed.get("extra")
        if isinstance(extra, dict) and isinstance(extra.get("metadata"), dict):
            metadata = extra["metadata"]
        transformed["extra"] = {"metadata": metadata}
        if transformed.get("error"):
            transformed["error"] = _REDACTED_ERROR
        return transformed


class LangSmithTraceAdapter:
    """显式启用的 LangSmith Adapter；正文、输出和异常文本不会上传。"""

    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str,
        project: str,
        sampling_rate: float,
        hash_salt: str,
    ) -> None:
        if not api_key.strip():
            raise ValueError("启用 LangSmith 时 LANGSMITH_API_KEY 必填")
        if len(hash_salt) < 16:
            raise ValueError("LANGSMITH_HASH_SALT 至少 16 个字符")
        if not 0.0 <= sampling_rate <= 1.0:
            raise ValueError("LANGSMITH_SAMPLING_RATE 必须在 0 到 1 之间")
        self._project = project if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", project) else "riskpilot"
        self._hash_salt = hash_salt
        self._client = _PrivacySafeClient(
            api_url=endpoint,
            api_key=api_key,
            auto_batch_tracing=False,
            hide_inputs=True,
            hide_outputs=True,
            anonymizer=self._anonymize,
            omit_traced_runtime_info=True,
            tracing_sampling_rate=sampling_rate,
        )

    @contextmanager
    def span(
        self,
        name: str,
        *,
        run_type: Literal["chain", "llm", "tool", "retriever"] = "chain",
        metadata: Mapping[str, Any] | None = None,
    ) -> Iterator[TraceSpanPort]:
        safe_metadata = sanitize_trace_metadata(
            metadata or {},
            hash_salt=self._hash_salt,
        )
        with (
            tracing_context(
                project_name=self._project,
                enabled=True,
                client=self._client,
                tags=["privacy-redacted", "riskpilot"],
            ),
            trace(
                _safe_run_name(name),
                run_type=run_type,
                inputs={},
                project_name=self._project,
                metadata=safe_metadata,
                client=self._client,
            ) as run,
        ):
            yield _LangSmithTraceSpan(run, hash_salt=self._hash_salt)

    def _anonymize(self, data: dict[str, Any]) -> dict[str, Any]:
        if set(data) == {"error"}:
            return {"error": _REDACTED_ERROR}
        return sanitize_trace_metadata(data, hash_salt=self._hash_salt)


def sanitize_trace_metadata(
    metadata: Mapping[str, Any],
    *,
    hash_salt: str,
) -> dict[str, str | int | float | bool]:
    """只保留白名单字段；业务标识符使用 HMAC，不保留可逆原值。"""
    sanitized: dict[str, str | int | float | bool] = {}
    for key, value in metadata.items():
        if key in _ID_KEYS and isinstance(value, str) and value:
            sanitized[f"{key}_hash"] = _hash_identifier(value, hash_salt)
        elif (
            key in _HASHED_ID_KEYS
            and isinstance(value, str)
            and re.fullmatch(r"[0-9a-f]{24}", value)
        ):
            sanitized[key] = value
        elif key in _STRING_KEYS and isinstance(value, str):
            sanitized[key] = value if _SAFE_STRING.fullmatch(value) else "[redacted]"
        elif (
            (
                key in _NUMBER_KEYS
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
            )
            or key in _BOOLEAN_KEYS
            and isinstance(value, bool)
        ):
            sanitized[key] = value
    return sanitized


def _hash_identifier(value: str, hash_salt: str) -> str:
    return hmac.new(
        hash_salt.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:24]


def _safe_run_name(value: str) -> str:
    return value if value in _ALLOWED_RUN_NAMES else "riskpilot.framework.operation"
