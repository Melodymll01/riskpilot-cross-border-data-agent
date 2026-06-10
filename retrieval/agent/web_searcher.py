"""联网搜索模块（Corrective RAG 补偿检索组件）。

当知识库检索质量不足时，通过网络搜索补充信息。
支持多种搜索后端，默认使用 DuckDuckGo 免费搜索。
"""

import logging
import time
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15


@dataclass
class WebSearchResult:
    """联网搜索结果。"""
    title: str
    url: str
    snippet: str
    full_text: str = ""   # 抓取的正文（可选）


class WebSearcher:
    """联网搜索器：通过 DuckDuckGo HTML 搜索获取补充信息。

    不依赖任何付费 API，直接解析 DuckDuckGo 的 HTML 结果页。
    """

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    def search(self, query: str, max_results: int = 3) -> List[WebSearchResult]:
        """
        执行联网搜索。

        Args:
            query: 搜索关键词
            max_results: 最多返回几条结果

        Returns:
            搜索结果列表
        """
        logger.info(f"联网搜索: '{query[:60]}', max_results={max_results}")

        # 后端调度：优先 DuckDuckGo（中文法规/政务类查询相关性显著更高），
        # 无结果或不可达时降级到 Bing 兜底。
        # 历史教训：Bing 的 HTML 抓取对中文长词（如"数据出境安全评估"）会退化成
        # 单字"数据"匹配，总能返回非空但毫不相关的结果，从而永久遮蔽更准的 DDG。
        results: List[WebSearchResult] = []
        for backend_name, backend in (
            ("DuckDuckGo", self._search_duckduckgo),
            ("Bing", self._search_bing),
        ):
            try:
                results = backend(query, max_results)
            except Exception as e:
                logger.warning(f"{backend_name} 搜索异常: {e}")
                results = []
            if results:
                logger.info(f"{backend_name} 搜索成功，返回 {len(results)} 条")
                break
            logger.warning(f"{backend_name} 搜索无结果，尝试下一个后端")

        if not results:
            logger.warning("所有搜索后端均无结果")
            return []

        # 尝试抓取每个结果的正文（增强上下文）
        for r in results:
            try:
                r.full_text = self._fetch_page_text(r.url)
            except Exception as e:
                logger.debug(f"抓取正文失败 ({r.url}): {e}")

        logger.info(f"联网搜索完成，获得 {len(results)} 条结果")
        return results

    def _search_duckduckgo(self, query: str, max_results: int) -> List[WebSearchResult]:
        """通过 DuckDuckGo HTML 搜索获取结果。"""
        try:
            url = "https://html.duckduckgo.com/html/"
            resp = requests.post(
                url,
                data={"q": query},
                headers=self.HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")
            results = []

            for item in soup.select(".result")[:max_results]:
                title_el = item.select_one(".result__a")
                snippet_el = item.select_one(".result__snippet")

                if not title_el:
                    continue

                title = title_el.get_text(strip=True)
                link = title_el.get("href", "")

                # DuckDuckGo 的链接可能是重定向 URL，提取实际 URL
                if "uddg=" in link:
                    from urllib.parse import unquote, parse_qs, urlparse
                    parsed = urlparse(link)
                    params = parse_qs(parsed.query)
                    link = unquote(params.get("uddg", [link])[0])

                snippet = snippet_el.get_text(strip=True) if snippet_el else ""

                results.append(WebSearchResult(
                    title=title,
                    url=link,
                    snippet=snippet,
                ))

            return results

        except Exception as e:
            logger.warning(f"DuckDuckGo 搜索失败: {e}")
            return []

    def _search_bing(self, query: str, max_results: int) -> List[WebSearchResult]:
        """通过 Bing HTML 搜索获取结果（国内网络可访问）。"""
        try:
            url = "https://www.bing.com/search"
            resp = requests.get(
                url,
                params={"q": query, "ensearch": 0, "mkt": "zh-CN"},
                headers=self.HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")
            results: List[WebSearchResult] = []

            # Bing 搜索结果项位于 li.b_algo 下
            for item in soup.select("li.b_algo")[: max_results * 2]:
                title_el = item.select_one("h2 a")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                link = title_el.get("href", "")
                if not link or not link.startswith(("http://", "https://")):
                    continue

                snippet_el = (
                    item.select_one(".b_caption p")
                    or item.select_one("p")
                    or item.select_one(".b_snippet")
                )
                snippet = snippet_el.get_text(strip=True) if snippet_el else ""

                results.append(WebSearchResult(title=title, url=link, snippet=snippet))
                if len(results) >= max_results:
                    break

            return results

        except Exception as e:
            logger.warning(f"Bing 搜索失败: {e}")
            return []

    def _fetch_page_text(self, url: str, max_chars: int = 2000) -> str:
        """抓取网页正文文本（截取前 max_chars 字符）。"""
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return ""

        resp = requests.get(
            url, headers=self.HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True,
        )
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        body = soup.find("article") or soup.find("main") or soup.find("body")
        if not body:
            return ""

        text = body.get_text(separator="\n", strip=True)
        return text[:max_chars]

    def results_to_chunks(self, results: List[WebSearchResult]) -> List[Dict[str, Any]]:
        """将搜索结果转换为与知识库 chunk 相同的格式，方便统一处理。"""
        chunks = []
        for r in results:
            text = r.full_text if r.full_text else r.snippet
            if not text:
                continue
            chunks.append({
                "id": f"web_search_{hash(r.url) & 0xffffffff:08x}",
                "text": text,
                "metadata": {
                    "source_type": "web_search",
                    "source_name": r.title,
                    "title": r.title,
                    "source_url": r.url,
                    "category": "联网搜索",
                },
                "distance": 0.2,  # 联网搜索结果给较高的相关性
            })
        return chunks
