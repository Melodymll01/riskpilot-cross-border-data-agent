"""DuckDuckGo/Bing HTML 搜索基础设施。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup

from infra.web.safe_http import SafeHttpClient

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class WebSearchResult:
    title: str
    url: str
    snippet: str
    full_text: str = ""


class WebSearcher:
    """优先 DuckDuckGo，失败后回退 Bing，并尝试抓取正文。"""

    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    def __init__(self, *, safe_http: SafeHttpClient | None = None) -> None:
        self._safe_http = safe_http or SafeHttpClient(
            timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
            max_redirects=3,
            max_response_bytes=2 * 1024 * 1024,
        )

    def search(self, query: str, max_results: int = 3) -> list[WebSearchResult]:
        if not query.strip() or max_results < 1:
            return []
        results: list[WebSearchResult] = []
        for backend_name, backend in (
            ("DuckDuckGo", self._search_duckduckgo),
            ("Bing", self._search_bing),
        ):
            try:
                results = backend(query, max_results)
            except Exception:
                logger.warning("%s 搜索异常", backend_name, exc_info=True)
                results = []
            if results:
                break
        enriched: list[WebSearchResult] = []
        for result in results:
            try:
                full_text = self._fetch_page_text(result.url)
            except Exception:
                full_text = ""
            enriched.append(
                WebSearchResult(
                    title=result.title,
                    url=result.url,
                    snippet=result.snippet,
                    full_text=full_text,
                )
            )
        return enriched

    def _search_duckduckgo(
        self,
        query: str,
        max_results: int,
    ) -> list[WebSearchResult]:
        response = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers=self._HEADERS,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results: list[WebSearchResult] = []
        for item in soup.select(".result")[:max_results]:
            title_element = item.select_one(".result__a")
            if title_element is None:
                continue
            url = str(title_element.get("href", ""))
            if "uddg=" in url:
                parsed = urlparse(url)
                url = unquote(parse_qs(parsed.query).get("uddg", [url])[0])
            if not url.startswith(("http://", "https://")):
                continue
            snippet_element = item.select_one(".result__snippet")
            results.append(
                WebSearchResult(
                    title=title_element.get_text(strip=True),
                    url=url,
                    snippet=(
                        snippet_element.get_text(strip=True) if snippet_element is not None else ""
                    ),
                )
            )
        return results

    def _search_bing(
        self,
        query: str,
        max_results: int,
    ) -> list[WebSearchResult]:
        response = requests.get(
            "https://www.bing.com/search",
            params={"q": query, "ensearch": "0", "mkt": "zh-CN"},
            headers=self._HEADERS,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results: list[WebSearchResult] = []
        for item in soup.select("li.b_algo"):
            title_element = item.select_one("h2 a")
            if title_element is None:
                continue
            url = str(title_element.get("href", ""))
            if not url.startswith(("http://", "https://")):
                continue
            snippet_element = (
                item.select_one(".b_caption p")
                or item.select_one("p")
                or item.select_one(".b_snippet")
            )
            results.append(
                WebSearchResult(
                    title=title_element.get_text(strip=True),
                    url=url,
                    snippet=(
                        snippet_element.get_text(strip=True) if snippet_element is not None else ""
                    ),
                )
            )
            if len(results) >= max_results:
                break
        return results

    def _fetch_page_text(self, url: str, max_chars: int = 2000) -> str:
        response = self._safe_http.get(
            url,
            headers=self._HEADERS,
        )
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        body = soup.find("article") or soup.find("main") or soup.find("body")
        if body is None:
            return ""
        return body.get_text(separator="\n", strip=True)[:max_chars]
