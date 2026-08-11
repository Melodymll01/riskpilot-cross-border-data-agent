# RiskPilot V2：证据驱动的数据出境合规案件工作台

## 1. 文档状态

- 状态：已接受，进入增量实施
- 目标版本：V2
- 适用仓库：`riskpilot-cross-border-data-agent`
- 设计原则：保留现有 DDD 与 Port 边界，增量建设 `/api/v3`
- 迁移原则：V2 未达到替代门槛前，`/api/v2` 保持可用

## 2. 产品定位

RiskPilot V2 不再以“法规聊天机器人”为核心，而是一套围绕案件、材料、事实、证据、
规则、评估和审批展开的数据出境合规辅助系统。

系统需要完成以下闭环：

```text
创建案件
→ 上传合同、制度、数据清单和评估材料
→ 异步解析文档并建立案件级索引
→ 提取结构化案件事实
→ 用户确认缺失、低置信度和冲突事实
→ 版本化规则引擎计算候选合规路径
→ 检索法规证据并生成风险项与整改清单
→ 校验结论与引用
→ 人工复核
→ 生成不可变 Assessment 快照
```

## 3. 核心目标

1. 每个重要结论都能追溯到案件事实、原文证据、法规条款和规则版本。
2. 长流程支持暂停、恢复、重试、取消和人工介入。
3. LLM 负责提取、检索和解释，确定性规则负责门槛计算。
4. 用户文档在 Workspace、Case 和 Document 三个层级严格隔离。
5. 文档解析、检索、事实提取、规则判断和 Agent 运行都具有版本化评测。
6. V2 可在本地 SQLite/ChromaDB 上演示，也能通过 Port 替换为生产组件。

## 4. 非目标

第一阶段不实现：

- 自动向监管平台提交材料；
- 自动发送邮件或执行外部不可逆操作；
- 无人工审核的正式法律意见；
- LLM 直接发布规则、批准报告、删除文档或修改权限；
- 多个自主 Agent 无约束对话；
- 模型训练平台、商业计费和复杂企业组织架构。

## 5. 用户与权限

### 5.1 Workspace 角色

| 角色 | 权限 |
| --- | --- |
| `viewer` | 查看 Workspace 内授权案件、材料和报告 |
| `editor` | 创建案件、上传材料、维护普通事实 |
| `reviewer` | 确认关键事实、复核和审批 Assessment |
| `admin` | 管理成员、规则版本、公共语料和审计 |

### 5.2 授权原则

- 所有 V3 业务对象必须归属 `workspace_id`。
- 案件检索必须同时带 `workspace_id` 和 `case_id`。
- 权限字段由服务端运行时注入，模型不得提供或覆盖。
- SQL 查询和向量检索都必须执行范围过滤。
- 跨 Workspace 和跨 Case 泄漏率必须为 0。

## 6. 产品信息架构

主导航：

```text
首页
合规案件
法规中心
监管研究
评测中心
审计中心
设置
```

案件详情页：

```text
概览
材料
事实
证据矩阵
评估
整改清单
问答
运行记录
审计
```

## 7. AI 能力边界

### 7.1 Evidence QA

简单问答使用普通应用管线，不使用 LangGraph，不运行自由 ReAct 循环。

适用场景：

- 解释法规概念；
- 查询精确条款；
- 总结当前案件材料；
- 解释某个 Assessment 结论；
- 回答有明确证据支持的事实问题。

处理链路：

```text
输入安全检查
→ 范围校验
→ 查询改写
→ 混合检索
→ 重排
→ 精确条款或文档 Span 读取
→ 证据充分性判断
→ 回答生成
→ Claim-Citation 校验
→ 返回或拒答
```

Evidence QA 不直接回答完整路径、综合风险评级和正式整改方案。这类问题应引导用户
启动 Case Assessment。

### 7.2 Case Assessment

Case Assessment 是核心 LangGraph 工作流，负责：

- 等待案件文档处理完成；
- 提取候选事实；
- 检测关键事实缺失和证据冲突；
- 在必要节点请求人工确认；
- 选择法规与规则快照；
- 执行确定性规则；
- 生成风险项、材料缺口、整改行动和报告；
- 校验引用；
- 等待 Reviewer 审批；
- 持久化不可变 Assessment。

### 7.3 Deep Research

Deep Research 使用独立 LangGraph，负责：

- 研究问题拆解；
- 官方法规、监管网页和 Workspace 材料检索；
- 来源权威性分级；
- 证据缺口补检；
- 结论与引用核验；
- 专题报告与法规版本对比；
- 将研究结果关联到案件。

## 8. 领域模型

### 8.1 Workspace

```text
Workspace
- workspace_id
- name
- status
- created_by
- created_at
- updated_at
```

```text
WorkspaceMembership
- workspace_id
- user_id
- role
- joined_at
```

### 8.2 Case

```text
Case
- case_id
- workspace_id
- title
- description
- jurisdiction
- scenario_type
- assessment_date
- status
- owner_id
- reviewer_id
- active_assessment_id
- created_at
- updated_at
```

案件状态：

```text
draft
collecting
processing_documents
facts_pending_confirmation
ready_for_assessment
assessing
review_required
completed
archived
```

状态转换由领域规则控制，非法跳转必须拒绝。

### 8.3 Document

```text
Document
- document_id
- workspace_id
- logical_name
- document_type
- status
- created_by
- current_version_id
- created_at
- updated_at
```

```text
DocumentVersion
- version_id
- document_id
- version_number
- object_key
- sha256
- mime_type
- size_bytes
- parser_version
- page_count
- created_at
```

```text
CaseDocument
- case_id
- document_id
- purpose
- added_by
- added_at
```

### 8.4 ProcessingJob

```text
ProcessingJob
- job_id
- document_version_id
- status
- current_stage
- progress
- error_code
- error_message
- retry_count
- started_at
- completed_at
```

文档状态：

```text
uploaded
queued
parsing
ocr
chunking
indexing
ready
failed
deleted
```

### 8.5 Fact 与 Evidence

```text
CaseFact
- fact_id
- case_id
- field_name
- value_json
- status
- source_type
- confidence
- criticality
- created_by
- confirmed_by
- confirmed_at
- version
```

事实状态：

```text
proposed
confirmed
rejected
conflicting
unknown
```

LLM Fact 提议边界：

- 调用方显式传入允许抽取的 `field_names`，模型不能创建白名单外字段；
- 只读取当前 Case 已绑定、已解析、已完成索引且处于 `ready` 的当前
  DocumentVersion；
- 模型输出必须包含页码和逐字 quote，应用层重新读取解析快照核验；
- 模型候选统一以 `critical + proposed` 写入，不能直接变成 `confirmed`；
- 同字段已有未拒绝事实且值不一致时，新候选标记为 `conflicting`；
- Reviewer/Admin 确认一个候选时，同字段其他 active facts 在同一事务中转为
  `rejected`，保证最多一个 confirmed 值；
- 一批候选在全部字段、证据和冲突校验通过后原子写入，任一失败不留部分结果；
- `confirmed` 仍只由 Reviewer/Admin 人工转换，规则引擎仍只消费 confirmed facts。

```text
EvidenceSpan
- evidence_id
- case_id
- fact_id
- document_id
- version_id
- page_number
- section_path
- start_offset
- end_offset
- quote
- confidence
- source_hash
```

### 8.6 Regulation 与 Policy

```text
Regulation
- regulation_id
- title
- jurisdiction
- issuing_authority
- source_url
```

```text
RegulationVersion
- regulation_version_id
- regulation_id
- version_name
- effective_from
- effective_to
- status
- source_hash
- fetched_at
```

```text
RegulationClause
- clause_id
- regulation_version_id
- article_number
- heading
- text
- parent_clause_id
```

```text
PolicyRule
- rule_id
- ruleset_version
- effective_from
- effective_to
- required_fact_fields
- condition_json
- result_json
- source_clause_ids
- status
```

### 8.7 Assessment

```text
Assessment
- assessment_id
- case_id
- version
- status
- ruleset_version
- regulation_snapshot_id
- fact_snapshot_id
- risk_level
- candidate_paths
- generated_by_run_id
- approved_by
- approved_at
- created_at
```

Assessment 是不可变快照。事实、法规或规则变化后创建新版本，旧版本标记为
`superseded`，不得覆盖。

Assessment 引用完整性：

- `rule_trigger` Finding 的 `fact_ids` 来自 PolicyEvaluation 实际消费的 confirmed facts；
- document Fact 的当前版本 `CaseFactEvidence` 会冻结为
  `AssessmentEvidenceCitation`，保存原 evidence ID、Fact/version、Document/version、
  页码、quote、offset 与 source SHA；
- Finding 的 `evidence_ids` 必须完整引用其 document facts 的快照，不能引用其他 Fact；
- Finding 的 `rule_ids` / `clause_ids` 必须与 Assessment 内 PolicyEvaluation 快照一致；
- 生成 Assessment 和 Reviewer 批准前都会重新读取当前 Fact、DocumentVersion 与解析页；
- Fact 版本、文档当前版本、SHA、页码、quote 或 offset 任一漂移都会阻止生成/批准；
- user 来源 Fact 可通过 `fact_ids + fact_versions` 追溯，不伪造文档证据引用。

### 8.8 Run

```text
AgentRun
- run_id
- workspace_id
- case_id
- workflow_type
- status
- thread_id
- checkpoint_id
- model_config_snapshot
- token_usage
- cost
- started_at
- completed_at
```

运行状态：

```text
queued
running
waiting_for_user
waiting_for_review
retrying
completed
failed
cancelled
```

## 9. Case Assessment Graph

```text
load_case
→ authorize
→ validate_documents
→ extract_fact_candidates
→ merge_existing_facts
→ detect_missing_facts
→ request_fact_confirmation
→ retrieve_case_evidence
→ validate_evidence
→ detect_conflicts
→ resolve_conflicts
→ select_policy_snapshot
→ evaluate_policy_rules
→ determine_candidate_paths
→ generate_findings
→ generate_action_items
→ draft_assessment
→ verify_claims_and_citations
→ repair_assessment
→ human_review
→ persist_assessment
→ complete
```

允许中断的节点：

1. 关键事实缺失；
2. 文档证据与用户输入冲突；
3. 关键事实置信度不足；
4. 适用日期不明确；
5. 高风险结论确认；
6. 最终报告审批。

循环预算：

- 事实补充最多 2 轮；
- 证据检索最多 3 轮；
- 引用修复最多 1 轮；
- 超出预算转人工处理，不无限循环。

## 10. 文档处理

```text
upload
→ validate_file
→ persist_original
→ create_version
→ enqueue_job
→ extract_structure
→ extract_text
→ ocr_missing_pages
→ extract_tables
→ normalize
→ split
→ enrich_metadata
→ index_vector
→ index_bm25
→ quality_check
→ ready
```

第一阶段支持 PDF、DOCX、TXT、Markdown；第二阶段增加 XLSX、CSV、PNG、JPG。

Chunk 至少携带：

```text
workspace_id
case_id
document_id
version_id
owner_id
page_number
section_path
chunk_index
content_type
source_hash
parser_version
```

## 11. 检索

语料分为：

1. Regulatory Corpus：法规、监管问答、官方指南；
2. Workspace Knowledge Base：企业制度和通用模板；
3. Case Evidence：当前案件合同、清单和说明材料。

排序链路：

```text
Vector Recall
+ BM25 Recall
→ RRF Fusion
→ Metadata Filter
→ Cross-Encoder Rerank
→ Authority Boost
→ Temporal Validity Check
→ Context Expansion
```

检索必须真正下推 `workspace_id`、`case_id`、`document_id`、`effective_at` 和法规版本。

## 12. 工具与服务

LLM 可见工具控制在以下范围：

- `search_regulations`
- `get_regulation_clause`
- `search_case_evidence`
- `read_document_span`
- `search_regulatory_updates`
- `compare_document_versions`

固定节点服务：

- `FactExtractionService`
- `EvidenceValidationService`
- `ConflictDetectionService`
- `ClaimCitationVerifier`
- `ReportGenerationService`
- `InputGuardrailService`
- `OutputGuardrailService`

确定性服务：

- `PolicyRuleEngine`
- `AccessControlService`
- `CaseStateMachine`
- `AssessmentVersionService`
- `ApprovalService`
- `DocumentLifecycleService`
- `AuditService`

LLM 不得直接执行删除、审批、发布规则、修改权限和持久化最终 Assessment。

## 13. API V3

核心资源：

```text
/api/v3/workspaces
/api/v3/cases
/api/v3/cases/{case_id}/documents
/api/v3/processing-jobs
/api/v3/cases/{case_id}/facts
/api/v3/qa
/api/v3/cases/{case_id}/assessment-runs
/api/v3/research-runs
/api/v3/runs
/api/v3/assessments
/api/v3/rulesets
```

长任务先返回 `run_id` 或 `job_id`，任务生命周期不得依赖单个 HTTP 连接。

## 14. 事件协议

V3 不对外暴露原始思维链，使用可审计的阶段事件：

```text
run_started
stage_started
stage_progress
stage_completed
tool_started
tool_completed
evidence_found
facts_proposed
fact_confirmation_required
conflict_detected
human_input_required
human_review_required
artifact_ready
answer_delta
run_paused
run_resumed
run_retrying
run_failed
run_completed
```

## 15. 安全

### 15.1 文件

- 扩展名、MIME、文件魔数三重校验；
- 文件大小、页数、OCR 时间和内存限制；
- 防 ZIP bomb；
- 不执行宏和嵌入对象；
- 原始文件只通过鉴权接口访问；
- 删除时级联对象、Chunk、向量、事实引用和运行状态。

### 15.2 Prompt Injection

- 用户文档视为不可信证据，不得作为 system message；
- 文档中的指令不得改变工具权限；
- 工具参数必须经过 Pydantic 校验；
- 正式引用必须重新读取原始 Span；
- 输出前执行 Claim-Citation 校验。

### 15.3 隐私

- 日志和 Trace 不保存完整正文；
- checkpoint 只保存对象 ID 和轻量状态；
- 案件事实不得进入跨案件长期记忆；
- 支持数据导出和级联删除。

### 15.4 法律输出

- 法规结论带版本和生效日期；
- 区分事实、规则结果和建议；
- 证据不足不得输出确定性结论；
- 正式报告必须经过 Reviewer。

## 16. 评测

评测目录：

```text
evaluations/
  document_processing/
  retrieval/
  fact_extraction/
  policy_rules/
  case_assessment/
  evidence_qa/
  deep_research/
  security/
  fault_injection/
```

建议门禁：

| 指标 | 目标 |
| --- | ---: |
| 跨 Workspace 泄漏 | 0 |
| 跨 Case 泄漏 | 0 |
| Claim-Citation 支撑率 | ≥ 95% |
| 坏 Claim 过滤准确率 | 100% |
| 关键事实召回率 | ≥ 95% |
| 高风险规则召回率 | ≥ 95% |
| 故障恢复成功率 | ≥ 99% |
| 无依据结论率 | ≤ 2% |
| Assessment 字段完整率 | 100% |

这些是工程目标，不代表当前仓库已经达到。

## 17. 迁移阶段

### Phase 0：设计与基线

- 固化设计、ADR 和迁移基线；
- 保留 `/api/v2`；
- 建立 `/api/v3` 增量边界。

### Phase 1：Workspace 与 Case

- 领域模型；
- Repository；
- 应用用例；
- `/api/v3/cases`。

### Phase 2：Document

- Document/DocumentVersion；
- ObjectStorePort；
- ProcessingJob；
- 案件级异步上传。

### Phase 3：Evidence

- 三类语料；
- 范围过滤；
- EvidenceSpan；
- 原文证据查看。

### Phase 4：Fact 与 Policy

- CaseFact；
- 人工确认；
- 规则引擎；
- Golden Case。

### Phase 5：Evidence QA

- 法规和案件问答；
- 引用校验；
- 退役旧 QA Agent。

### Phase 6：Case Assessment Graph

- LangGraph runtime；
- checkpointer；
- interrupt/resume；
- 节点幂等；
- 人工审批。

### Phase 7：Deep Research

- 官方来源研究；
- 来源分级；
- 法规版本对比；
- 研究结果关联案件。

### Phase 8：评测与展示

- 评测中心；
- Trace 和成本；
- 故障恢复 Demo；
- 安全红队；
- 报告导出。

## 18. 当前实施切片

截至 2026-08-11，已完成：

1. Workspace、成员角色和 Case 状态机；
2. 案件级 Document、DocumentVersion、原始对象存储与 ProcessingJob；
3. 页级解析、案件范围证据 Chunk、向量 + BM25 + RRF 检索；
4. CaseFact、版本化证据引用、关键事实人工确认；
5. Workspace 隔离的 PolicyRule 和只消费 confirmed facts 的确定性规则引擎；
6. 不可变 Assessment、Finding、ActionItem、版本替换与人工审批；
7. AgentRun、RunCheckpoint、RunEvent、乐观锁和连续事件持久化；
8. LangGraph 1.x Case Assessment 运行时、SQLite checkpointer、
   `interrupt/Command` 暂停恢复、失败重试和取消；
9. `/api/v3` 的 Workspace、Case、Document、Evidence、Fact、Policy、
   Assessment 和 Assessment Run 资源接口；
10. `/api/v2` 保持可用，未进行 Big Bang 退役。
11. `/api/v3/qa` Evidence QA：公共法规、Workspace Knowledge、Case Evidence 和
    Assessment 四类服务端授权范围；
12. 结构化 Claim 生成、版本化页码引用、`structural_v1` 覆盖校验和独立
    `independent_llm_v1` 语义支持校验；
13. Case/Workspace 引用在回答前重新读取当前 DocumentVersion 解析页，并校验
    `source_sha256`、CaseDocument 绑定和原文 quote。
14. `evaluations/evidence_qa` 离线评测基线：覆盖结构引用、否定/数值蕴含、
    引用漂移、伪造引用、跨 Workspace/Case 越权和安全拒答；Oracle 仅用于协议自检，
    正式候选必须提供独立预测文件。
15. `bounded_filter_v1` 结果层有限修复：只删除无引用、未知引用或语义不受支持的
    Claim；至少保留一条可信 Claim 时降级为部分回答，全部失败或验证器协议异常时
    仍然安全拒答，不改写 Claim、不补造 Citation、不增加模型调用。
16. `production_verifier` 实测入口：固定 Claim/Citation，仅调用当前生产
    `independent_llm_v1`，模型输入隔离 Gold 标签；保存逐 Case predictions、错误和
    评测报告，以支持模型升级前后的可复现对比。
17. 文档 Fact 提议：字段白名单驱动的结构化 LLM 输出、当前版本原文二次核验、
    同字段值冲突检测、批量原子写入；所有候选强制为 critical，必须由 Reviewer/Admin
    确认后才能进入规则计算。
18. Assessment 引用快照：规则 Finding 自动关联 consumed Fact、Fact Evidence 和
    Policy Clause；保存不可变 EvidenceCitation，并在生成与批准前重新验证 Fact /
    DocumentVersion / SHA / quote / offset 漂移。

当前 Case Assessment Graph 已落地的确定性骨架：

```text
load_case
→ authorize
→ validate_documents
→ detect_missing_facts
→ select_policy_snapshot
→ evaluate_policy_rules
→ draft_assessment
→ human_review
→ complete
```

其中正式 Assessment 的生成和审批由应用用例执行，LangGraph 只负责流程推进与中断恢复，
不会直接批准报告或写入最终 Assessment。checkpoint 只保存对象 ID 和轻量状态，禁用
pickle fallback。

尚未完成：

1. Fact 提议与 `fact_confirmation` 中断的前端联动；
2. 基于 Claim-Citation 评测基线的完整生成器 + 验证器端到端生产实测；
3. Deep Research Graph；
4. V3 案件工作台前端；
5. 评测中心、故障注入指标、安全红队和报告导出。

后续仍按“一个可验证步骤对应一个中文 commit”推进。
