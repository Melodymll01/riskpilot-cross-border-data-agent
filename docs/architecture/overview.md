# RiskPilot 架构总览

## 当前架构

当前 `/api/v2` 提供通用 Copilot、知识库和记忆能力，`/api/v3` 提供案件工作台能力；
两套 API 按产品域并行，继续遵守四层结构：

```text
api → app → domain
       ↑
     infra
```

- `domain`：纯领域模型和 Port；
- `app`：用例、Agent 编排和依赖装配；
- `infra`：数据库、检索、模型、记忆、鉴权等适配器；
- `api`：FastAPI 路由、鉴权依赖和 SSE；
- `frontend`：无构建步骤的浏览器端工作台。

Copilot 使用 LangChain 标准 Tool Calling Agent，Deep Research 与 Case Assessment
使用两张独立 LangGraph；案件工作台使用 `/api/v3`，覆盖 Workspace、Case、Document、
Text Evidence、Visual Evidence、Fact、Policy、Assessment 和 Assessment Run。

本轮生产化升级把 **Case Assessment Agent** 设为唯一产品主线。Copilot、Memory、
Visual Evidence 和 Deep Research 都是辅助模块，不与正式案件评估争夺项目叙事。
阶段路线、目标架构和验收门禁见：

- `docs/roadmap/autumn-recruitment-production-plan.md`
- `docs/implementation/phase-00-baseline-and-product-focus.md`

## V2 已落地架构

```text
Frontend
  → API V3
    → Application Use Cases
      → Domain
      → WorkflowRuntimePort
        → LangGraph Runtime
      → Infrastructure Ports
        → SQL / Object Store / Retrieval / LLM
```

V2 的关键边界：

1. Evidence QA 是普通应用服务，不依赖 LangGraph；
2. Case Assessment 和 Deep Research 使用 LangGraph；
3. LangGraph 只保存执行状态，不取代领域 Repository；
4. 合规门槛由版本化规则引擎计算；
5. 文档、事实、证据和 Assessment 均为一等领域对象；
6. `/api/v2` 与 `/api/v3` 按通用 Copilot / 案件工作台边界并行运行。

### LangChain Copilot

```text
RunCopilotUseCase
  → CopilotAgentPort
    ← LangChainComplianceAgent
      → create_agent / ChatOpenAI
      → ToolRuntime(owner_id, task_id)
      → law / user docs / web / risk profile tools
```

- 不再维护自定义 JSON action parser 和 ReAct 循环；
- owner_id 由 `ToolRuntime` 注入，不进入模型可控参数；
- Tool Call 仍写入 TaskRepo，API 继续输出既有 SSE 事件；
- 记忆由应用层装配为额外 SystemMessage。

### AI 可观测性

```text
LangChain Copilot / LangGraph Research / LangGraph Assessment / Risk Profile
  → TracePort
    ├─ NoopTraceAdapter（默认）
    └─ LangSmithTraceAdapter（显式启用）
```

- LangSmith 是可替换基础设施 Adapter，不进入领域逻辑；
- 默认关闭且无网络；项目专属开关避免 SDK 全局自动追踪绕过隐私策略；
- Client 出站前隐藏输入/输出，删除序列化 Prompt、事件和附件；
- 异常只记录类型，异常正文和 traceback 统一脱敏；
- metadata 采用白名单，业务 ID 使用 HMAC 哈希；
- 不上传案件正文、证据原文、记忆原文、Prompt、回答、图片、Embedding 或思维链。

### 风险评估模型

```text
RunCopilotUseCase / risk_profile_assess tool
  → RiskProfilePort
    ← HttpRiskProfileClient
      → POST /v1/risk-profile
```

- `RISK_PROFILE_API_BASE` 留空时 fail closed，明确返回模型未配置；
- HTTP、JSON 和 `RiskProfile` schema 错误翻译为领域异常；
- 风险模型可独立部署和升级，不把训练仓库依赖引入本应用；
- Trace 只记录 target/document 长度、evidence state、证据数和状态。

### LangGraph Deep Research

```text
plan → retrieve → assess ── sufficient ─→ generate
                    ├─ partial ─────────→ retrieve
                    └─ insufficient ────→ web_search → generate
```

- 检索始终携带 owner_id；
- 最多三轮补查，防止无限循环；
- 证据为空且禁用 Web Search 时安全拒答。

### Evidence QA

```text
POST /api/v3/qa
  → EvidenceQAUseCase
    → server-authorized scope
    → Regulatory / Workspace / Case / Assessment retrieval
    → reread current source span
    → EvidenceQAGeneratorPort
    → structural_v1 Claim-Citation verification
    → ClaimSupportVerifierPort
    → answer or refuse
```

- Regulatory 只检索公共法规语料，不携带当前用户私人 KB owner；
- Workspace 范围只读取 `document_type=workspace_knowledge` 的 ready 当前版本，且只有
  Workspace admin 可以上传该类文档；
- Case 范围按 `workspace_id + case_id` 下推过滤；
- Document Citation 必须带 `document_id`、`document_version_id`、页码和 SHA-256；
- 回答前重新读取当前解析页，确认版本、SHA、CaseDocument 绑定和 quote 仍一致；
- LLM 只生成原子 Claim 和 citation IDs，服务端不直接透出自由长答案；
- 独立验证调用不能扩大 Claim 声明的引用范围；不受支持的 Claim 只能被结果层移除，
  至少保留一条可信 Claim 时降级为部分回答，否则 fail closed；
- API 不返回 Prompt、原始模型响应或思维链。

### AI 长期记忆提取

```text
user messages only
  → deterministic source filter
  → LLM selects quote / tags / salience
  → server verifies message_id + verbatim quote
  → sensitive / injection / transient gates
  → embed, deduplicate or supersede
  → owner-scoped L4 Fact
```

- 助手、系统和工具消息不进入提取上下文，避免把模型回答反写成用户事实；
- 输入以 JSON 数组传递 `message_id + content`，用户文本不能突破消息边界；
- LLM 不生成最终事实文案，`Fact.text` 直接使用服务端核验过的用户逐字 quote；
- 密码、API Key、联系方式、身份证/银行卡、生物特征、健康/政治等高敏属性、
  提示注入和一次性/假设性请求均 fail closed；
- `Fact` 持久化 `source_message_id + source_quote`，管理面板展示来源原话；
- `evaluations/memory_extraction` 只证明确定性协议门禁，不冒充生产模型抽取准确率。

### AI 长期记忆召回

```text
query
  → owner-scoped vector candidates
  → superseded / TTL / minimum semantic gates
  → hybrid_v1 score
      semantic 0.65
      + confidence 0.15
      + salience 0.15
      + freshness 0.05
  → minimum final score
  → top-k prompt injection + safe recall trace
```

- `domain.memory.MemoryRecallPolicy` 是纯领域策略，生产和离线评测复用同一实现；
- 向量库只负责 owner 隔离的候选获取，扩大候选池后再按事实质量重排；
- `MemoryRecallTrace` 记录候选数、过滤原因和命中分解，不记录 embedding、Prompt 或思维链；
- `/api/v2/memory/recall/explain` 只向已认证 owner 展示其自己的事实与分数；
- `evaluations/memory_recall` 验证排序和安全过滤协议，不冒充真实 embedding 召回准确率。

### Visual Evidence

- PNG/JPEG/WebP 经魔数、解码、尺寸和大小校验后写入对象存储；
- Chinese-CLIP 分别生成 image embedding 与 text embedding；
- SQLite 查询先按 `workspace_id + case_id` 过滤，再计算余弦相似度；
- Viewer 可以检索，Editor/Reviewer/Admin 才能上传；
- `evaluations/visual_retrieval` 默认生成 12 张合成图片；
- `run.py --live` 才下载并实测 Chinese-CLIP，CI 不下载模型。

### Case Assessment

```text
AssessmentRunUseCase
  → Domain Repositories / PolicyRuleEngine / AssessmentManagementUseCase
  → WorkflowRuntimePort
    → LangGraphWorkflowRuntime
      → local SQLite / production PostgreSQL checkpointer
```

- `AgentRunRepoPort` 保存产品可见的 Run、轻量 checkpoint 和审计事件；
- LangGraph checkpointer 只保存框架执行位置；local 使用 SQLite，production 使用
  PostgreSQL，两者与产品 Run 使用同一个 `thread_id` 关联但不互相取代；
- LangChain function calling 生成 EvidencePlan；Typed Tool Registry 复核 Schema、角色、
  阶段、timeout/retry 和副作用级别；
- 案件证据检索按调查问题有限循环，规则计算、引用校验和人工审批是不可跳过的单向门禁；
- `AssessmentRunUseCase` 在中断点重新读取 Document/Fact/Policy Repository，不信任
  客户端提交的业务状态；
- Graph 不保存文档正文、证据原文、原始 prompt、凭证或思维链；
- `assessment_generation` 是内部中断：应用层调用确定性 Assessment 用例后再恢复 Graph；
- `assessment_review` 只能由 Reviewer/Admin 通过审批用例完成；
- 同一 Case 同一工作流只允许一个活动 Run，Run/检查点/事件写入使用乐观锁；
- 支持进程重建恢复、失败重试、取消和增量事件查询。

### PostgreSQL 生产存储 Profile

```text
STORAGE_BACKEND=sqlite
  → SQLite Repository + Chroma local profile

STORAGE_BACKEND=postgres
  → SQLAlchemy Workspace / Case / Document / Evidence / Fact
  → SQLAlchemy Policy / Assessment / AgentRun / RunEvent
  → PostgreSQL transaction + JSONB + partial unique index
```

- Domain Pydantic Model 不依赖 SQLAlchemy ORM；
- `SqlAlchemyDatabase` 统一 Engine、短生命周期 Session 和事务边界；
- Alembic 初始 revision 创建 20 张核心业务表和 `alembic_version`；
- `AgentRun` 使用 `WHERE revision = expected_revision` 乐观锁；
- PostgreSQL partial unique index 保证同一 Case + Workflow 只有一个活动 Run；
- Assessment 版本切换和审批使用条件更新，防止活动版本漂移；
- Evidence Index 在 Phase 2 暂以 JSON 向量保持完整 PostgreSQL profile，Phase 3
  升级为 pgvector + PostgreSQL FTS；
- User/Task/Memory/Audit 等辅助 V2 模块当前仍复用 SQLite，避免一次性迁移全仓。

### 当前边界

已实现 Evidence QA、图内文档 Fact 提议与 Case Assessment 的证据驱动闭环。
Fact 提议具备字段白名单、当前版本原文复核、冲突检测和 Reviewer 唯一确认，并已通过
Typed Tool Registry 接入 LangGraph；原生案件工作台通过 Run 事件完成候选生成、证据展示、
Reviewer 确认和继续运行，并支持 Workspace / Case 创建与多 Case 导航。
案件材料支持浏览器上传、解析、索引、进度展示和失败重试；材料列表同时返回当前版本
最新 ProcessingJob，因此页面刷新后仍可恢复 job_id 和处理状态。Assessment 已实现
Fact / Evidence / Clause 不可变引用快照和审批前漂移校验；Deep Research Graph 与
Case 图片检索已落地，图片暂作为检索辅助证据，尚未进入正式 Assessment 引用闭包。

核心 Agent 工具执行经过统一 Tool Policy：高权限工具不能注册，运行时 scope 不能由模型
传入，可逆写工具必须显式 allowlist 且禁止自动重试。网页正文和用户提交 URL 使用
SSRF-safe Client，逐跳校验 DNS/IP、重定向、Content-Type 与响应体大小。

完整产品和技术设计见：

- `docs/design/riskpilot-v2.md`
- `docs/decisions/ADR-014-v2-增量迁移与领域内核.md`
- `docs/decisions/ADR-015-AI能力分层与LangGraph边界.md`
- `docs/decisions/ADR-016-案件证据与规则快照.md`
