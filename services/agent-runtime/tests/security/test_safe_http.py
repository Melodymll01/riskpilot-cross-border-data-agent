"""SSRF-safe HTTP 客户端离线测试。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

import infra.web.safe_http as safe_http_module
from infra.web import SafeHttpClient, SafeHttpResponse, validate_public_url
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
                    "<body><main><p>这是用于验证通用正文抽取器的安全页面正文，"
                    "包含足够长度的连续内容和完整句子。</p>"
                    "<p>第二段继续说明网页正文应当保留，导航链接不应进入结果。</p>"
                    "</main></body></html>"
                ).encode()
            )
        ]
    )
    loader = WebLoader(safe_http=SafeHttpClient(transport=transport))

    document = loader.load("https://example.com/article")

    assert "这是用于验证通用正文抽取器的安全页面正文" in document.content
    assert "第二段继续说明网页正文应当保留" in document.content
    assert document.title == "安全页面"
    assert document.source_name == "https://example.com/article"
    assert document.source_url == "https://example.com/article"
    assert document.extra_metadata["extraction_method"].startswith("scored:")


def test_web_loader_prefers_official_body_label_and_removes_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        safe_http_module,
        "_resolve_addresses",
        lambda host: ["8.8.8.8"],
    )
    html = """
    <html><head><title>数据出境安全评估办法_官方页面</title></head><body>
      <div class="main">
        <div class="main-title"><h1>数据出境安全评估办法</h1></div>
        <div class="main-content"><div id="BodyLabel">
          <p>国家互联网信息办公室令</p>
          <p>第11号</p>
          <p>《数据出境安全评估办法》现予公布，自2022年9月1日起施行。</p>
          <p>第一条 为了规范数据出境活动，制定本办法。</p>
        </div></div>
        <div class="site-nav">设为首页 加入收藏 手机版 繁体 搜索</div>
      </div>
    </body></html>
    """
    transport = _FakeTransport([_QueuedResponse(content=html.encode())])
    loader = WebLoader(safe_http=SafeHttpClient(transport=transport))

    document = loader.load("https://example.com/law")

    assert "国家互联网信息办公室令" in document.content
    assert "第一条 为了规范数据出境活动，制定本办法。" in document.content
    assert "设为首页" not in document.content
    assert "加入收藏" not in document.content
    assert document.source_name == "https://example.com/law"
    assert document.title == "数据出境安全评估办法"
    assert document.extra_metadata["extraction_method"] == "selector:#BodyLabel"


def test_web_loader_rejects_navigation_only_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        safe_http_module,
        "_resolve_addresses",
        lambda host: ["8.8.8.8"],
    )
    links = "".join(f'<a href="/item/{i}">栏目导航链接{i}</a>' for i in range(40))
    html = f"<html><head><title>导航页</title></head><body><main>{links}</main></body></html>"
    transport = _FakeTransport([_QueuedResponse(content=html.encode())])
    loader = WebLoader(safe_http=SafeHttpClient(transport=transport))

    with pytest.raises(ValueError, match="链接|导航"):
        loader.load("https://example.com/navigation")
