# Phase 5 实施复盘：核心 Case Assessment Agent

- 状态：已完成
- 日期：2026-08-17
- 前置提交：`b409380`

## 1. 本阶段目标

把现有“确定性 LangGraph 节点骨架”升级为真正的证据驱动案件 Agent：

```text
load_case
→ authorize
→ inspect_documents
→ build_evidence_plan
→ retrieve_case_evidence
→ retrieve_regulations
→ extract_fact_candidates
→ detect_missing_facts
→ detect_fact_conflicts
→ human_fact_confirmation
→ select_policy_snapshot
→ evaluate_deterministic_rules
→ draft_findings
→ verify_claim_citations
→ human_review
→ finalize_assessment
```

必须证明：

1. Agent 会先制定 EvidencePlan，而不是直接生成结论；
2. 模型只能规划和选择受限工具，不能提供 workspace/case/actor；
3. Fact Proposal 在 Graph 节点内执行并持久化；
4. 缺失事实、冲突事实与 Reviewer 审批是不同 HITL；
5. 确定性规则和正式审批不能被 Agent 绕过；
6. checkpoint 只保存 ID、计数、状态和摘要；
7. 工具调用、证据计划、候选事实、预算与拒答形成可审计状态和 RunEvent；
8. 最大循环、工具次数和 token budget 可阻止失控执行。

## 2. 当前实现审计

Phase 5 开始前已有：

- LangGraph 原生 SQLite checkpointer；
- `interrupt/Command(resume=...)`；
- Run/Checkpoint/Event 持久化与 revision CAS；
- documents/facts/assessment/review 四类中断；
- Fact Proposal Use Case，含字段白名单、当前文档版本、原文 quote/offset 复核；
- PolicyRuleEngine 与 Assessment 生成/审批；
- Claim-Citation 服务端验证；
- LangChain Tool Calling Copilot。

但核心 Case Assessment Graph 仍有五个缺口：

1. 图节点只校验预先计算好的 `missing_fact_fields`，不制定调查计划；
2. Fact Proposal 只能从 Graph 外部 API 手工调用；
3. 图没有 Typed Tool Registry、工具权限、timeout/retry/stage 约束；
4. 规则评估、Assessment 草拟和引用校验主要由外部 Use Case 代做；
5. 没有工具次数、循环次数、token budget 和 safe refusal 状态。

因此本阶段复用成熟的业务 Use Case 和 Repository，不另造 Multi-Agent，也不把业务对象塞进
LangGraph checkpoint。

## 3. 为什么这样设计

### 3.1 Graph 与业务工具之间为什么使用 Port

`infra/workflows` 不能直接导入 FastAPI 或 SQLAlchemy，也不应直接操作 app 内部字段。新增：

- `EvidencePlannerPort`：模型只返回结构化 EvidencePlan；
- `CaseAssessmentToolPort`：执行受控 Typed Tool；
- `AgentRuntimeContext`：由服务端注入 run/workspace/case/actor。

Graph 只调用 Port。App 层组合真实 Use Case 成 Tool Registry，测试可注入 Fake。

### 3.2 为什么 scope 不进入工具输入 Schema

每个工具输入只包含业务参数，例如 query、field_names、top_k。以下字段不属于模型参数：

```text
run_id
workspace_id
case_id
actor_id
workflow_stage
```

它们由 `AgentRuntimeContext` 注入。Pydantic input model 使用 `extra="forbid"`，模型即使尝试传
`case_id` 也会在服务端验证失败。

### 3.3 为什么 EvidencePlan 是领域模型

EvidencePlan 是 Agent 可解释决策，不是 LangGraph 私有 dict。字段：

- `investigation_questions`；
- `required_fact_fields`；
- `planned_tools`；
- `evidence_gaps`；
- `completion_criteria`。

它可进入 checkpoint 的轻量 JSON，也可展示在 Run Detail。它不包含文档正文、Prompt 或思维链。

### 3.4 为什么 Typed Tool Registry 放在 app

Registry 负责：

- Pydantic 输入/输出校验；
- stage allowlist；
- required role；
- timeout/retry policy 元数据；
- side effect level；
- runtime scope 注入；
- 统一工具调用结果摘要。

具体 executor 复用 `FactManagementUseCase`、`PolicyManagementUseCase`、
`AssessmentManagementUseCase` 和 EvidenceIndex Port。这样：

- domain 只定义契约；
- app 编排业务；
- infra LangGraph 不反向依赖 app；
- 模型不能直接访问 Repository。

### 3.5 为什么 Fact Proposal 在 Graph 内、确认仍由人完成

`extract_fact_candidates` 工具会调用已有 `propose_from_documents()`：

- 模型只能返回字段白名单内候选；
- 引用必须属于本 Case 的当前 ready DocumentVersion；
- quote/offset 重新读取原文；
- 生成 `proposed/conflicting` Fact。

Graph 只记录 candidate fact IDs。`human_fact_confirmation` interrupt 后，恢复时重新查询数据库，
不接受客户端提交完整 Fact 对象。

### 3.6 为什么 Assessment 生成仍由确定性 Use Case 执行

`draft_findings` 节点触发受控 `assessment_generation` interrupt，由
`AssessmentManagementUseCase.generate()`：

- 读取 confirmed Fact version；
- 调用确定性 PolicyRuleEngine；
- 生成 Finding/Action/Citation；
- 校验引用与规则来源；
- 持久化不可变 Assessment version。

Graph 恢复后只接收 `assessment_id`，`verify_claim_citations` 工具重新从 Repository 读取并复核。
Agent 不能自己构造或批准正式 Assessment。

本阶段 `draft_findings` 的名称表示“进入正式 Finding 生成阶段”，不是让 LLM 自由编写正式
结论。Finding 描述、风险等级和整改项仍来自版本化 PolicyRule 的确定性结果。这样先保证
正式产物可复现；若后续增加 LLM 风险解释，只能作为经过 Citation 校验的说明层，不能修改
规则结果、candidate path 或 required actions。

### 3.7 为什么保留单核心 Agent

证据规划、工具选择、缺口/冲突路由共享同一个 Case 上下文和权限。拆成多个自由 Agent 会增加：

- prompt/context 漂移；
- 成本与延迟；
- 责任边界不清；
- 中间越权面。

本阶段仍是单 Case Assessment Agent；Deep Research 保持受限辅助图。

### 3.8 为什么生产 Planner 使用 LangChain function calling

核心 Agent 不能只“用了 LangGraph”，却仍用自定义 SDK 调模型。生产 Profile 使用
`BaseChatModel.with_structured_output(EvidencePlan, method="function_calling")`：

- LangChain 负责标准模型适配和结构化 function calling；
- LangGraph 负责长程状态、条件路由、interrupt/resume；
- 服务端继续校验 planned tools、required fields 和强制门禁；
- 离线测试注入 `DeterministicEvidencePlanner`，不需要 API Key、网络或本地模型。

模型不能省略 `retrieve_regulations`、`evaluate_deterministic_rules` 和
`verify_claim_citations`，也不能扩大或缩小法规计算要求的事实字段。
Planner 输入不包含 `workspace_id`、`case_id` 或文档 ID，只接收 ready 文档数量、规则版本、
必需字段和工具白名单；真实 scope 仅在工具执行时由 Runtime Context 注入。

### 3.9 为什么只让证据检索形成有限循环

Agent 的“自主性”应体现为根据 EvidencePlan 调查问题决定补查，而不是让所有节点任意循环。
`retrieve_case_evidence` 按 `investigation_questions` 一题一轮检索，并受：

- `max_loop_count`；
- `max_tool_calls`；
- `max_tokens`

共同限制。规则计算、引用校验和人工审批仍是单向强制节点。这样可以证明 Agent 会迭代调查，
又不会因自由循环绕过合规门禁。

### 3.10 为什么生产 checkpoint 使用 PostgreSQL

业务数据库切到 PostgreSQL 后，如果 LangGraph 仍写容器本地 SQLite，API 多副本或重启后就
无法可靠恢复。本阶段增加两个 Profile：

```text
STORAGE_BACKEND=sqlite   → SqliteSaver
STORAGE_BACKEND=postgres → PostgresSaver + ConnectionPool
```

Checkpoint Adapter 懒连接，模块 import 和离线测试不访问数据库；FastAPI startup 显式
`initialize()`，shutdown 显式 `close()`。业务 Case/Fact/Assessment 仍由 Repository 保存，
PostgreSQL checkpoint 只表示 Graph 执行位置。

### 3.11 为什么 token 只记录真实 usage metadata

字符数除以四只能用于粗估，不能写入正式成本指标。`EvidencePlannerPort` 返回：

```text
EvidencePlanResult(plan, token_usage)
```

LangChain Planner 从 `AIMessage.usage_metadata.total_tokens` 读取真实值；Fact Proposal 通过
usage-aware `ChatResponse` 返回真实值。Provider 未返回 usage 或使用 Fake/Deterministic
Planner 时记录 0，不伪造 token。

Graph 把剩余 token 预算通过服务端 `AgentRuntimeContext` 注入写工具，Fact Proposal 的
`max_completion_tokens` 取“剩余预算”和全局 `CHAT_MAX_TOKENS` 的较小值。模型返回后，
Use Case 在落库前再次检查实际 token；超限则候选 Fact 不写入。`AgentRun.token_usage` 记录
Planner + Fact Proposal 累计 usage。其他非主线模型调用的统一成本换算仍在 Phase 8 完成。

## 4. 修改文件与实现说明

| 文件 | 为什么改 | 怎么实现 |
| --- | --- | --- |
| `domain/agent_workflow.py` | Agent 计划、工具和预算不能是 LangGraph 私有 dict | 新增 EvidencePlan、EvidencePlanResult、RuntimeContext、ToolDefinition/Result、AgentBudget |
| `domain/models.py`、`domain/facts.py` | 旧 Chat/Fact Proposal 契约只返回内容，无法统计真实 token | 新增 `ChatResponse` 与 `FactProposalResult`，Provider 无 usage 时明确为 0 |
| `domain/ports.py` | domain 不能依赖 LangChain/LangGraph 或 app Registry | 新增 Planner/Tool Port，扩展 Runtime 的角色、冲突、预算和服务端恢复身份参数 |
| `domain/runs.py` | 冲突审核与普通事实确认语义不同 | 新增 `fact_conflict_review` interrupt kind |
| `domain/__init__.py` | 统一对测试和应用导出新领域契约 | 导出 Agent workflow 模型与 Ports |
| `app/agent_tools/registry.py` | 模型工具调用必须做输入、输出、角色和阶段复核 | Pydantic `extra=forbid`、role/stage allowlist、timeout、有限 retry、脱敏摘要 |
| `app/agent_tools/case_assessment.py` | Graph 不能直接访问 Repository | 把 EvidenceSearch、Fact、Policy、Assessment Use Case 包装为五个 Typed Tools |
| `app/agent_tools/__init__.py` | 提供稳定 Registry 装配入口 | 导出 Registry、RegisteredTool 和 builder |
| `infra/agents/evidence_planner.py` | 生产主线需要标准 LangChain Tool Calling | `with_structured_output(..., function_calling)`；服务端复核工具/字段/强制门禁；离线 deterministic profile |
| `infra/chat/openai_chat.py`、`infra/qa/fact_proposals.py` | 图内 Fact Proposal 也是模型调用，必须进入同一预算 | Chat Adapter 读取 usage metadata；Fact Proposal 下推剩余输出上限并返回 usage |
| `infra/agents/__init__.py` | 暴露两种 Planner Adapter | 导出 LangChain 与 Deterministic Planner |
| `infra/workflows/case_assessment_graph.py` | 旧 9 节点只是顺序状态机，不能证明 Agent 决策 | 实现 16 节点图、按调查问题有限循环、五工具调用、三类 HITL、safe refusal 与预算 |
| `infra/workflows/checkpoint_store.py` | 生产多实例不能依赖本地 SQLite checkpoint | 懒加载 SQLite/PostgreSQL Saver；PostgreSQL 使用官方 ConnectionPool；关闭时释放资源 |
| `infra/workflows/langgraph_runtime.py` | Runtime 需要框架适配、恢复参数白名单和安全状态输出 | 懒编译 Graph；start/resume/inspect；服务端身份注入；轻量 state allowlist；两种 checkpoint profile |
| `app/use_cases/assessment_runs.py` | Graph 状态必须与业务数据库和 Run/Event 对账 | 注入真实角色/required fields；恢复时重读 DB；冲突 Reviewer 门禁；Plan/Tool/Fact 事件；幂等 continue；token 落 Run |
| `app/use_cases/assessment_management.py` | 引用验证不能只在内部生成路径使用 | 暴露 `verify_references()`，复用严格 Fact/Document/Clause 漂移校验 |
| `app/container.py` | Runtime 早于业务 Use Case 构造，无法注入真实工具 | 后移 Runtime 装配；先构造 Use Case，再构造 Registry、Planner、Runtime；Planner 与 Copilot 复用 BaseChatModel |
| `app/factories.py` | production/local profile 需要统一组合根 | 按配置构造 Planner、预算与 SQLite/PostgreSQL checkpoint Runtime |
| `config.py` | 预算和 Planner Profile 不能写死 | 新增 `AGENT_PLANNER_BACKEND` 与 loop/tool/token 上限 |
| `.env.example` | 面试者需要知道可配置边界 | 增加 Agent Planner 与预算示例 |
| `requirements.txt` | 生产 checkpoint 需要官方 PostgreSQL Adapter | 增加 `langgraph-checkpoint-postgres` |
| `main.py` | checkpoint 连接必须启动期失败、关闭期释放 | lifespan 调用 Runtime `initialize/close`，关闭异常只记录 |
| `api/v3/schemas.py` | Plan 需要稳定公开 Schema | 新增 `EvidencePlanOut` |
| `api/v3/assessment_runs.py` | Run Detail 不能读取 LangGraph 私有状态 | 新增 `GET /runs/{run_id}/plan`；未生成返回 409 |
| `tests/domain/test_agent_workflow.py` | 验证计划唯一性和预算 fail-closed | 覆盖 Pydantic 不变量与 loop/tool/token budget |
| `tests/app/test_typed_tool_registry.py` | 验证 scope 不能由模型提供 | 覆盖 scope 注入、非法 extra、role/stage、retry、timeout |
| `tests/app/test_fact_management.py`、`tests/infra/test_fact_proposals.py` | 验证写工具超预算不会留下副作用 | 覆盖 usage 透传、Provider token 上限、写前拒绝和零落库 |
| `tests/infra/test_evidence_planner.py` | 证明真正使用 LangChain function calling | 覆盖 bound tools、真实 usage、越权工具/字段和强制门禁 |
| `tests/infra/test_checkpoint_store.py` | 外部数据库不能进入普通 CI | 覆盖懒连接和 DSN 规范化；真实 DB 由显式 contract 验收 |
| `tests/infra/test_langgraph_runtime.py` | 旧测试只覆盖 9 节点 | 更新 16 节点、HITL、恢复、安全 state、tool/token budget |
| `tests/app/test_assessment_runs.py` | 验证产品 Run 与 Graph 对账 | 更新阶段名并验证无状态变化的 continue 不增 revision |
| `tests/api/conftest.py` | API 测试过去绕过真实 Container Registry | 注入 Deterministic Planner，但让 Container 构造真实 Typed Tools/Runtime |
| `tests/api/test_v3_assessment_runs.py` | 需要端到端证明图内候选、冲突、工具轨迹 | 覆盖 Plan API、五工具、scope 脱敏、Fact Proposal、Reviewer 冲突恢复 |
| `scripts/phase5_postgres_checkpoint_contract.py` | 普通 pytest 不应依赖数据库，但生产恢复必须有证据 | 显式环境开关；Runtime A 写中断并关闭，Runtime B 重建后恢复 |
| `docs/guides/v3-assessment-run.md` | 旧指南仍描述 9 节点和图外 Fact Proposal | 更新 16 节点、Plan API、图内候选、冲突 HITL 和双 checkpoint profile |
| `docs/design/riskpilot-v2.md` | 设计文档的“当前实现”仍停留在旧 9 节点骨架 | 更新为 16 节点、LangChain Planner、Typed Tools 和双 checkpoint profile |
| `docs/roadmap/autumn-recruitment-production-plan.md` | 路线状态必须与真实实现同步 | Phase 0～5 完成，下一阶段切到 Phase 6 |
| `docs/implementation/phase-05-core-assessment-agent.md` | 每项改动要可复习、可解释 | 本文记录设计、实现、命令、证据和风险 |

## 5. 数据模型变化

### 5.1 不新增业务表

EvidencePlan、预算和工具摘要进入已有 RunCheckpoint/RunEvent JSON；Fact、Assessment 仍使用
既有业务表。这样避免把 LangGraph state 误当业务数据库。

### 5.2 新增轻量领域模型

- `EvidencePlan`：调查问题、必需字段、计划工具、证据缺口、完成标准；
- `EvidencePlanResult`：Plan + Provider 返回的真实 token；
- `AgentRuntimeContext`：服务端 run/workspace/case/actor/role/stage；
- `ToolDefinition`：Schema、timeout、retry、role、stage、side effect；
- `ToolExecutionResult`：脱敏参数、结构化输出、摘要、耗时、重试；
- `AgentBudget`：loop/tool/token 上限与当前计数。
- `ChatResponse` / `FactProposalResult`：内容或候选 + Provider 真实 token usage。

### 5.3 Checkpoint State allowlist

只允许保存：

- run/case/workspace/actor ID 与角色；
- ruleset、Document ID、Fact ID、Evidence ID、Assessment ID；
- EvidencePlan；
- missing/conflict 字段名；
- budget；
- 工具的脱敏参数和 ID/计数/状态型输出；
- interrupt/review 状态。

禁止正文、原始 Prompt、Authorization、密钥、Cookie 和 Chain of Thought。

## 6. API 变化

保持已有 Run API 兼容，新增：

- `GET /api/v3/runs/{run_id}/plan`；
- RunEvent 中的结构化工具事件与证据/Fact IDs；
- `continue` 仍只触发服务端重读，不接收完整业务对象。

行为变化：

- Plan 尚未生成时返回 409；
- 无状态变化的 `continue` 幂等返回，不增加 revision、不重复写事件；
- 冲突中断为 `waiting_for_review/detect_fact_conflicts`；
- Editor 不能恢复冲突；Reviewer/Admin 处理数据库 Fact 后再恢复；
- 正式 Assessment 审批仍只能走 `/review`，普通 continue 不会绕过审批。

## 7. Agent 状态变化

### 7.1 16 节点状态机

```text
load_case → authorize → inspect_documents → build_evidence_plan
→ retrieve_case_evidence ↺（按调查问题、受预算限制）
→ retrieve_regulations → extract_fact_candidates → detect_missing_facts
→ detect_fact_conflicts → human_fact_confirmation → select_policy_snapshot
→ evaluate_deterministic_rules → draft_findings → verify_claim_citations
→ human_review → finalize_assessment
```

### 7.2 中断协议

| interrupt | 当前阶段 | 恢复条件 |
| --- | --- | --- |
| `documents_required` | `inspect_documents` | 服务端重读后至少一个 ready 且无 pending |
| `fact_confirmation` | `human_fact_confirmation` | 服务端重读后 missing fields 为空 |
| `fact_conflict_review` | `detect_fact_conflicts` | Reviewer/Admin 处理后 conflicting fields 为空 |
| `assessment_generation` | `draft_findings` | Use Case 生成/复用 Assessment，只回传 assessment_id |
| `assessment_review` | `human_review` | Reviewer/Admin 通过专用审批 API 决策 |

恢复参数和 state update 都有字段白名单；客户端不能提交完整 Document、Fact、Assessment 或 scope。

### 7.3 工具

| 工具 | 副作用 | 关键边界 |
| --- | --- | --- |
| `retrieve_case_evidence` | read_only | Workspace/Case 由 Context 注入 |
| `retrieve_regulations` | read_only | 只读取当前 Workspace published rules |
| `extract_fact_candidates` | reversible_write | retry=0，防止重复写；候选仍需人工确认 |
| `evaluate_deterministic_rules` | read_only | 必须调用 PolicyRuleEngine |
| `verify_claim_citations` | read_only | 重新读取 Assessment、Fact、Document 和规则快照 |

### 7.4 预算

- loop 只计算证据补查轮次，不把固定业务节点误算为循环；
- 每次工具调用先消费 tool budget；
- Planner 和 Fact Proposal token 来自真实 usage metadata；
- Fact Proposal 超出剩余预算时在候选 Fact 写入前拒绝；
- 任一预算耗尽都 fail closed。

## 8. 验收门禁

- [x] 完整材料自动运行到 Reviewer；
- [x] 缺失关键事实自动提议候选并暂停确认；
- [x] 冲突事实进入 Reviewer 处理，不静默覆盖；
- [x] 恢复时重新读取数据库 Fact/Document/Assessment；
- [x] 模型不能提供 workspace/case/actor；
- [x] Agent 不能绕过 PolicyRuleEngine；
- [x] Agent 不能批准 Assessment；
- [x] 无候选事实时 safe refusal；
- [x] 最大 loop/tool/token budget 生效；
- [x] 工具输入输出均通过 Pydantic；
- [x] Tool stage/role/side-effect policy 生效；
- [x] checkpoint 不含正文、Prompt、密钥或思维链；
- [x] SQLite/PostgreSQL checkpoint 都支持 Runtime 重建恢复；
- [x] RunEvent 可还原节点与工具轨迹；
- [x] 默认测试零模型 API；
- [x] 最终全量测试通过。

## 9. 测试结果

### 9.1 Phase 5 聚焦测试

Agent Graph 聚焦结果：

```text
76 passed, 5 warnings in 5.68s
```

usage-aware Chat/Fact Proposal/Graph/API 追加聚焦：

```text
64 passed, 5 warnings in 3.13s
```

静态检查：

```text
Ruff: All checks passed
mypy: Success: no issues found in 143 source files
```

### 9.2 真实 PostgreSQL checkpoint

实际启动 `pgvector/pgvector:pg17`，使用官方 `PostgresSaver`：

```text
Runtime A: interrupted inspect_documents documents_required
Runtime B inspect: interrupted inspect_documents documents_required
Runtime B resume: interrupted human_fact_confirmation fact_confirmation
```

数据库实际存在：

```text
checkpoints=11
checkpoint_writes=40
checkpoint_blobs=18
```

显式 contract 二次执行：

```text
thread_id=phase5-contract-f0ebce74
first_stage=inspect_documents
resumed_stage=human_fact_confirmation
tool_calls=3
```

命令：

```bash
RUN_PHASE5_CONTRACT=1 \
STORAGE_BACKEND=postgres \
VECTOR_BACKEND=pgvector \
DATABASE_URL='postgresql+psycopg://...' \
python scripts/phase5_postgres_checkpoint_contract.py
```

验收后已删除临时 PostgreSQL 容器并停止 Colima。

### 9.3 最终全量

```text
$ PATH="$PWD/.venv/bin:$PATH" make ci
Ruff: All checks passed
Format: 397 files already formatted
mypy: Success: no issues found in 143 source files
pytest: 1291 passed, 4 skipped, 5 warnings in 21.44s
```

四项 skip 均为显式外部环境门禁：

1. 真实 PostgreSQL Alembic schema；
2. 真实 MinIO；
3. 真实 PostgreSQL 并发约束；
4. `RUN_LIVE=1` 模型效果测试。

本阶段已另外通过真实 PostgreSQL checkpoint contract，因此不是用普通 pytest 的 skip 代替
生产恢复验收。

## 10. 尚未解决的风险

1. `ThreadPoolExecutor` timeout 无法强杀已经进入 C 扩展或阻塞系统调用的线程；当前会停止等待
   并 fail closed，但真正的执行隔离需在 Phase 6/8 评估进程/任务队列边界；
2. `extract_fact_candidates` 是写工具，已设置 `max_retries=0`；如果进程在业务提交后、工具结果
   写入 checkpoint 前崩溃，人工 retry 前应先对账已有候选，后续可增加业务幂等键；
3. 当前循环只用于按调查问题补查案件证据，没有做任意反思循环；这是有意的安全边界，不应
   在面试中虚称“无限自主研究”；
4. AgentRun 已累计 Planner 与 Fact Proposal 的真实 token usage；货币成本换算、其他辅助
   模型调用与全链路 OTel 指标统一留 Phase 8；
5. 正式 Finding/整改项目前由规则结果确定性生成，尚未增加独立的 LLM 自然语言解释层；这是
   为了不让模型修改法规门槛，后续若实现必须经过结构化 Schema 和 Claim-Citation 复核；
6. Tool Policy 的 `privileged_write/forbidden_for_agent` 全局矩阵、Prompt Injection、SSRF、
   Trace 敏感字段专项攻击测试属于 Phase 6；
7. 当前工具 trace 输出只允许 ID/计数/状态；后续新增工具必须继续遵守该约束，不能把正文放进
   checkpoint/event。

## 11. 下一阶段

Phase 5 全部门禁通过，可以进入 Phase 6：Tool Policy、Prompt Injection、SSRF、跨
Workspace/Case、Citation 伪造、非法 MIME/大文件/ZIP Bomb 和敏感信息 Trace 测试。
