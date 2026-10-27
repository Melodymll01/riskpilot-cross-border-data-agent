"""网页加载器：抓取单个 URL 的正文内容，输出统一的 RawDocument。"""

import logging
import re
from dataclasses import dataclass
from urllib.parse import urldefrag

from bs4 import BeautifulSoup
from bs4.element import Tag

from infra.web.safe_http import SafeHttpClient
from ingestion.unified_loader import RawDocument

logger = logging.getLogger(__name__)

# 请求超时（秒）
REQUEST_TIMEOUT = 30

# 官网站点常把导航和面包屑放在普通 div 中，不能只依赖 nav/header/footer 标签。
_HIGH_CONFIDENCE_BODY_SELECTORS = (
    "#BodyLabel",
    "[itemprop='articleBody']",
    ".TRS_Editor",
    ".article-content",
    ".article-body",
    ".detail-content",
    ".news-content",
    "article",
)
_GENERIC_CANDIDATE_SELECTORS = (
    "main",
    "[role='main']",
    ".main-content",
    ".main",
    "section",
    "div",
    "body",
)
_BOILERPLATE_LINES = {
    "设为首页",
    "加入收藏",
    "手机版",
    "繁体",
    "搜索",
    "首页",
    "首 页",
    "正文",
    "时政要闻",
    "网信政务",
    "互动服务",
    "热点专题",
    "当前位置：",
    "【打印】",
    "【纠错】",
}
_BOILERPLATE_PHRASES = tuple(_BOILERPLATE_LINES | {"分享到", "打印", "纠错"})
_MIN_CONTENT_CHARS = 40
_MAX_LINK_DENSITY = 0.45


@dataclass(frozen=True)
class _ContentCandidate:
    node: Tag
    method: str
    text: str
    text_chars: int
    link_density: float
    paragraph_count: int
    boilerplate_count: int
    score: float


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

        # 页面 h1 比 <title> 更少携带站点后缀，优先作为展示标题。
        heading = soup.find("h1")
        title = (
            heading.get_text(" ", strip=True)
            if heading and heading.get_text(" ", strip=True)
            else soup.title.string.strip()
            if soup.title and soup.title.string
            else resp.url
        )

        # 移除 script / style 等无关标签
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        candidate = self._select_content(soup)
        text = candidate.text
        if candidate.text_chars < _MIN_CONTENT_CHARS:
            raise ValueError(f"网页正文过短，拒绝入库: {candidate.text_chars} 字符")
        if candidate.link_density > _MAX_LINK_DENSITY:
            raise ValueError(f"网页正文链接占比过高，可能仍是导航页: {candidate.link_density:.2f}")

        logger.info(
            "网页抓取完成: %s，正文长度=%d，提取方式=%s，链接占比=%.3f，模板行=%d",
            title,
            len(text),
            candidate.method,
            candidate.link_density,
            candidate.boilerplate_count,
        )

        final_url = urldefrag(resp.url).url

        return RawDocument(
            content=text,
            source_type="web",
            source_name=final_url,
            title=title,
            source_url=final_url,
            extra_metadata={
                "extraction_method": candidate.method,
                "extracted_text_chars": str(candidate.text_chars),
                "content_link_density": f"{candidate.link_density:.4f}",
                "boilerplate_lines_removed": str(candidate.boilerplate_count),
            },
        )

    @classmethod
    def _select_content(cls, soup: BeautifulSoup) -> _ContentCandidate:
        """正文 selector 优先；未知站点使用文本密度、段落数和链接占比评分。"""
        for selector in _HIGH_CONFIDENCE_BODY_SELECTORS:
            candidates = [
                cls._candidate(node, f"selector:{selector}") for node in soup.select(selector)
            ]
            usable = [item for item in candidates if item.text_chars >= _MIN_CONTENT_CHARS]
            if usable:
                return max(usable, key=lambda item: item.score)

        candidates_by_node: dict[int, _ContentCandidate] = {}
        for selector in _GENERIC_CANDIDATE_SELECTORS:
            for node in soup.select(selector):
                candidate = cls._candidate(node, f"scored:{selector}")
                if candidate.text_chars < _MIN_CONTENT_CHARS:
                    continue
                previous = candidates_by_node.get(id(node))
                if previous is None or candidate.score > previous.score:
                    candidates_by_node[id(node)] = candidate
        if not candidates_by_node:
            raise ValueError("无法解析网页正文内容")
        best = max(candidates_by_node.values(), key=lambda item: item.score)
        if best.link_density > _MAX_LINK_DENSITY:
            raise ValueError("网页正文候选全部以链接导航为主，拒绝入库")
        return best

    @classmethod
    def _candidate(cls, node: Tag, method: str) -> _ContentCandidate:
        raw_text = node.get_text(separator="\n", strip=True)
        text, removed = cls._remove_boilerplate(raw_text)
        text_chars = len(re.sub(r"\s+", "", text))
        link_chars = sum(
            len(re.sub(r"\s+", "", anchor.get_text(" ", strip=True)))
            for anchor in node.find_all("a")
        )
        link_density = link_chars / max(1, text_chars)
        paragraph_count = len(node.find_all("p"))
        punctuation_count = len(re.findall(r"[。！？；.!?]", text))
        score = (
            text_chars * (1.0 - min(link_density, 1.0) * 1.5)
            + paragraph_count * 45
            + punctuation_count * 5
            - removed * 70
        )
        return _ContentCandidate(
            node=node,
            method=method,
            text=text,
            text_chars=text_chars,
            link_density=link_density,
            paragraph_count=paragraph_count,
            boilerplate_count=removed,
            score=score,
        )

    @staticmethod
    def _remove_boilerplate(text: str) -> tuple[str, int]:
        """移除常见站点模板行，不改写正文句子。"""
        kept: list[str] = []
        removed = 0
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line in _BOILERPLATE_LINES:
                removed += 1
                continue
            if re.fullmatch(r"首页\s*[>/＞]\s*(正文)?", line):
                removed += 1
                continue
            if line.startswith("分享到") or line in {"打印", "纠错"}:
                removed += 1
                continue
            # 多个站点模板短语挤在一行时视为导航，而不是正文。
            if len(line) <= 120 and sum(phrase in line for phrase in _BOILERPLATE_PHRASES) >= 2:
                removed += 1
                continue
            kept.append(line)
        return "\n".join(kept).strip(), removed
