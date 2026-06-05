"""``StubRiskProfileService`` 行为测试 + ``RiskProfilePort`` 契约测试。"""

from __future__ import annotations

import pytest

from domain.errors import RiskProfileNotReady
from domain.models import RiskProfile
from domain.ports import RiskProfilePort
from infra.risk_profile import StubRiskProfileService


class TestStubSatisfiesPort:
    def test_default_stub_implements_port(self) -> None:
        assert isinstance(StubRiskProfileService(), RiskProfilePort)

    def test_placeholder_stub_implements_port(self) -> None:
        assert isinstance(StubRiskProfileService(mode="placeholder"), RiskProfilePort)

    def test_invalid_mode_rejected(self) -> None:
        with pytest.raises(ValueError, match="mode"):
            StubRiskProfileService(mode="bogus")  # type: ignore[arg-type]


class TestStubRaiseMode:
    def test_raises_not_ready(self) -> None:
        svc = StubRiskProfileService()  # 默认 raise
        with pytest.raises(RiskProfileNotReady):
            svc.assess(target="跨境电商日均向香港传 5 万条订单数据")

    def test_empty_target_rejected_before_raise(self) -> None:
        svc = StubRiskProfileService()
        with pytest.raises(ValueError, match="target"):
            svc.assess(target="")


class TestStubPlaceholderMode:
    def test_returns_risk_profile_with_stub_metadata(self) -> None:
        svc = StubRiskProfileService(mode="placeholder")
        result = svc.assess(
            target="医药企业临床数据出境到德国总部",
            document="某临床试验合同片段……",
            language="zh",
        )
        assert isinstance(result, RiskProfile)
        assert result.target == "医药企业临床数据出境到德国总部"
        assert result.evidence_state == "not_disclosed"
        assert result.evidence_spans == []
        assert result.metadata["stub"] is True
        assert result.metadata["language"] == "zh"
        assert result.metadata["has_document"] is True
        assert "尚未部署" in result.explanation

    def test_no_document_marks_metadata(self) -> None:
        svc = StubRiskProfileService(mode="placeholder")
        result = svc.assess(target="某场景命题")
        assert result.metadata["has_document"] is False
