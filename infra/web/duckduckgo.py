"""`WebSearchPort` 实现：包装 retrieval/agent/web_searcher.WebSearcher。

虽然命名为 DuckDuckGo，实际后端按既有实现优先使用 Bing 国内可达性更好。
"""

from __future__ import annotations

from typing import Protocol

from domain.models import WebResult


class _WebSearcherLike(Protocol):
    def search(self, query: str, max_results: int = 3) -> list: ...


class DuckDuckGoAdapter:
    """实现 `WebSearchPort`，把 `WebSearchResult` dataclass 转换为 domain `WebResult`。"""

    def __init__(self, searcher: _WebSearcherLike | None = None) -> None:
        if searcher is None:
            from retrieval.agent.web_searcher import WebSearcher

            searcher = WebSearcher()
        self._searcher = searcher

    def search(self, query: str, max_results: int = 3) -> list[WebResult]:
        raw = self._searcher.search(query, max_results=max_results)
        out: list[WebResult] = []
        for r in raw:
            url = getattr(r, "url", "") or ""
            if not url:
                continue  # WebResult.url 有 min_length=1
            out.append(
                WebResult(
                    title=getattr(r, "title", "") or "",
                    url=url,
                    snippet=getattr(r, "snippet", "") or "",
                )
            )
        return out
