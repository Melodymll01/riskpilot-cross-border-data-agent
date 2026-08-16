"""schema-evidence-risk-profiling 远程推理客户端。"""

from __future__ import annotations

from typing import Any

import requests
from pydantic import ValidationError

from domain.errors import RiskProfileNotReady, RiskProfileServiceError
from domain.models import RiskProfile
from domain.ports import TracePort
from infra.observability import NoopTraceAdapter


class HttpRiskProfileClient:
    """调用 evidence-state 风险评估模型，并严格校验返回 schema。"""

    def __init__(
        self,
        *,
        base_url: str | None,
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        session: Any | None = None,
        trace: TracePort | None = None,
    ) -> None:
        self._base_url = (base_url or "").rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._session = session or requests.Session()
        self._trace = trace or NoopTraceAdapter()

    def assess(
        self,
        target: str,
        document: str | None = None,
        *,
        language: str = "zh",
    ) -> RiskProfile:
        if not target.strip():
            raise ValueError("target 不能为空")
        with self._trace.span(
            "riskpilot.risk_profile.assess",
            run_type="chain",
            metadata={
                "operation": "assess",
                "target_length": len(target),
                "document_length": len(document or ""),
                "risk_profile_configured": bool(self._base_url),
            },
        ) as span:
            try:
                result = self._assess(target, document, language=language)
            except Exception as exc:
                span.add_metadata({"error_type": type(exc).__name__})
                raise
            span.add_metadata(
                {
                    "evidence_state": result.evidence_state,
                    "evidence_count": len(result.evidence_spans),
                    "status": "completed",
                }
            )
            return result

    def _assess(
        self,
        target: str,
        document: str | None,
        *,
        language: str,
    ) -> RiskProfile:
        if not self._base_url:
            raise RiskProfileNotReady(
                "未配置风险画像模型服务地址 RISK_PROFILE_API_BASE"
            )
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            response = self._session.post(
                f"{self._base_url}/v1/risk-profile",
                json={
                    "target": target,
                    "document": document,
                    "language": language,
                },
                headers=headers,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise RiskProfileNotReady(f"风险画像模型服务连接失败: {exc}") from exc
        if not 200 <= response.status_code < 300:
            raise RiskProfileServiceError(
                f"风险画像模型返回 HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RiskProfileServiceError("风险画像模型返回非 JSON 响应") from exc
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            payload = payload["data"]
        try:
            return RiskProfile.model_validate(payload)
        except ValidationError as exc:
            raise RiskProfileServiceError(
                f"风险画像模型响应不符合 RiskProfile schema: {exc}"
            ) from exc
