"""SSRF-safe HTTP 客户端离线测试。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from infra.web import SafeHttpClient, SafeHttpResponse, validate_public_url
from infra.web import safe_http as safe_http_module
from ingestion.web_loader import WebLoader


@dataclass
class _QueuedResponse:
    status_code: int = 200
    headers: dict[str, str] | None = None
    content: bytes = b"<html><body>ok</body></html>"


class _FakeTransport:
    def __init__(self, responses: list[_QueuedResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def request(
        self,
        *,
        url: str,
        resolved_ip: str,
        timeout_seconds: float,
        headers: dict[str, str],
        max_response_bytes: int,
    ) -> SafeHttpResponse:
        self.calls.append(
            {
                "url": url,
                "resolved_ip": resolved_ip,
                "timeout_seconds": timeout_seconds,
                "headers": headers,
                "max_response_bytes": max_response_bytes,
            }
        )
        queued = self.responses.pop(0)
        content = queued.content
        if len(content) > max_response_bytes:
            raise ValueError("网页响应超过大小限制")
        return SafeHttpResponse(
            url=url,
            status_code=queued.status_code,
            headers={
                key.lower(): value
                for key, value in (
                    queued.headers or {"content-type": "text/html; charset=utf-8"}
                ).items()
            },
            content=content,
        )


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost/admin",
        "http://127.0.0.1/admin",
        "http://10.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/admin",
        "http://user:pass@example.com/",
    ],
)
def test_non_public_urls_are_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        validate_public_url(url)


def test_mixed_public_private_dns_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        safe_http_module,
        "_resolve_addresses",
        lambda host: ["8.8.8.8", "10.0.0.1"],
    )

    with pytest.raises(ValueError, match="非公网"):
        validate_public_url("https://example.com/path")


def test_transport_receives_pinned_validated_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        safe_http_module,
        "_resolve_addresses",
        lambda host: ["8.8.8.8"] if host == "example.com" else [host],
    )
    transport = _FakeTransport([_QueuedResponse()])
    client = SafeHttpClient(transport=transport)

    response = client.get("https://Example.COM/a#fragment")

    assert response.text == "<html><body>ok</body></html>"
    assert transport.calls[0]["url"] == "https://example.com/a"
    assert transport.calls[0]["resolved_ip"] == "8.8.8.8"


def test_redirect_to_private_address_is_blocked_before_second_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        safe_http_module,
        "_resolve_addresses",
        lambda host: ["8.8.8.8"] if host == "example.com" else [host],
    )
    transport = _FakeTransport(
        [
            _QueuedResponse(
                status_code=302,
                headers={
                    "content-type": "text/html",
                    "location": "http://127.0.0.1/admin",
                },
                content=b"",
            )
        ]
    )
    client = SafeHttpClient(transport=transport)

    with pytest.raises(ValueError, match="非公网"):
        client.get("https://example.com/start")

    assert len(transport.calls) == 1


def test_redirect_limit_content_type_and_response_size_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        safe_http_module,
        "_resolve_addresses",
        lambda host: ["8.8.8.8"],
    )
    redirect_transport = _FakeTransport(
        [
            _QueuedResponse(
                status_code=302,
                headers={"content-type": "text/html", "location": "/again"},
                content=b"",
            )
        ]
    )
    with pytest.raises(ValueError, match="重定向次数"):
        SafeHttpClient(max_redirects=0, transport=redirect_transport).get(
            "https://example.com/start"
        )

    binary_transport = _FakeTransport(
        [
            _QueuedResponse(
                headers={"content-type": "application/octet-stream"},
            )
        ]
    )
    with pytest.raises(ValueError, match="类型不允许"):
        SafeHttpClient(transport=binary_transport).get("https://example.com/file")

    oversized_transport = _FakeTransport([_QueuedResponse(content=b"x" * 11)])
    with pytest.raises(ValueError, match="大小限制"):
        SafeHttpClient(
            max_response_bytes=10,
            transport=oversized_transport,
        ).get("https://example.com/large")


def test_web_loader_uses_safe_client_and_preserves_final_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        safe_http_module,
        "_resolve_addresses",
        lambda host: ["8.8.8.8"],
    )
    transport = _FakeTransport(
        [
            _QueuedResponse(
                content=(
                    "<html><head><title>安全页面</title></head>"
                    "<body><main>正文内容</main></body></html>"
                ).encode()
            )
        ]
    )
    loader = WebLoader(safe_http=SafeHttpClient(transport=transport))

    document = loader.load("https://example.com/article")

    assert document.content == "正文内容"
    assert document.title == "安全页面"
    assert document.source_url == "https://example.com/article"
