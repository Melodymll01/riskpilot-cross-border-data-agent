"""网页加载器：抓取单个 URL 的正文内容，输出统一的 RawDocument。"""

import logging
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from ingestion.unified_loader import RawDocument

logger = logging.getLogger(__name__)

# 请求超时（秒）
REQUEST_TIMEOUT = 30


class WebLoader:
    """抓取单个网页正文并返回统一文档对象。"""

    def load(self, url: str) -> RawDocument:
        """
        抓取指定 URL 的网页正文。

        Args:
            url: 要抓取的网页地址
        """
        # 安全校验：只允许 http / https 协议
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"仅支持 http/https 协议，收到: {parsed.scheme}")

        logger.info(f"正在抓取网页: {url}")

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }

        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()

        # 尝试自动检测编码
        resp.encoding = resp.apparent_encoding or "utf-8"

        soup = BeautifulSoup(resp.text, "html.parser")

        # 提取标题
        title = soup.title.string.strip() if soup.title and soup.title.string else parsed.netloc

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
            source_url=url,
        )
