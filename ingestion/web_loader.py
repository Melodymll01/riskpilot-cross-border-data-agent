"""网页加载器：抓取单个 URL 的正文内容，输出统一的 RawDocument。"""

import logging

from bs4 import BeautifulSoup

from infra.web.safe_http import SafeHttpClient
from ingestion.unified_loader import RawDocument

logger = logging.getLogger(__name__)

# 请求超时（秒）
REQUEST_TIMEOUT = 30


class WebLoader:
    """抓取单个网页正文并返回统一文档对象。"""

    def __init__(self, *, safe_http: SafeHttpClient | None = None) -> None:
        self._safe_http = safe_http or SafeHttpClient(
            timeout_seconds=REQUEST_TIMEOUT,
            max_redirects=3,
            max_response_bytes=5 * 1024 * 1024,
        )

    def load(self, url: str) -> RawDocument:
        """
        抓取指定 URL 的网页正文。

        Args:
            url: 要抓取的网页地址
        """
        logger.info(f"正在抓取网页: {url}")

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }

        resp = self._safe_http.get(url, headers=headers)
        soup = BeautifulSoup(resp.text, "html.parser")

        # 提取标题
        title = soup.title.string.strip() if soup.title and soup.title.string else resp.url

        # 移除 script / style 等无关标签
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        # 提取正文文本
        body = soup.find("article") or soup.find("main") or soup.find("body")
        if body is None:
            raise ValueError("无法解析网页正文内容")

        text = body.get_text(separator="\n", strip=True)

        if not text.strip():
            raise ValueError("网页正文内容为空")

        logger.info(f"网页抓取完成: {title}，内容长度: {len(text)}")

        return RawDocument(
            content=text,
            source_type="web",
            source_name=title,
            title=title,
            source_url=resp.url,
        )
