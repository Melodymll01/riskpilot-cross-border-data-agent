# RiskPilot 架构总览

## 当前架构

当前采用 `/api/v2` 与 `/api/v3` 并行的 Strangler Fig 迁移方式，继续遵守四层结构：

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

现有 QA 使用自研 ReAct，Research 使用旧 Agentic RAG 适配器，继续由 `/api/v2`
提供服务。案件工作台使用 `/api/v3`，已覆盖 Workspace、Case、Document、Evidence、
Fact、Policy、Assessment 和 Assessment Run。

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
6. `/api/v2` 与 `/api/v3` 在迁移期并行运行。

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

### Case Assessment

```text
AssessmentRunUseCase
  → Domain Repositories / PolicyRuleEngine / AssessmentManagementUseCase
  → WorkflowRuntimePort
    → LangGraphWorkflowRuntime
      → SQLite checkpointer
```

- `AgentRunRepoPort` 保存产品可见的 Run、轻量 checkpoint 和审计事件；
- LangGraph SQLite checkpointer 保存框架执行位置，两者使用同一个 `thread_id` 关联但
  不互相取代；
- `AssessmentRunUseCase` 在中断点重新读取 Document/Fact/Policy Repository，不信任
  客户端提交的业务状态；
- Graph 不保存文档正文、证据原文、原始 prompt、凭证或思维链；
- `assessment_generation` 是内部中断：应用层调用确定性 Assessment 用例后再恢复 Graph；
- `assessment_review` 只能由 Reviewer/Admin 通过审批用例完成；
- 同一 Case 同一工作流只允许一个活动 Run，Run/检查点/事件写入使用乐观锁；
- 支持进程重建恢复、失败重试、取消和增量事件查询。

### 当前边界

已实现 Evidence QA、显式文档 Fact 提议与 Case Assessment 的工程骨架和确定性闭环。
Fact 提议具备字段白名单、当前版本原文复核、冲突检测和 Reviewer 唯一确认，但尚未
内联到 LangGraph `fact_confirmation` 节点；原生案件工作台通过 Run 事件完成候选生成、
证据展示、Reviewer 确认和继续运行。Assessment 已实现 Fact / Evidence / Clause
不可变引用快照和审批前漂移校验；LLM 引用重写、Deep Research Graph 和完整多 Case
管理前端仍待后续切片。

完整产品和技术设计见：

- `docs/design/riskpilot-v2.md`
- `docs/design/v2-migration-baseline.md`
- `docs/decisions/ADR-014-v2-增量迁移与领域内核.md`
- `docs/decisions/ADR-015-AI能力分层与LangGraph边界.md`
- `docs/decisions/ADR-016-案件证据与规则快照.md`
