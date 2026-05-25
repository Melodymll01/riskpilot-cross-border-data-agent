"""Pydantic 请求/响应模型定义。"""

from typing import List, Optional
from pydantic import BaseModel, Field, field_validator
from urllib.parse import urlparse


# ===================== 请求模型 =====================

class WebIngestRequest(BaseModel):
    """网页采集请求。"""
    url: str = Field(..., description="要采集的网页 URL", examples=["https://example.com/article"])
    category: str = Field(default="", description="文档分类标签：法规/政策/指南/标准")

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("仅支持 http/https 协议的 URL")
        if not parsed.netloc:
            raise ValueError("URL 缺少域名")
        return v


class AskRequest(BaseModel):
    """问答请求。"""
    question: str = Field(..., description="用户的问题", min_length=1, max_length=2000)
    top_k: int = Field(default=5, description="检索返回的最大结果数", ge=1, le=20)
    category: Optional[str] = Field(default=None, description="按文档分类过滤（法规/政策/指南）")


class RetrieveRequest(BaseModel):
    """纯检索请求（不调用 LLM，供 Agent 使用）。"""
    query: str = Field(..., description="检索查询", min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    category: Optional[str] = Field(default=None, description="按文档分类过滤")


# ===================== 响应模型 =====================

class IngestResponse(BaseModel):
    """知识接入响应。"""
    success: bool
    message: str
    source_name: str = ""
    chunk_count: int = 0


class CitationItem(BaseModel):
    """引用来源项。"""
    source_type: str = Field(..., description="来源类型: file/web")
    source_name: str = Field(..., description="来源名称")
    title: str = Field(default="", description="文档标题")
    source_url: Optional[str] = Field(default=None, description="来源 URL")
    text_snippet: str = Field(default="", description="原文片段")


class AskResponse(BaseModel):
    """问答响应。"""
    answer: str = Field(..., description="生成的回答")
    citations: List[CitationItem] = Field(default_factory=list, description="引用来源列表")
    has_enough_context: bool = Field(default=True, description="是否有足够的上下文来回答")


class RetrieveChunkItem(BaseModel):
    """检索结果中的单个文本块。"""
    text: str = Field(..., description="文本内容")
    source_type: str = ""
    source_name: str = ""
    title: str = ""
    source_url: Optional[str] = None
    category: str = ""
    distance: float = Field(default=1.0, description="与查询的距离（越小越相关）")


class RetrieveResponse(BaseModel):
    """纯检索响应（不含 LLM 回答，供 Agent 使用）。"""
    chunks: List[RetrieveChunkItem] = Field(default_factory=list)
    query_used: List[str] = Field(default_factory=list, description="实际使用的检索查询（含改写）")


class SourceItem(BaseModel):
    """知识来源项。"""
    source_type: str
    source_name: str
    title: str
    source_url: Optional[str] = None
    chunk_count: int = 0


class SourceListResponse(BaseModel):
    """知识来源列表响应。"""
    sources: List[SourceItem]
    total_chunks: int


class DeleteSourceResponse(BaseModel):
    """删除知识来源响应。"""
    success: bool
    message: str = ""
    deleted_count: int = 0


# ===================== Agentic RAG 模型 =====================

class ResearchRequest(BaseModel):
    """Agentic RAG 深度研究请求。"""
    query: str = Field(..., description="研究问题", min_length=1, max_length=2000)
    mode: str = Field(
        default="report",
        description="输出模式: report(深度报告) / qa(增强问答)",
    )
    top_k: int = Field(default=8, description="每轮检索结果数", ge=1, le=20)
    enable_web_search: bool = Field(
        default=True, description="是否允许联网搜索补齐"
    )

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in ("report", "qa"):
            raise ValueError("mode 只能是 'report' 或 'qa'")
        return v


class ResearchStepItem(BaseModel):
    """研究步骤项。"""
    step_name: str
    description: str
    result_summary: str


class ResearchCitationItem(BaseModel):
    """研究报告引用项。"""
    index: Optional[int] = None
    source_type: str = ""
    source_name: str = ""
    title: str = ""
    source_url: Optional[str] = None
    text_snippet: str = ""


class ResearchResponse(BaseModel):
    """Agentic RAG 深度研究响应。"""
    answer: str = Field(..., description="生成的回答或报告内容")
    citations: List[ResearchCitationItem] = Field(default_factory=list)
    original_query: str = ""
    transformed_queries: List[str] = Field(default_factory=list)
    retrieval_rounds: int = 0
    total_docs_retrieved: int = 0
    web_search_used: bool = False
    steps: List[ResearchStepItem] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    """错误响应。"""
    success: bool = False
    message: str


# ===================== 对话历史模型 =====================

class ConversationMessageItem(BaseModel):
    """对话中的单条消息。"""
    id: str
    role: str
    content: str
    citations: list = Field(default_factory=list)
    created_at: float


class ConversationItem(BaseModel):
    """对话摘要项（列表用）。"""
    id: str
    title: str
    created_at: float
    updated_at: float
    message_count: int = 0


class ConversationDetail(BaseModel):
    """对话详情（含消息）。"""
    id: str
    title: str
    created_at: float
    updated_at: float
    messages: List[ConversationMessageItem] = Field(default_factory=list)


class ConversationListResponse(BaseModel):
    """对话列表响应。"""
    conversations: List[ConversationItem]


class CreateConversationRequest(BaseModel):
    """创建对话请求。"""
    title: str = Field(default="新对话", max_length=200)


class UpdateConversationRequest(BaseModel):
    """更新对话标题请求。"""
    title: str = Field(..., min_length=1, max_length=200)


class AddMessageRequest(BaseModel):
    """向对话添加消息。"""
    role: str = Field(..., description="消息角色: user/ai")
    content: str = Field(..., min_length=1, max_length=50000)
    citations: list = Field(default_factory=list)

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in ("user", "ai"):
            raise ValueError("role 只能是 'user' 或 'ai'")
        return v
