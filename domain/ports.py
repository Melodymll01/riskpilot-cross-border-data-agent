"""Domain 层端口（Protocol）定义。

每个 Protocol 都是 infra 层适配器的"对接合同"。本文件不实现任何方法，只定义签名。
约定：
- 所有 Protocol 标注 `@runtime_checkable`，便于测试使用 `isinstance` 检查 fake 是否满足契约。
- 不导入 infra / app / api；不引入 IO 或网络依赖。
- L3+ 记忆相关 Port 暴露 `SessionProfile` / `Fact`，不暴露底层向量库 / SQLite 细节。

模块边界：
- 检索三段式（BM25 / 向量 / RRF / Reranker）由 infra 层组合，对外只暴露 `RetrievePort`。
- 鉴权流程由 `AuthPort` 抽象，匿名 / OAuth / JWT 颁发与校验在同一个 Port 下统一。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager
from typing import Any, Literal, Protocol, runtime_checkable

from domain.agent import AgentEvent
from domain.assessments import Assessment, AssessmentBundle
from domain.cases import Case
from domain.document_content import DocumentParseSnapshot
from domain.documents import CaseDocument, Document, DocumentVersion, ProcessingJob
from domain.evidence import EvidenceChunk, EvidenceSearchHit
from domain.facts import (
    CaseFact,
    CaseFactEvidence,
    FactProposal,
    FactProposalDocument,
)
from domain.models import (
    Artifact,
    AuditEntry,
    Chunk,
    ConsolidationState,
    Fact,
    ForgetResult,
    KbChunk,
    KbDocument,
    MemoryRecallTrace,
    MemorySettings,
    Message,
    MessageFeedback,
    ResearchReport,
    RiskProfile,
    SessionProfile,
    Task,
    TaskSummary,
    ToolCall,
    User,
    WebResult,
)
from domain.policies import PolicyRule
from domain.qa import (
    ClaimSupportResult,
    EvidenceQACitation,
    EvidenceQAClaim,
    EvidenceQADraft,
)
from domain.runs import (
    AgentRun,
    CaseDocumentReadiness,
    RunCheckpoint,
    RunEvent,
    WorkflowExecutionResult,
)
from domain.visual import VisualAsset, VisualSearchHit
from domain.workspaces import Workspace, WorkspaceMembership

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


# === V2 Workspace / Case ===


@runtime_checkable
class WorkspaceRepoPort(Protocol):
    """Workspace 与成员关系持久化端口。"""

    def create(
        self,
        workspace: Workspace,
        creator_membership: WorkspaceMembership,
    ) -> None:
        """原子创建 Workspace，并写入创建者成员关系。"""
        ...

    def get(self, workspace_id: str) -> Workspace | None: ...

    def list_for_user(self, user_id: str, limit: int = 50) -> list[Workspace]: ...

    def get_membership(self, workspace_id: str, user_id: str) -> WorkspaceMembership | None: ...

    def upsert_membership(self, membership: WorkspaceMembership) -> None: ...

    def list_memberships(self, workspace_id: str) -> list[WorkspaceMembership]: ...


@runtime_checkable
class CaseRepoPort(Protocol):
    """V2 合规案件持久化端口。"""

    def create(self, case: Case) -> None: ...

    def get(self, case_id: str) -> Case | None: ...

    def list_for_workspace(
        self,
        workspace_id: str,
        *,
        include_archived: bool = False,
        limit: int = 50,
    ) -> list[Case]: ...

    def update(self, case: Case) -> None: ...


# === V2 Document / Object Store ===


@runtime_checkable
class ObjectStorePort(Protocol):
    """原始文件对象存储；object_key 必须是存储根目录内的相对键。"""

    def put(self, object_key: str, content: bytes) -> None: ...

    def read(self, object_key: str) -> bytes: ...

    def delete(self, object_key: str) -> bool: ...

    def exists(self, object_key: str) -> bool: ...


@runtime_checkable
class VisualEmbedPort(Protocol):
    """Chinese-CLIP 图文共享向量空间。"""

    def embed_images(self, images: list[bytes]) -> list[list[float]]: ...

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


@runtime_checkable
class VisualIndexPort(Protocol):
    """Case 作用域图片元数据与向量索引。"""

    def add(self, asset: VisualAsset, embedding: list[float]) -> None: ...

    def search(
        self,
        *,
        workspace_id: str,
        case_id: str,
        query_embedding: list[float],
        top_k: int,
    ) -> list[VisualSearchHit]: ...

    def get(self, asset_id: str) -> VisualAsset | None: ...


@runtime_checkable
class DocumentRepoPort(Protocol):
    """文档、版本、案件绑定和处理任务的元数据端口。"""

    def create_upload(
        self,
        document: Document,
        version: DocumentVersion,
        binding: CaseDocument,
        job: ProcessingJob,
    ) -> None:
        """在单个事务中创建首次上传的四个对象。"""
        ...

    def get(self, document_id: str) -> Document | None: ...

    def get_version(self, version_id: str) -> DocumentVersion | None: ...

    def list_versions(self, document_id: str) -> list[DocumentVersion]: ...

    def get_binding(self, case_id: str, document_id: str) -> CaseDocument | None: ...

    def list_bindings_for_document(self, document_id: str) -> list[CaseDocument]: ...

    def list_for_case(
        self,
        case_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[Document]: ...

    def get_job(self, job_id: str) -> ProcessingJob | None: ...

    def get_latest_job_for_version(
        self,
        document_version_id: str,
    ) -> ProcessingJob | None: ...

    def update_document(self, document: Document) -> None: ...

    def update_job(self, job: ProcessingJob) -> None: ...

    def update_processing_state(
        self,
        document: Document,
        job: ProcessingJob,
    ) -> None: ...

    def save_parse_result(
        self,
        version: DocumentVersion,
        snapshot: DocumentParseSnapshot,
        document: Document,
        job: ProcessingJob,
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
class EvidenceChunkerPort(Protocol):
    """页级解析快照到作用域证据块的转换端口。"""

    def chunk(
        self,
        document: Document,
        version: DocumentVersion,
        snapshot: DocumentParseSnapshot,
        bindings: list[CaseDocument],
    ) -> list[EvidenceChunk]: ...


@runtime_checkable
class EvidenceIndexPort(Protocol):
    """先按 Workspace/Case 过滤，再执行混合检索的证据索引端口。"""

    def replace_version_chunks(
        self,
        document_version_id: str,
        chunks: list[EvidenceChunk],
        embeddings: list[list[float]],
    ) -> None: ...

    def search(
        self,
        *,
        workspace_id: str,
        case_id: str,
        query: str,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[EvidenceSearchHit]: ...

    def search_workspace(
        self,
        *,
        workspace_id: str,
        query: str,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[EvidenceSearchHit]:
        """只搜索显式标记为 workspace_knowledge 的当前就绪文档。"""
        ...

    def count_version(self, document_version_id: str) -> int: ...

    def complete_version_indexing(
        self,
        document_version_id: str,
        chunks: list[EvidenceChunk],
        embeddings: list[list[float]],
        document: Document,
        job: ProcessingJob,
    ) -> None:
        """原子替换版本证据块，并完成文档与任务状态。"""
        ...


@runtime_checkable
class CaseFactRepoPort(Protocol):
    """案件事实当前快照、版本历史和证据引用端口。"""

    def create(
        self,
        fact: CaseFact,
        evidence: list[CaseFactEvidence],
    ) -> None: ...

    def create_many(
        self,
        items: list[tuple[CaseFact, list[CaseFactEvidence]]],
    ) -> None:
        """原子创建一批 Fact 及其证据；任一失败不得留下部分结果。"""
        ...

    def get(self, fact_id: str) -> CaseFact | None: ...

    def get_version(self, fact_id: str, version: int) -> CaseFact | None: ...

    def list_for_case(
        self,
        case_id: str,
        *,
        statuses: set[str] | None = None,
    ) -> list[CaseFact]: ...

    def list_evidence(
        self,
        fact_id: str,
        *,
        fact_version: int | None = None,
    ) -> list[CaseFactEvidence]: ...

    def save_revision(
        self,
        fact: CaseFact,
        evidence: list[CaseFactEvidence],
    ) -> None: ...

    def update_status(self, fact: CaseFact) -> None: ...

    def update_statuses(self, facts: list[CaseFact]) -> None:
        """原子更新一批 Fact 状态，用于唯一确认同字段事实。"""
        ...


@runtime_checkable
class PolicyRuleRepoPort(Protocol):
    """版本化 PolicyRule 存储端口。"""

    def create(self, rule: PolicyRule) -> None: ...

    def get(
        self,
        workspace_id: str,
        rule_id: str,
        ruleset_version: str,
    ) -> PolicyRule | None: ...

    def list_rules(
        self,
        *,
        workspace_id: str,
        ruleset_version: str | None = None,
        jurisdiction: str | None = None,
        status: str | None = None,
    ) -> list[PolicyRule]: ...

    def update_status(self, rule: PolicyRule) -> None: ...


@runtime_checkable
class AssessmentRepoPort(Protocol):
    """不可变 Assessment 版本与 Bundle 持久化端口。"""

    def create_version(
        self,
        bundle: AssessmentBundle,
        previous: Assessment | None,
        case: Case,
    ) -> None:
        """原子保存新版本、supersede 旧版本并更新 Case.active_assessment_id。"""
        ...

    def get(self, assessment_id: str) -> AssessmentBundle | None: ...

    def get_active(self, case_id: str) -> AssessmentBundle | None: ...

    def list_for_case(self, case_id: str) -> list[Assessment]: ...

    def next_version(self, case_id: str) -> int: ...

    def save_review(self, assessment: Assessment, case: Case) -> None:
        """原子保存审批结果，并同步更新活动 Case 状态。"""
        ...


# === V2 Workflow Run / Checkpoint ===


@runtime_checkable
class AgentRunRepoPort(Protocol):
    """AgentRun、轻量检查点和阶段事件的原子持久化端口。"""

    def create(
        self,
        run: AgentRun,
        checkpoint: RunCheckpoint,
        event: RunEvent,
    ) -> None: ...

    def get(self, run_id: str) -> AgentRun | None: ...

    def get_checkpoint(self, checkpoint_id: str) -> RunCheckpoint | None: ...

    def get_latest_checkpoint(self, run_id: str) -> RunCheckpoint | None: ...

    def list_for_case(
        self,
        case_id: str,
        *,
        limit: int = 50,
    ) -> list[AgentRun]: ...

    def list_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> list[RunEvent]: ...

    def next_checkpoint_version(self, run_id: str) -> int: ...

    def next_event_sequence(self, run_id: str) -> int: ...

    def save_progress(
        self,
        run: AgentRun,
        checkpoint: RunCheckpoint,
        events: list[RunEvent],
        *,
        expected_revision: int,
    ) -> None:
        """以乐观锁原子保存 Run、检查点和事件。"""
        ...


@runtime_checkable
class WorkflowRuntimePort(Protocol):
    """框架无关的案件评估工作流运行时端口。"""

    def inspect_case_assessment(
        self,
        *,
        thread_id: str,
    ) -> WorkflowExecutionResult | None: ...

    def start_case_assessment(
        self,
        *,
        thread_id: str,
        case_id: str,
        workspace_id: str,
        actor_id: str,
        ruleset_version: str,
        document_readiness: CaseDocumentReadiness,
        missing_fact_fields: list[str],
    ) -> WorkflowExecutionResult: ...

    def resume_case_assessment(
        self,
        *,
        thread_id: str,
        resume_value: dict[str, Any],
        state_update: dict[str, Any] | None = None,
    ) -> WorkflowExecutionResult: ...


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

    def append_tool_call(self, call: ToolCall) -> None: ...

    def append_artifact(self, art: Artifact) -> None: ...


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
    def embed(self, texts: list[str]) -> list[list[float]]: ...


@runtime_checkable
class ChatPort(Protocol):
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str: ...


@runtime_checkable
class CopilotAgentPort(Protocol):
    """会话式 Tool Calling Agent 入口。"""

    def run(
        self,
        *,
        owner_id: str,
        task_id: str,
        user_message: str,
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
class ReadinessPort(Protocol):
    """应用必需依赖的就绪检查；不得检查可选 LLM 或外部搜索服务。"""

    def check(self) -> dict[str, bool | str]: ...


@runtime_checkable
class FactProposalGeneratorPort(Protocol):
    """基于显式字段白名单和案件文档生成待人工确认的 Fact 候选。"""

    def propose(
        self,
        *,
        field_names: list[str],
        documents: list[FactProposalDocument],
    ) -> list[FactProposal]: ...


@runtime_checkable
class RetrievePort(Protocol):
    """检索三段式（BM25 + 向量 + RRF + Reranker）的高层入口。"""

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        corpus: Literal["law", "user_docs"] = "law",
        owner_id: str | None = None,
        filters: dict[str, str] | None = None,
    ) -> list[Chunk]: ...


@runtime_checkable
class ClaimSupportVerifierPort(Protocol):
    """独立校验 Claim 是否由其引用原文支持；失败时必须 fail closed。"""

    def verify(
        self,
        claims: list[EvidenceQAClaim],
        citations: list[EvidenceQACitation],
    ) -> ClaimSupportResult: ...


@runtime_checkable
class EvidenceQAGeneratorPort(Protocol):
    """基于服务端编号证据生成结构化 Claim，不生成自由长答案。"""

    def generate(
        self,
        *,
        question: str,
        citations: list[EvidenceQACitation],
    ) -> EvidenceQADraft: ...


# === 外部服务 ===


@runtime_checkable
class RiskProfilePort(Protocol):
    """风险画像评估端口（schema-evidence v1 接口预留）。

    本端口承接「自然语言目标命题 → evidence_state」的 sample-level 评估，
    对齐 ``schema-evidence-risk-profiling`` 仓库当前阶段输出
    （`evidence_v1/sample_schema_v1.json`）。

    生产实现通过 HTTP 或本地推理服务接入，infra 负责校验返回 schema。
    """

    def assess(
        self,
        target: str,
        document: str | None = None,
        *,
        language: str = "zh",
    ) -> RiskProfile: ...


@runtime_checkable
class ResearchPort(Protocol):
    """LangGraph Deep Research 的高层入口。"""

    def research(
        self,
        query: str,
        *,
        owner_id: str | None = None,
        top_k: int = 8,
        enable_web_search: bool = True,
    ) -> ResearchReport: ...


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
    def search(self, query: str, max_results: int = 3) -> list[WebResult]: ...


# === 记忆（按 owner_id / task_id 多层） ===


@runtime_checkable
class MemoryPort(Protocol):
    """4 层记忆统一入口：L1 短期 / L2 摘要 / L3 用户画像 / L4 语义事实。"""

    # L1 短期：按 task_id
    def append_message(self, task_id: str, msg: Message) -> None: ...

    def recent_messages(self, owner_id: str, task_id: str, n: int) -> list[Message]: ...

    # L2 摘要：按 task_id
    def get_summary(self, owner_id: str, task_id: str) -> str | None: ...

    def maybe_summarize(self, owner_id: str, task_id: str, threshold: int = 20) -> None: ...

    # L3 用户画像：按 owner_id（跨 task / 跨设备）
    def get_profile(self, owner_id: str) -> SessionProfile: ...

    def update_profile(self, owner_id: str, facts: dict[str, str]) -> None: ...

    # L4 语义事实：按 owner_id
    def recall_semantic(self, owner_id: str, query: str, k: int) -> list[Fact]: ...

    def explain_recall(self, owner_id: str, query: str, k: int) -> MemoryRecallTrace: ...

    # L4 事实列表（管理面板展示，过滤已 superseded / 过期，Step 031a）
    def list_facts(self, owner_id: str) -> list[Fact]: ...

    # L4 单条事实删除（被遗忘权细粒度，Step 034）：返回是否真删了
    def delete_fact(self, owner_id: str, fact_id: str) -> bool: ...

    # 主动遗忘（被遗忘权）：按 owner_id 级联清除（Step 030d）
    def forget(self, owner_id: str, *, scope: str = "memory") -> ForgetResult: ...


@runtime_checkable
class SummaryStorePort(Protocol):
    """L2 摘要存储（``task_summaries`` 表，Step 030b）。"""

    def get(self, task_id: str, owner_id: str) -> TaskSummary | None: ...

    def upsert(self, summary: TaskSummary) -> None: ...

    def delete_owner(self, owner_id: str) -> int:
        """删除该 owner 的全部摘要，返回删除条数（主动遗忘，Step 030d）。"""
        ...


@runtime_checkable
class ProfileStorePort(Protocol):
    """L3 用户画像存储（``profiles`` 表，Step 030d）。

    画像是跨 task 的稳定偏好快照（按 ``owner_id``），无 TTL，靠主动遗忘清除。
    """

    def get(self, owner_id: str) -> SessionProfile | None: ...

    def upsert(self, profile: SessionProfile) -> None: ...

    def delete_owner(self, owner_id: str) -> int:
        """删除该 owner 的画像，返回删除条数（0 或 1）。"""
        ...


@runtime_checkable
class MemorySettingsStorePort(Protocol):
    """每用户记忆开关存储（``memory_settings`` 表，Step 031a）。

    按 ``owner_id`` 存两个布尔偏好；``get`` 缺失返回 None（调用方视为双开默认）。
    """

    def get(self, owner_id: str) -> MemorySettings | None: ...

    def upsert(self, settings: MemorySettings) -> None: ...


@runtime_checkable
class MemoryJobSchedulerPort(Protocol):
    """记忆后台作业调度（§14.1 显式调度起步，Step 030b/030c）。

    回复完成后由 use case 显式调用，后台 best-effort 跡出 L2 摘要 / L4 固化，
    不阻塞主回复；失败下一轮按 watermark 自愈补。
    """

    def schedule_summarization(self, owner_id: str, task_id: str) -> None: ...

    def schedule_consolidation(self, owner_id: str, task_id: str) -> None: ...


@runtime_checkable
class FactStorePort(Protocol):
    """L4 语义事实存储（Chroma ``memory_facts`` collection，Step 030c）。

    向量化由调用方（固化 worker / 记忆适配器）用 ``EmbedPort`` 完成后传入，
    本端口只管"带 owner 隔离的向量 upsert / 近邻查询 / 逻辑删除 / 容量管理"。
    """

    def add(self, fact: Fact, embedding: list[float]) -> None: ...

    def query(self, owner_id: str, embedding: list[float], k: int) -> list[tuple[Fact, float]]:
        """按 owner 隔离的近邻检索，返回 ``(fact, 相似度)`` 倒序（含已 superseded，由调用方过滤）。"""
        ...

    def get(self, owner_id: str, fact_id: str) -> Fact | None: ...

    def mark_superseded(self, owner_id: str, fact_id: str, superseded_by: str) -> None: ...

    def list_owner(self, owner_id: str) -> list[Fact]: ...

    def delete(self, owner_id: str, fact_id: str) -> None: ...

    def delete_owner(self, owner_id: str) -> int:
        """删除该 owner 的全部事实，返回删除条数（主动遗忘，Step 030d）。"""
        ...

    def count(self, owner_id: str) -> int: ...


@runtime_checkable
class ConsolidationStatePort(Protocol):
    """L4 固化进度水位存储（``consolidation_state`` 表，Step 030c）。"""

    def get(self, task_id: str, owner_id: str) -> ConsolidationState | None: ...

    def upsert(self, state: ConsolidationState) -> None: ...

    def delete_owner(self, owner_id: str) -> int:
        """删除该 owner 的全部固化水位，返回删除条数（主动遗忘，Step 030d）。"""
        ...


# === 审计（Step 021） ===


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
