"""Conversation support: owner-scoped data and bounded context."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

from domain.agent import AgentEvent
from domain.document_content import DocumentParseSnapshot
from domain.documents import Document, DocumentVersion, ProcessingJob
from domain.evidence import EvidenceChunk, EvidenceSearchHit
from domain.models import (
    AuditEntry,
    ChatResponse,
    Chunk,
    ConversationRun,
    KbChunk,
    KbDocument,
    Message,
    MessageFeedback,
    Task,
    TaskSummary,
    ToolCall,
    User,
    WebResult,
)
from domain.qa import (
    ClaimSupportResult,
    EvidenceQACitation,
    EvidenceQAClaim,
    EvidenceQADraft,
    UserContextMessage,
)

T = TypeVar("T")

# === 身份 ===


@runtime_checkable
class AuthPort(Protocol):
    """OAuth 流程 + JWT 颁发 + 匿名用户创建。"""

    def begin_oauth(self, provider: str) -> tuple[str, str]:
        """返回 `(auth_url, state)`，前端跳转到 `auth_url`，回调时携带 `state`。"""
        ...

    def complete_oauth(self, provider: str, code: str, state: str) -> User:
        """校验 `state`、用 `code` 换 access_token、拉用户信息、upsert 后返回。"""
        ...

    def issue_jwt(self, user_id: str) -> str: ...

    def verify_jwt(self, token: str) -> str | None:
        """校验通过返回 `user_id`，否则返回 `None`（不抛异常）。"""
        ...

    def create_anonymous(self) -> User: ...


@runtime_checkable
class UserRepoPort(Protocol):
    def upsert(self, user: User) -> None: ...

    def get(self, user_id: str) -> User | None: ...

    def merge_owner(self, from_id: str, to_id: str) -> int:
        """把所有 `owner_id == from_id` 的资源迁移到 `to_id`，返回迁移条数。"""
        ...

    def touch(self, user_id: str) -> None:
        """更新 `last_active_at`，不动其它字段。"""
        ...


# === V2 Document / Object Store ===


@runtime_checkable
class ObjectStorePort(Protocol):
    """原始文件对象存储；object_key 必须是存储根目录内的相对键。"""

    def put(self, object_key: str, content: bytes) -> None: ...

    def read(self, object_key: str) -> bytes: ...

    def delete(self, object_key: str) -> bool: ...

    def exists(self, object_key: str) -> bool: ...


@runtime_checkable
class DocumentRepoPort(Protocol):
    """文档、版本、处理任务的元数据端口。"""

    def create_upload(
        self,
        document: Document,
        version: DocumentVersion,
        job: ProcessingJob,
    ) -> None:
        """在单个事务中创建首次上传的四个对象。"""
        ...

    def get(self, document_id: str) -> Document | None: ...

    def get_version(self, version_id: str) -> DocumentVersion | None: ...

    def list_versions(self, document_id: str) -> list[DocumentVersion]: ...

    def list_for_owner(
        self,
        owner_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[Document]: ...

    def get_job(self, job_id: str) -> ProcessingJob | None: ...

    def get_latest_job_for_version(
        self,
        document_version_id: str,
    ) -> ProcessingJob | None: ...

    def list_jobs_for_owner(
        self,
        owner_id: str,
        *,
        statuses: set[str] | None = None,
        limit: int = 50,
    ) -> list[ProcessingJob]: ...

    def update_document(self, document: Document) -> None: ...

    def update_job(
        self,
        job: ProcessingJob,
        *,
        expected_revision: int,
    ) -> None: ...

    def update_processing_state(
        self,
        document: Document,
        job: ProcessingJob,
        *,
        expected_revision: int,
    ) -> None: ...

    def save_parse_result(
        self,
        version: DocumentVersion,
        snapshot: DocumentParseSnapshot,
        document: Document,
        job: ProcessingJob,
        *,
        expected_job_revision: int,
    ) -> None:
        """原子保存解析快照，并推进版本、文档和任务状态。"""
        ...

    def get_parse_snapshot(self, document_version_id: str) -> DocumentParseSnapshot | None: ...


@runtime_checkable
class DocumentParserPort(Protocol):
    """原始文件字节到页级解析快照的转换端口。"""

    def parse(
        self,
        version: DocumentVersion,
        content: bytes,
    ) -> DocumentParseSnapshot: ...


@runtime_checkable
class DocumentOcrPort(Protocol):
    """只补全 ParseSnapshot 中标记为 empty 的页面。"""

    def apply_ocr(
        self,
        version: DocumentVersion,
        content: bytes,
        snapshot: DocumentParseSnapshot,
    ) -> DocumentParseSnapshot: ...


@runtime_checkable
class BackgroundJobDispatcherPort(Protocol):
    """后台任务投递端口；业务消息只携带服务端 job_id。"""

    def enqueue_document(self, job_id: str, *, attempt: int) -> str: ...

    def cancel_document(self, job_id: str, *, attempt: int) -> None: ...


@runtime_checkable
class EvidenceChunkerPort(Protocol):
    """页级解析快照到作用域证据块的转换端口。"""

    def chunk(
        self,
        document: Document,
        version: DocumentVersion,
        snapshot: DocumentParseSnapshot,
    ) -> list[EvidenceChunk]: ...


@runtime_checkable
class EvidenceIndexPort(Protocol):
    """先按 owner 过滤，再执行混合检索的证据索引端口。"""

    def replace_version_chunks(
        self,
        document_version_id: str,
        chunks: list[EvidenceChunk],
        embeddings: list[list[float]],
    ) -> None: ...

    def search(
        self,
        *,
        owner_id: str,
        query: str,
        query_embedding: list[float],
        top_k: int = 5,
        document_ids: list[str] | None = None,
    ) -> list[EvidenceSearchHit]: ...

    def count_version(self, document_version_id: str) -> int: ...

    def complete_version_indexing(
        self,
        document_version_id: str,
        chunks: list[EvidenceChunk],
        embeddings: list[list[float]],
        document: Document,
        job: ProcessingJob,
        *,
        expected_job_revision: int,
    ) -> None:
        """原子替换版本证据块，并完成文档与任务状态。"""
        ...


# === V2 Workflow Run / Checkpoint ===


# === 任务 / 消息 ===


@runtime_checkable
class TaskRepoPort(Protocol):
    def create(self, task: Task) -> None: ...

    def get(self, task_id: str, owner_id: str) -> Task | None: ...

    def list_for_owner(self, owner_id: str, limit: int = 50) -> list[Task]: ...

    def update(self, task: Task) -> None: ...

    def delete(self, task_id: str, owner_id: str) -> bool: ...

    def append_message(self, msg: Message) -> None: ...

    def list_messages(self, task_id: str) -> list[Message]: ...

    def set_attachment_ids(
        self,
        task_id: str,
        owner_id: str,
        document_ids: list[str],
    ) -> None: ...

    def list_attachment_ids(self, task_id: str, owner_id: str) -> list[str]: ...

    def append_tool_call(self, call: ToolCall) -> None: ...


@runtime_checkable
class ConversationRunRepoPort(Protocol):
    def create(self, run: ConversationRun) -> None: ...

    def get(self, run_id: str, owner_id: str) -> ConversationRun | None: ...

    def update(self, run: ConversationRun, *, expected_status: str | None = None) -> None: ...

    def claim_resume(
        self,
        run_id: str,
        owner_id: str,
        *,
        updated_at: float,
    ) -> ConversationRun | None:
        """Atomically transition one interrupted run to running."""
        ...

    def commit_terminal(
        self,
        run: ConversationRun,
        message: Message,
        *,
        validate_context: Callable[[], None] | None = None,
    ) -> None:
        """Atomically append the terminal assistant message and finish the run."""
        ...

    def reserve_call(
        self,
        run_id: str,
        owner_id: str,
        *,
        kind: Literal["model", "tool"],
        stage: str,
        now: float,
    ) -> ConversationRun | None: ...

    def record_model_usage(
        self,
        run_id: str,
        owner_id: str,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        now: float,
    ) -> ConversationRun | None: ...

    def record_retry(
        self,
        run_id: str,
        owner_id: str,
        *,
        kind: Literal["model", "tool"],
        now: float,
    ) -> ConversationRun | None: ...

    def request_cancel(
        self,
        run_id: str,
        owner_id: str,
        *,
        reason: str,
        now: float,
    ) -> ConversationRun | None: ...

    def finish_active(
        self,
        run_id: str,
        owner_id: str,
        *,
        status: Literal["cancelled", "timed_out", "budget_exhausted", "failed"],
        reason: str,
        error_type: str | None,
        now: float,
    ) -> ConversationRun | None: ...

    def pause(
        self,
        run_id: str,
        owner_id: str,
        *,
        now: float,
    ) -> ConversationRun | None: ...

    def reconcile_stale(self, *, now: float) -> int:
        """Close non-terminal runs left without an in-process executor at startup."""
        ...

    def get_latest_for_task(
        self,
        task_id: str,
        owner_id: str,
    ) -> ConversationRun | None: ...


@runtime_checkable
class FeedbackRepoPort(Protocol):
    """消息反馈（点赞/点踩）存储。按 ``msg_id`` 幂等上写。"""

    def set(self, feedback: MessageFeedback) -> None: ...

    def clear(self, msg_id: str, owner_id: str) -> bool: ...

    def get_for_task(self, task_id: str, owner_id: str) -> dict[str, str]: ...

    def counts(self) -> dict[str, int]: ...


# === LLM / Embedding / 检索 ===


@runtime_checkable
class EmbedPort(Protocol):
    def embed(
        self,
        texts: list[str],
        *,
        runtime_control: RunControlPort | None = None,
    ) -> list[list[float]]: ...


@runtime_checkable
class RunControlPort(Protocol):
    @property
    def run_id(self) -> str: ...

    def check(self, *, stage: str | None = None) -> ConversationRun: ...

    def before_model_call(self, *, operation: str, stage: str) -> ConversationRun: ...

    def before_tool_call(self, *, operation: str) -> ConversationRun: ...

    def call_model(
        self,
        *,
        operation: str,
        stage: str,
        invoke: Callable[[], T],
        retry: bool = False,
    ) -> T: ...

    def call_tool(
        self,
        *,
        operation: str,
        invoke: Callable[[], T],
        retry: bool = False,
    ) -> T: ...

    def wait_for_graph_event(self, invoke: Callable[[], T]) -> T: ...

    def wait(self, seconds: float) -> None: ...

    def record_model_usage(
        self,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> None: ...

    def record_retry(self, *, kind: Literal["model", "tool"]) -> None: ...

    def interrupt(self) -> ConversationRun: ...

    def finish(
        self,
        *,
        status: Literal["cancelled", "timed_out", "budget_exhausted", "failed"],
        reason: str,
        error_type: str | None = None,
    ) -> ConversationRun: ...


@runtime_checkable
class ChatPort(Protocol):
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = False,
        runtime_control: RunControlPort | None = None,
        operation: str = "chat",
    ) -> str: ...

    def chat_with_usage(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = False,
        runtime_control: RunControlPort | None = None,
        operation: str = "chat",
    ) -> ChatResponse: ...


@runtime_checkable
class CopilotAgentPort(Protocol):
    """会话式 Tool Calling Agent 入口。"""

    def run(
        self,
        *,
        owner_id: str,
        task_id: str,
        user_message: str,
        attachment_doc_ids: list[str],
        run_id: str,
        thread_id: str,
        input_message_id: str,
        runtime_control: RunControlPort,
    ) -> Iterator[AgentEvent]: ...

    def resume(
        self,
        *,
        owner_id: str,
        task_id: str,
        run_id: str,
        thread_id: str,
        user_message: str,
        attachment_doc_ids: list[str],
        prior_evidence: list[dict[str, Any]],
        runtime_control: RunControlPort,
    ) -> Iterator[AgentEvent]: ...


@runtime_checkable
class TraceSpanPort(Protocol):
    """运行中的可观测性 span；只接收脱敏后的结构化元数据。"""

    def add_metadata(self, metadata: Mapping[str, Any]) -> None: ...


@runtime_checkable
class TracePort(Protocol):
    """框架无关的 AI Trace 端口。"""

    def span(
        self,
        name: str,
        *,
        run_type: Literal["chain", "llm", "tool", "retriever"] = "chain",
        metadata: Mapping[str, Any] | None = None,
    ) -> AbstractContextManager[TraceSpanPort]: ...


@runtime_checkable
class MetricsPort(Protocol):
    """框架无关的聚合指标端口。"""

    def observe_http(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None: ...

    def observe_agent_run(
        self,
        *,
        workflow: str,
        status: str,
        duration_seconds: float,
        token_usage: int,
        cost: float,
        refused: bool,
    ) -> None: ...

    def observe_tool(
        self,
        *,
        tool: str,
        status: str,
        duration_seconds: float,
        retry_count: int,
    ) -> None: ...

    def record_llm_usage(
        self,
        *,
        operation: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost: float,
    ) -> None: ...

    def record_citation_failure(self, *, workflow: str) -> None: ...

    def render(self) -> bytes: ...

    @property
    def content_type(self) -> str: ...


@runtime_checkable
class ReadinessPort(Protocol):
    """应用必需依赖的就绪检查；不得检查可选 LLM 或外部搜索服务。"""

    def check(self) -> dict[str, bool | str]: ...


@runtime_checkable
class RetrievePort(Protocol):
    """检索三段式（BM25 + 向量 + RRF + Reranker）的高层入口。"""

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        corpus: Literal["law", "user_docs"] = "law",
        owner_id: str | None = None,
        runtime_control: RunControlPort | None = None,
    ) -> list[Chunk]: ...


@runtime_checkable
class ClaimSupportVerifierPort(Protocol):
    """独立校验 Claim 是否由其引用原文支持；失败时必须 fail closed。"""

    def verify(
        self,
        claims: list[EvidenceQAClaim],
        citations: list[EvidenceQACitation],
        user_context: list[UserContextMessage] | None = None,
        runtime_control: RunControlPort | None = None,
    ) -> ClaimSupportResult: ...


@runtime_checkable
class EvidenceQAGeneratorPort(Protocol):
    """基于服务端编号证据生成结构化 Claim，不生成自由长答案。"""

    def generate(
        self,
        *,
        question: str,
        citations: list[EvidenceQACitation],
        user_context: list[UserContextMessage] | None = None,
        preferences: list[str] | None = None,
        runtime_control: RunControlPort | None = None,
    ) -> EvidenceQADraft: ...


# === 外部服务 ===


@runtime_checkable
class KbDocumentRepoPort(Protocol):
    """知识库管理端口（Step 016a）：按 ``source_name`` 聚合的 CRUD + 写入。

    职责边界：
    - **不做**检索（检索走 ``RetrievePort``，由 BM25 + 向量 + RRF 协同）。
    - **只做**管理面：列出文档、按 source 聚合、计 chunk 数、按 source 删除，
      以及"批量写入 chunks + embeddings"的原子操作。
    - 切分 / 清洗 / 嵌入由上层 ``KbManagementUseCase`` 编排，
      本端口只接受已就绪的 ``KbChunk`` 列表与平行的 embeddings。

    与 v1 ``service.py:KnowledgeService.list_sources/delete_source`` 的老 KB 管理
    职责被本端口完整接管；Step 016d 已删除 v1 入口，本端口成为唯一管理面入口。

    Step 025a：读侧 ``list_documents`` / ``get_document`` 增加 ``owners`` 筛选；delete_document
    增加 ``owner_id`` 筛选。语义为面向 KB 的冗余身份粒度，免得在上层重复过滤。
    """

    def list_documents(
        self,
        *,
        owners: list[str | None] | None = None,
    ) -> list[KbDocument]: ...

    def get_document(
        self,
        source_name: str,
        *,
        owners: list[str | None] | None = None,
    ) -> KbDocument | None: ...

    def count_chunks(self) -> int: ...

    def delete_document(
        self,
        source_name: str,
        *,
        owner_id: Any = ...,
    ) -> int:
        """按 ``source_name`` 删除其所有 chunk，返回实际删除条数（不存在返回 0）。

        ``owner_id`` 语义：作为可选过滤。传 None 仅删公共；传字符串仅删该 owner；
        未传（实现方用 sentinel）表示 admin 语义，不限 owner。该参数默认使用 ``...``
        仅作 Protocol 描述；实现侧以各自的 ``_UNSET`` sentinel 为准。
        """
        ...

    def add_chunks(
        self,
        chunks: list[KbChunk],
        embeddings: list[list[float]],
    ) -> None:
        """批量写入 chunks 与 embeddings。``len(chunks) == len(embeddings)`` 必须满足。

        实现方应在写入前清理同 ``source_name`` + ``owner_id`` 的旧数据（典型语义：替换而非追加），
        以保持 ingestion 链路的"先删后插"幂等。
        """
        ...


@runtime_checkable
class DocumentLoaderPort(Protocol):
    """文档加载 + 切分一体端口（Step 016b）：把外部资源转成 ``list[KbChunk]``。

    职责边界：
    - **承担** I/O（读文件 / 抓网页）+ 清洗 + 切分 + 元数据组装四件事；
    - **不承担** embedding（走 ``EmbedPort``）；
    - **不承担** 写库（走 ``KbDocumentRepoPort``）。

    返回的 chunks 必须满足 ``KbChunk`` 的所有约束（text 非空、chunk_index >= 0、
    source_type ∈ {"file", "web"}）。空文档（无可入库内容）返回空列表，**不抛**。

    设计取舍：之所以把"加载 + 切分"打包，是为了 ``KbManagementUseCase`` 编排时
    只面对 3 个 Port（loader / embedder / repo），避免把 ``processing.metadata``
    /``ingestion.unified_loader`` 这类基础设施模块直接渗到 app 层。

    Step 025a：load_file / load_web 加 ``owner_id`` 参数，会默认填到返回 chunks 的 owner_id。
    """

    def load_file(
        self,
        file_path: str,
        *,
        original_filename: str | None = None,
        category: str | None = None,
        owner_id: str | None = None,
    ) -> list[KbChunk]: ...

    def load_web(
        self,
        url: str,
        *,
        category: str | None = None,
        owner_id: str | None = None,
    ) -> list[KbChunk]: ...


@runtime_checkable
class WebSearchPort(Protocol):
    def search(
        self,
        query: str,
        max_results: int = 3,
        *,
        runtime_control: RunControlPort | None = None,
    ) -> list[WebResult]: ...


# === 记忆（按 owner_id / task_id 多层） ===


@runtime_checkable
class MemoryPort(Protocol):
    """最近消息与会话摘要。"""

    # L1 短期：按 task_id

    def recent_messages(self, owner_id: str, task_id: str, n: int) -> list[Message]: ...

    # L2 摘要：按 task_id
    def get_summary(self, owner_id: str, task_id: str) -> str | None: ...

    def maybe_summarize(self, owner_id: str, task_id: str, threshold: int = 20) -> None: ...


@runtime_checkable
class SummaryStorePort(Protocol):
    """会话滚动摘要存储。"""

    def get(self, task_id: str, owner_id: str) -> TaskSummary | None: ...

    def upsert(self, summary: TaskSummary) -> None: ...


@runtime_checkable
class MemoryJobSchedulerPort(Protocol):
    """会话摘要后台调度。

    回复完成后由 use case 显式调用，后台 best-effort 跡出 会话摘要，
    不阻塞主回复；失败下一轮按 watermark 自愈补。
    """

    def schedule_summarization(self, owner_id: str, task_id: str) -> None: ...


# === 审计 ===


@runtime_checkable
class AuditLogPort(Protocol):
    """admin 操作审计端口（Step 021）。

    职责：把 ``AuditEntry`` 不可变记录到持久层；提供按 ``action`` / ``actor_id``
    过滤的只读查询。

    实现方约定：
    - ``record`` 同步阻塞；写失败抛异常（由调用方决定如何兜底，典型策略是
      ``try/except`` 吞错并打 warning 日志，**不**让 audit 失败影响主业务）
    - ``list_recent`` 按 ``timestamp`` 倒序；过滤参数 None 表示不过滤
    - ``offset`` 用于分页（基于 0），与 ``limit`` 共同决定窗口
    - 不提供 update / delete API（审计要求可追溯不可变）

    与 ``logger.info`` 区别：本端口的产出是结构化、按字段索引的合规流水账；
    散点 logger 是给开发者看的运维日志，二者不互相替代。
    """

    def record(self, entry: AuditEntry) -> None: ...

    def list_recent(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        action: str | None = None,
        actor_id: str | None = None,
    ) -> list[AuditEntry]: ...
