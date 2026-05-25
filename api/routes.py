"""FastAPI 路由定义：知识接入、检索问答、来源管理。"""

import os
import asyncio
import json
import logging
import time
import threading
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, UploadFile, File, HTTPException, Query, Request
from starlette.responses import StreamingResponse
from slowapi import Limiter
from slowapi.util import get_remote_address

from config import settings

# ---- API 限流器（按客户端 IP 限流，防滥用） ----
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.rate_limit_default],
    config_filename="",  # 不让 slowapi 读 .env（已由 pydantic-settings 管理，避免中文 Windows 编码问题）
)
from api.schemas import (
    WebIngestRequest,
    AskRequest,
    RetrieveRequest,
    IngestResponse,
    AskResponse,
    RetrieveResponse,
    RetrieveChunkItem,
    CitationItem,
    SourceListResponse,
    SourceItem,
    DeleteSourceResponse,
    ResearchRequest,
    ResearchResponse,
    ResearchStepItem,
    ResearchCitationItem,
    ConversationItem,
    ConversationDetail,
    ConversationMessageItem,
    ConversationListResponse,
    CreateConversationRequest,
    UpdateConversationRequest,
    AddMessageRequest,
)
from service import KnowledgeService

logger = logging.getLogger(__name__)

router = APIRouter()

# ---- 知识服务层单例（后台初始化，避免首次请求阻塞） ----
_knowledge_service: KnowledgeService | None = None
_service_ready = threading.Event()


def _init_service_background():
    """后台线程初始化 KnowledgeService。"""
    global _knowledge_service
    try:
        logger.info("正在后台初始化 KnowledgeService...")
        _knowledge_service = KnowledgeService()
        _service_ready.set()
        logger.info("KnowledgeService 后台初始化完成")
    except Exception as e:
        logger.exception(f"KnowledgeService 初始化失败: {e}")
        _service_ready.set()  # 设置 event 避免永远阻塞


def start_service_init():
    """由 FastAPI startup 事件调用，启动后台初始化线程。"""
    threading.Thread(target=_init_service_background, daemon=True).start()


def get_knowledge_service() -> KnowledgeService:
    """获取 KnowledgeService 单例，阻塞等待直到初始化完成。"""
    _service_ready.wait()
    if _knowledge_service is None:
        raise RuntimeError("KnowledgeService 初始化失败")
    return _knowledge_service

# 允许上传的文件后缀
ALLOWED_EXTENSIONS = {".pdf", ".txt", ".docx"}


# ==================== 工具函数 ====================

def _safe_save_path(original_filename: str) -> tuple[str, str]:
    """生成安全的保存路径（UUID文件名），返回 (save_path, original_name)。

    使用 UUID 完全消除路径穿越风险，同时保留原始后缀用于格式判断。
    """
    suffix = Path(original_filename).suffix.lower()
    safe_name = f"{uuid4().hex}{suffix}"
    save_path = os.path.join(settings.upload_dir, safe_name)
    return save_path, original_filename


# ==================== 健康检查 ====================

@router.get("/health")
async def health_check():
    """服务健康检查：返回状态和知识库当前 chunk 总数。"""
    if not _service_ready.is_set():
        return {"status": "initializing", "chunks": 0}
    try:
        total = await asyncio.to_thread(
            lambda: _knowledge_service.vector_store.get_total_count()
        )
        return {"status": "ok", "chunks": total}
    except Exception:
        return {"status": "degraded", "chunks": 0}


# ==================== 入库接口 ====================

@router.post("/api/ingest/file", response_model=IngestResponse)
@limiter.limit(settings.rate_limit_ingest)
async def ingest_file(
    request: Request,
    file: UploadFile = File(...),
    category: str = Query("", description="文档分类标签：法规/政策/指南/标准"),
):
    """上传本地文档并导入知识库。支持 PDF / TXT / DOCX。"""
    original_name = file.filename or "unknown"
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"不支持的文件格式: {suffix}，仅支持 {ALLOWED_EXTENSIONS}")

    content = await file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(413, f"文件过大，最大支持 {settings.max_upload_mb}MB")

    save_path, _ = _safe_save_path(original_name)
    try:
        with open(save_path, "wb") as f:
            f.write(content)

        logger.info(f"文件已保存: {save_path}（原始名: {original_name}）")
        result = await asyncio.to_thread(
            get_knowledge_service().ingest_file, save_path, original_name, category
        )

        return IngestResponse(
            success=result.success, message=result.message,
            source_name=result.source_name, chunk_count=result.chunk_count,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"文件导入失败: {e}")
        raise HTTPException(500, f"文件导入失败: {str(e)}")
    finally:
        # 无论成功或失败，都删除临时文件，避免磁盘泄漏
        if os.path.exists(save_path):
            try:
                os.remove(save_path)
            except OSError:
                logger.warning(f"临时文件清理失败: {save_path}")


@router.post("/api/ingest/web", response_model=IngestResponse)
@limiter.limit(settings.rate_limit_ingest)
async def ingest_web(request: Request, req: WebIngestRequest):
    """采集网页内容并导入知识库。"""
    try:
        result = await asyncio.to_thread(get_knowledge_service().ingest_web, req.url, req.category)
        return IngestResponse(
            success=result.success, message=result.message,
            source_name=result.source_name, chunk_count=result.chunk_count,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception(f"网页采集失败: {e}")
        raise HTTPException(500, f"网页采集失败: {str(e)}")


# ==================== 检索接口（供多 Agent 调用） ====================

@router.post("/api/retrieve", response_model=RetrieveResponse)
async def retrieve_chunks(req: RetrieveRequest):
    """纯知识检索：只返回相关文档片段，不调用 LLM。

    多 Agent 系统中的各 Agent 应调用此接口获取知识上下文，
    然后用自己的 Prompt 和推理逻辑来处理。
    """
    try:
        result = await asyncio.to_thread(
            get_knowledge_service().retrieve, req.query, req.top_k, req.category
        )

        chunks = []
        for c in result.chunks:
            meta = c.get("metadata", {})
            chunks.append(RetrieveChunkItem(
                text=c.get("text", ""),
                source_type=meta.get("source_type", ""),
                source_name=meta.get("source_name", ""),
                title=meta.get("title", ""),
                source_url=meta.get("source_url"),
                category=meta.get("category", ""),
                distance=c.get("distance", 1.0),
            ))

        return RetrieveResponse(chunks=chunks, query_used=result.query_used)

    except Exception as e:
        logger.exception(f"检索失败: {e}")
        raise HTTPException(500, f"检索失败: {str(e)}")


# ==================== 问答接口 ====================

@router.post("/api/ask", response_model=AskResponse)
@limiter.limit(settings.rate_limit_llm)
async def ask_question(request: Request, req: AskRequest):
    """知识库问答：检索 + LLM 生成回答。"""
    try:
        top_k = max(1, min(req.top_k, settings.max_top_k))
        qa_result = await asyncio.to_thread(
            get_knowledge_service().ask, req.question, top_k, req.category
        )

        citations = [
            CitationItem(
                source_type=c.source_type, source_name=c.source_name,
                title=c.title, source_url=c.source_url,
                text_snippet=c.text_snippet,
            )
            for c in qa_result.citations
        ]

        return AskResponse(
            answer=qa_result.answer, citations=citations,
            has_enough_context=qa_result.has_enough_context,
        )

    except Exception as e:
        logger.exception(f"问答失败: {e}")
        raise HTTPException(500, f"问答失败: {str(e)}")


# ==================== 流式问答接口（SSE） ====================

@router.post("/api/ask/stream")
@limiter.limit(settings.rate_limit_llm)
async def ask_question_stream(request: Request, req: AskRequest):
    """流式知识库问答：通过 SSE 逐 token 返回回答。"""

    top_k = max(1, min(req.top_k, settings.max_top_k))
    timeout = settings.stream_timeout

    def event_generator():
        start = time.monotonic()
        try:
            from retrieval.generation.qa_chain import QAResult as _QAResult
            gen = get_knowledge_service().ask_stream(req.question, top_k, req.category)
            for item in gen:
                # 超时检查：防止流式生成无限挂起
                if time.monotonic() - start > timeout:
                    logger.warning(f"流式问答超时 ({timeout}s)，强制结束")
                    yield f"data: {json.dumps({'type': 'error', 'message': f'生成超时（{timeout}秒），已自动截断'}, ensure_ascii=False)}\n\n"
                    return
                if isinstance(item, _QAResult):
                    # 最后一个 yield 是 QAResult，发送引用和完成信号
                    citations = [
                        {
                            "source_type": c.source_type,
                            "source_name": c.source_name,
                            "title": c.title,
                            "source_url": c.source_url,
                            "text_snippet": c.text_snippet,
                        }
                        for c in item.citations
                    ]
                    yield f"data: {json.dumps({'type': 'citations', 'data': citations, 'has_enough_context': item.has_enough_context}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'token', 'content': item}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception(f"流式问答失败: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ==================== Agentic RAG 深度研究接口 ====================

@router.post("/api/research", response_model=ResearchResponse)
@limiter.limit(settings.rate_limit_llm)
async def research(request: Request, req: ResearchRequest):
    """Agentic RAG 深度研究：自我反思检索 + 深度报告/增强问答。

    核心特性：
    - Query Transformation：多维度查询变换
    - Corrective RAG：检索质量自动评估，不足时联网搜索
    - 深度报告生成：带引用标签的长文分析
    """
    try:
        result = await asyncio.to_thread(
            get_knowledge_service().research,
            req.query, req.mode, req.top_k, req.enable_web_search,
        )

        citations = [
            ResearchCitationItem(**c) for c in result.citations
        ]
        steps = [
            ResearchStepItem(
                step_name=s.step_name,
                description=s.description,
                result_summary=s.result_summary,
            )
            for s in result.steps
        ]

        return ResearchResponse(
            answer=result.answer,
            citations=citations,
            original_query=result.original_query,
            transformed_queries=result.transformed_queries,
            retrieval_rounds=result.retrieval_rounds,
            total_docs_retrieved=result.total_docs_retrieved,
            web_search_used=result.web_search_used,
            steps=steps,
        )

    except Exception as e:
        logger.exception(f"深度研究失败: {e}")
        raise HTTPException(500, f"深度研究失败: {str(e)}")


@router.post("/api/research/stream")
@limiter.limit(settings.rate_limit_llm)
async def research_stream(request: Request, req: ResearchRequest):
    """流式 Agentic RAG 深度研究。

    通过 SSE 推送研究进度和生成内容：
    - step 事件：研究步骤进度
    - token 事件：流式文本生成
    - result 事件：最终引用和元数据
    """
    timeout = settings.stream_timeout

    def event_generator():
        start = time.monotonic()
        try:
            gen = get_knowledge_service().research_stream(
                req.query, req.mode, req.top_k, req.enable_web_search,
            )
            for item in gen:
                if time.monotonic() - start > timeout:
                    logger.warning(f"流式深度研究超时 ({timeout}s)，强制结束")
                    yield f"data: {json.dumps({'type': 'error', 'message': f'研究超时（{timeout}秒），已自动截断'}, ensure_ascii=False)}\n\n"
                    return
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as e:
            logger.exception(f"流式深度研究失败: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ==================== 知识来源管理 ====================

@router.get("/api/sources", response_model=SourceListResponse)
async def list_sources():
    """获取当前知识库中所有知识来源列表。"""
    if not _service_ready.is_set():
        return SourceListResponse(sources=[], total_chunks=0)
    try:
        data = await asyncio.to_thread(get_knowledge_service().list_sources)
        return SourceListResponse(
            sources=[SourceItem(**s) for s in data["sources"]],
            total_chunks=data["total_chunks"],
        )
    except Exception as e:
        logger.exception(f"获取知识源失败: {e}")
        raise HTTPException(500, f"获取知识源失败: {str(e)}")


@router.delete("/api/sources/{source_name}", response_model=DeleteSourceResponse)
async def delete_source(source_name: str):
    """按来源名称删除知识。"""
    try:
        deleted = await asyncio.to_thread(get_knowledge_service().delete_source, source_name)
        if deleted == 0:
            return DeleteSourceResponse(success=False, message=f"未找到来源: {source_name}", deleted_count=0)
        return DeleteSourceResponse(success=True, message=f"已删除来源 [{source_name}] 的 {deleted} 条记录", deleted_count=deleted)
    except Exception as e:
        logger.exception(f"删除来源失败: {e}")
        raise HTTPException(500, f"删除来源失败: {str(e)}")


# ==================== 对话历史管理 ====================

from data.chat_db import (
    create_conversation as db_create_conv,
    list_conversations as db_list_convs,
    get_conversation as db_get_conv,
    add_message as db_add_msg,
    update_conversation_title as db_update_title,
    delete_conversation as db_delete_conv,
)


@router.get("/api/conversations", response_model=ConversationListResponse)
async def list_conversations(limit: int = Query(50, ge=1, le=200)):
    """获取对话列表，按最近更新排序。"""
    try:
        convs = await asyncio.to_thread(db_list_convs, limit)
        return ConversationListResponse(
            conversations=[
                ConversationItem(
                    id=c.id, title=c.title,
                    created_at=c.created_at, updated_at=c.updated_at,
                    message_count=c.message_count,
                )
                for c in convs
            ]
        )
    except Exception as e:
        logger.exception(f"获取对话列表失败: {e}")
        raise HTTPException(500, f"获取对话列表失败: {str(e)}")


@router.post("/api/conversations", response_model=ConversationDetail)
async def create_conversation(req: CreateConversationRequest = None):
    """创建新对话。"""
    try:
        title = req.title if req else "新对话"
        conv = await asyncio.to_thread(db_create_conv, title)
        return ConversationDetail(
            id=conv.id, title=conv.title,
            created_at=conv.created_at, updated_at=conv.updated_at,
            messages=[],
        )
    except Exception as e:
        logger.exception(f"创建对话失败: {e}")
        raise HTTPException(500, f"创建对话失败: {str(e)}")


@router.get("/api/conversations/{conv_id}", response_model=ConversationDetail)
async def get_conversation(conv_id: str):
    """获取对话详情（含所有消息）。"""
    try:
        conv = await asyncio.to_thread(db_get_conv, conv_id)
        if not conv:
            raise HTTPException(404, "对话不存在")
        return ConversationDetail(
            id=conv.id, title=conv.title,
            created_at=conv.created_at, updated_at=conv.updated_at,
            messages=[
                ConversationMessageItem(
                    id=m.id, role=m.role, content=m.content,
                    citations=m.citations, created_at=m.created_at,
                )
                for m in conv.messages
            ],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取对话失败: {e}")
        raise HTTPException(500, f"获取对话失败: {str(e)}")


@router.patch("/api/conversations/{conv_id}")
async def rename_conversation(conv_id: str, req: UpdateConversationRequest):
    """重命名对话。"""
    try:
        ok = await asyncio.to_thread(db_update_title, conv_id, req.title)
        if not ok:
            raise HTTPException(404, "对话不存在")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"重命名对话失败: {e}")
        raise HTTPException(500, f"重命名对话失败: {str(e)}")


@router.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    """删除对话及其所有消息。"""
    try:
        ok = await asyncio.to_thread(db_delete_conv, conv_id)
        if not ok:
            raise HTTPException(404, "对话不存在")
        return {"success": True, "message": "对话已删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"删除对话失败: {e}")
        raise HTTPException(500, f"删除对话失败: {str(e)}")


@router.post("/api/conversations/{conv_id}/messages", response_model=ConversationMessageItem)
async def add_conversation_message(conv_id: str, req: AddMessageRequest):
    """向指定对话添加一条消息。"""
    try:
        # 先确认对话存在
        conv = await asyncio.to_thread(db_get_conv, conv_id)
        if not conv:
            raise HTTPException(404, "对话不存在")
        msg = await asyncio.to_thread(db_add_msg, conv_id, req.role, req.content, req.citations)
        return ConversationMessageItem(
            id=msg.id, role=msg.role, content=msg.content,
            citations=msg.citations, created_at=msg.created_at,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"添加消息失败: {e}")
        raise HTTPException(500, f"添加消息失败: {str(e)}")
