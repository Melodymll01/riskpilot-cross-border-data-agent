"""HttpRiskProfileClient 行为与协议测试。"""

from __future__ import annotations

import pytest
import requests

from domain.errors import RiskProfileNotReady, RiskProfileServiceError
from domain.ports import RiskProfilePort
from infra.risk_profile import HttpRiskProfileClient
from tests.fakes import FakeTrace


class _Response:
    def __init__(
        self,
        payload: object = None,
        *,
        status_code: int = 200,
        text: str = "",
        json_error: bool = False,
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text
        self._json_error = json_error

    def json(self):  # type: ignore[no-untyped-def]
        if self._json_error:
            raise ValueError("not json")
        return self._payload


class _Session:
    def __init__(self, response: _Response | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append({"url": url, **kwargs})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _payload() -> dict[str, object]:
    return {
        "target": "医药企业临床数据出境到德国总部",
        "evidence_state": "supported",
        "evidence_spans": [{"text": "合同约定传输至德国总部"}],
        "explanation": "文档显式支持该命题",
        "metadata": {"model": "evidence-v1"},
    }


def test_implements_port_and_parses_response() -> None:
    session = _Session(_Response({"data": _payload()}))
    trace = FakeTrace()
    client = HttpRiskProfileClient(
        base_url="http://risk-model",
        api_key="token",
        session=session,
        trace=trace,
    )

    result = client.assess(
        target="医药企业临床数据出境到德国总部",
        document="某临床试验合同片段……",
        language="zh",
    )

    assert isinstance(client, RiskProfilePort)
    assert result.evidence_state == "supported"
    assert result.evidence_spans[0].text == "合同约定传输至德国总部"
    assert session.calls[0]["url"] == "http://risk-model/v1/risk-profile"
    assert session.calls[0]["headers"]["Authorization"] == "Bearer token"  # type: ignore[index]
    metadata = trace.spans[0]["metadata"]
    assert metadata["target_length"] == len("医药企业临床数据出境到德国总部")
    assert metadata["document_length"] == len("某临床试验合同片段……")
    assert metadata["evidence_state"] == "supported"
    assert metadata["status"] == "completed"
    assert "某临床试验合同片段" not in str(metadata)


def test_missing_endpoint_raises_not_ready() -> None:
    client = HttpRiskProfileClient(base_url=None)
    with pytest.raises(RiskProfileNotReady, match="RISK_PROFILE_API_BASE"):
        client.assess("目标")


def test_connection_error_translated() -> None:
    client = HttpRiskProfileClient(
        base_url="http://risk-model",
        session=_Session(requests.ConnectionError("offline")),
    )
    with pytest.raises(RiskProfileNotReady, match="连接失败"):
        client.assess("目标")


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_Response(status_code=503, text="unavailable"), "HTTP 503"),
        (_Response(json_error=True), "非 JSON"),
        (_Response({"evidence_state": "bad"}), "RiskProfile schema"),
    ],
)
def test_invalid_responses_fail_closed(response: _Response, message: str) -> None:
    client = HttpRiskProfileClient(
        base_url="http://risk-model",
        session=_Session(response),
    )
    with pytest.raises(RiskProfileServiceError, match=message):
        client.assess("目标")
