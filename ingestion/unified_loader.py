"""统一文档对象定义 & 统一加载入口。

所有数据源（文件、网页）加载后都转换为 RawDocument，
后续处理链路只面向 RawDocument 工作，实现数据源解耦。
"""

from dataclasses import dataclass, field


@dataclass
class RawDocument:
    """
    统一的原始文档对象，是文件加载和网页抓取的共同输出。

    Attributes:
        content: 原始文本内容
        source_type: 来源类型，'file' 或 'web'
        source_name: 来源名称（文件名或网页标题）
        title: 文档标题
        source_url: 来源 URL（仅网页有）
    """

    content: str
    source_type: str  # "file" | "web"
    source_name: str
    title: str
    source_url: str | None = None
    extra_metadata: dict = field(default_factory=dict)


class UnifiedLoader:
    """
    统一加载入口，根据来源类型调度到对应的加载器，
    确保所有数据源输出相同格式的 RawDocument。
    """

    def __init__(self):
        # 延迟导入，避免循环引用
        from ingestion.file_loader import FileLoader
        from ingestion.web_loader import WebLoader

        self.file_loader = FileLoader()
        self.web_loader = WebLoader()

    def load_file(self, file_path: str, original_filename: str | None = None) -> RawDocument:
        """加载本地文件，返回统一文档对象。"""
        return self.file_loader.load(file_path, original_filename)

    def load_web(self, url: str) -> RawDocument:
        """加载网页内容，返回统一文档对象。"""
        return self.web_loader.load(url)
