"""防 SSRF 的受限 HTTP 文本客户端。"""

from __future__ import annotations

import ipaddress
import socket
import ssl
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin, urlsplit

import urllib3

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_DEFAULT_ALLOWED_TYPES = (
    "text/html",
    "text/plain",
    "application/xhtml+xml",
)


@dataclass(frozen=True)
class SafeHttpResponse:
    url: str
    status_code: int
    headers: dict[str, str]
    content: bytes

    @property
    def text(self) -> str:
        content_type = self.headers.get("content-type", "")
        charset = "utf-8"
        for part in content_type.split(";")[1:]:
            key, _, value = part.strip().partition("=")
            if key.lower() == "charset" and value:
                charset = value.strip("\"'")
                break
        try:
            return self.content.decode(charset, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")


class SafeHttpTransport(Protocol):
    def request(
        self,
        *,
        url: str,
        resolved_ip: str,
        timeout_seconds: float,
        headers: dict[str, str],
        max_response_bytes: int,
    ) -> SafeHttpResponse: ...


class Urllib3PinnedTransport:
    """连接到已校验 IP，同时保留原 Host 与 TLS hostname。"""

    def request(
        self,
        *,
        url: str,
        resolved_ip: str,
        timeout_seconds: float,
        headers: dict[str, str],
        max_response_bytes: int,
    ) -> SafeHttpResponse:
        parsed = urlsplit(url)
        host = parsed.hostname
        if host is None:
            raise ValueError("URL 缺少主机名")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        host_header = host if parsed.port is None else f"{host}:{parsed.port}"
        request_headers = {**headers, "Host": host_header}
        target = parsed.path or "/"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        pool: urllib3.HTTPConnectionPool
        if parsed.scheme == "https":
            pool = urllib3.HTTPSConnectionPool(
                resolved_ip,
                port=port,
                timeout=timeout_seconds,
                maxsize=1,
                block=True,
                assert_hostname=host,
                server_hostname=host,
                cert_reqs=ssl.CERT_REQUIRED,
            )
        else:
            pool = urllib3.HTTPConnectionPool(
                resolved_ip,
                port=port,
                timeout=timeout_seconds,
                maxsize=1,
                block=True,
            )
        try:
            response = pool.urlopen(
                "GET",
                target,
                headers=request_headers,
                redirect=False,
                preload_content=False,
                retries=False,
            )
            response_headers = {
                str(key).lower(): str(value) for key, value in response.headers.items()
            }
            declared = response_headers.get("content-length")
            if declared is not None and int(declared) > max_response_bytes:
                raise ValueError("网页响应超过大小限制")
            content = response.read(max_response_bytes + 1)
            if len(content) > max_response_bytes:
                raise ValueError("网页响应超过大小限制")
            return SafeHttpResponse(
                url=url,
                status_code=response.status,
                headers=response_headers,
                content=content,
            )
        finally:
            pool.close()


class SafeHttpClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        max_redirects: int = 3,
        max_response_bytes: int = 2 * 1024 * 1024,
        allowed_content_types: tuple[str, ...] = _DEFAULT_ALLOWED_TYPES,
        transport: SafeHttpTransport | None = None,
    ) -> None:
        if timeout_seconds <= 0 or max_redirects < 0 or max_response_bytes < 1:
            raise ValueError("SafeHttpClient 配置非法")
        self._timeout_seconds = timeout_seconds
        self._max_redirects = max_redirects
        self._max_response_bytes = max_response_bytes
        self._allowed_content_types = allowed_content_types
        self._transport = transport or Urllib3PinnedTransport()

    def get(self, url: str, *, headers: dict[str, str] | None = None) -> SafeHttpResponse:
        current = url
        visited: set[str] = set()
        for redirect_count in range(self._max_redirects + 1):
            normalized, addresses = validate_public_url(current)
            if normalized in visited:
                raise ValueError("网页重定向形成循环")
            visited.add(normalized)
            response = self._transport.request(
                url=normalized,
                resolved_ip=addresses[0],
                timeout_seconds=self._timeout_seconds,
                headers=headers or {},
                max_response_bytes=self._max_response_bytes,
            )
            if response.status_code in _REDIRECT_STATUSES:
                if redirect_count >= self._max_redirects:
                    raise ValueError("网页重定向次数超过限制")
                location = response.headers.get("location")
                if not location:
                    raise ValueError("网页重定向缺少 Location")
                current = urljoin(normalized, location)
                continue
            if response.status_code < 200 or response.status_code >= 300:
                raise ValueError(f"网页请求失败，HTTP {response.status_code}")
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type not in self._allowed_content_types:
                raise ValueError(f"网页响应类型不允许: {content_type or '<empty>'}")
            return response
        raise ValueError("网页重定向次数超过限制")


def validate_public_url(url: str) -> tuple[str, list[str]]:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("仅支持 http/https URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL 不允许携带凭据")
    host = parsed.hostname
    if host is None or not host.strip():
        raise ValueError("URL 缺少主机名")
    normalized_host = host.rstrip(".").lower()
    if normalized_host in {"localhost", "localhost.localdomain"} or normalized_host.endswith(
        ".localhost"
    ):
        raise ValueError("禁止访问 localhost")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL 端口非法") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("URL 端口非法")
    addresses = _resolve_addresses(normalized_host)
    if not addresses:
        raise ValueError("URL 主机无法解析")
    if any(not _is_public_address(address) for address in addresses):
        raise ValueError("禁止访问非公网地址")
    netloc = normalized_host
    if ":" in normalized_host and not normalized_host.startswith("["):
        netloc = f"[{normalized_host}]"
    if port is not None:
        netloc = f"{netloc}:{port}"
    path = parsed.path or "/"
    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=netloc,
        path=path,
        fragment="",
    ).geturl()
    return normalized, addresses


def _resolve_addresses(host: str) -> list[str]:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError("URL 主机无法解析") from exc
        return list(dict.fromkeys(str(info[4][0]) for info in infos))
    return [str(literal)]


def _is_public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return address.is_global and not any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )
