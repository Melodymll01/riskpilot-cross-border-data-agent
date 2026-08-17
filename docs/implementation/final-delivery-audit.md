# RiskPilot Phase 0～10 最终交付审计

- 状态：核心 20 项交付物已完成；P0/P1 闭环通过；P2 与真实模型效果验证明确保留
- 日期：2026-08-17
- 审计基线：`50272310957467bb21eee8d7ca525e4b3ac71c6c`
- Phase 10 提交：`d708b09e650be264757f002d828c14c0f29085d1`
- 审计方式：仓库静态证据 + 离线 CI + 安全专项回归 + 真实 Docker Compose 运行态

## 1. 为什么需要最终审计

Phase 0～10 各自通过，只能证明阶段内门禁成立，不能自动证明：

1. 原始 20 项最终交付物都能在当前仓库找到；
2. 后续阶段没有破坏前面阶段的架构边界；
3. README 的指标仍与当前真实执行结果一致；
4. Docker、数据库、Worker、对象存储和 Run Detail 能在同一 production profile 下协同；
5. 已完成能力和 P2/真实模型待办没有被混写；
6. 面试话术能够从代码、测试和运行结果反向验证。

因此本审计不新增业务范围，只做三件事：

- 从原始目标反查制品；
- 重新执行关键门禁；
- 把“已完成、部分覆盖、明确未做”写成可复习的事实。

## 2. 审计结论

### 2.1 可以确认完成

- Phase 0～10 共 11 份实施复盘全部标记完成，无未勾选验收项；
- 原始最终交付清单 20 项均有仓库制品和测试或运行证据；
- P0 与 P1 的核心产品闭环已经形成：
  Case → Document → Celery Processing → Evidence Plan → Typed Tools → Fact/HITL →
  Deterministic Rule → Claim-Citation → Reviewer → immutable Assessment；
- `domain/` 没有导入 FastAPI、SQLAlchemy、Celery、Redis、LangGraph、LangChain、
  Chroma、boto3 或 psycopg；
- PostgreSQL 是业务 SSOT，LangGraph checkpoint 只保存轻量执行状态；
- 默认 CI 零密钥、零网络、零模型下载、零模型费用；
- 真实 Compose production profile 当前可运行且健康。

### 2.2 不能宣称完成

以下项目没有被包装成“已完成”：

- 公网演示环境；
- Grafana Dashboard 和 Prometheus 长期存储；
- 完整企业 IAM；
- MCP Server；
- Outbox Pattern；
- 真实企业数据分布上的法规判断准确率；
- 完整真实模型端到端 Eval；
- 生产流量 P95；
- 未配置价格时的真实模型成本；
- 独立 Assessment PDF/Word 报告导出产品流程。

这些项目不影响当前 P0/P1 秋招闭环，但面试和简历中必须继续遵守真实性边界。

## 3. 最终 20 项交付物逐项核对

| # | 交付物 | 状态 | 主要证据 | 为什么这样判断 |
| ---: | --- | :---: | --- | --- |
| 1 | 可运行代码 | 完成 | `main.py`、`app/`、`domain/`、`infra/`、`api/` | 离线 CI 与真实 Compose 均通过 |
| 2 | Docker Compose | 完成 | `docker-compose.yml`、`Dockerfile`、`scripts/compose.sh` | API/Worker/PG/Redis/MinIO 可共同启动 |
| 3 | PostgreSQL/Alembic | 完成 | `migrations/`、`alembic.ini`、SQLAlchemy Adapters | 当前 migration 为 `c312b95fd8a1 (head)` |
| 4 | Redis/Celery Worker | 完成 | `infra/tasks/`、`ProcessingJob`、worker service | Worker 独立容器健康，Job 支持 retry/cancel/CAS |
| 5 | pgvector | 完成 | `infra/storage/sqlalchemy/evidence_index.py`、迁移 | 容器扩展版本为 `0.8.6` |
| 6 | MinIO | 完成 | `S3ObjectStore`、MinIO/MinIO-init services | API 与 Worker 共享同一 object key |
| 7 | 核心 LangGraph | 完成 | `infra/workflows/case_assessment_graph.py` | 16 个受控节点，支持 interrupt/resume |
| 8 | Agent Tool Registry | 完成 | `app/agent_tools/registry.py`、`case_assessment.py` | 5 个 Typed Tool 均有输入/输出/角色/阶段策略 |
| 9 | Human-in-the-loop | 完成 | Fact、Conflict、Reviewer interrupts | Demo B 与恢复测试证明 checkpoint 恢复 |
| 10 | Agent Eval | 完成 | `evaluations/agent_runs/` | 39 Case、13 类场景、离线 Gate PASS |
| 11 | OpenTelemetry/Metrics | 完成 | `infra/observability/`、`observability_context.py` | HTTP→Agent→Node→Tool→Celery 可关联 |
| 12 | 安全测试 | 完成 | `tests/security/`、V3 QA/Run 安全测试 | 安全聚焦回归 `49 passed` |
| 13 | Run Detail 页面 | 完成 | `GET /api/v3/runs/{run_id}/detail`、`frontend/cases.js` | 一次聚合请求展示结构化轨迹且不展示思维链 |
| 14 | Seed Demo | 完成 | `scripts/seed_demo.py` | 三类合成、脱敏、幂等 Demo |
| 15 | README | 完成 | `README.md` | 首屏定位、闭环、架构、真实指标和 Quick Start 已收敛 |
| 16 | 架构文档 | 完成 | `docs/architecture/overview.md`、路线图 Mermaid | 当前/目标架构和职责边界均有图与文字 |
| 17 | ADR | 完成 | `docs/decisions/ADR-017`～`ADR-021` | 覆盖 Graph/Celery、LLM/Rule、DDD、pgvector、Multi-Agent |
| 18 | 真实测试/评测报告 | 完成 | `make ci`、`evaluations/agent_runs/reports/latest.*` | 报告包含 dataset/model/prompt/tool/evaluator 版本 |
| 19 | 2～3 分钟演示脚本 | 完成 | `docs/guides/interview-demo-script.md` | Demo A/B/C、命令、话术和常见追问齐全 |
| 20 | 简历项目描述 | 完成 | `docs/guides/resume-project-description.md` | AI 应用岗、后端岗、精简版和真实性边界齐全 |

## 4. Phase 0～10 证据链

| Phase | 提交 | 核心证据 |
| ---: | --- | --- |
| 0 | `6f45b05` | 路线图、当前/目标架构、5 份关键 ADR、非目标 |
| 1 | `4c9444f` | 零密钥测试、全量 Ruff/format/mypy、live/ready |
| 2 | `2671935`、`9660a6e` | PostgreSQL Repository、Alembic、乐观锁、并发 Run 唯一 |
| 3 | `9e27676` | pgvector/FTS、S3ObjectStore、MinIO、范围过滤下推 |
| 4 | `b409380` | Redis/Celery、ProcessingJob、幂等重试和取消 |
| 5 | `e40295e` | EvidencePlan、Typed Tools、16 节点 Graph、HITL |
| 6 | `d7ec00e` | Tool Policy、SSRF/注入/跨租户/文件安全 |
| 7 | `83868de` | 39 Case 轨迹评测和离线门禁 |
| 8 | `5118754` | JSON Log、OTel、Prometheus、token/cost |
| 9 | `03189be` | 同镜像 API/Worker、Seed、Compose smoke |
| 10 | `d708b09` | Run Detail、三 Demo 入口、面试脚本、简历描述 |

从基线到 Phase 10 的 12 个阶段提交均包含且只包含一次：

```text
Co-authored-by: TRAE CLI <noreply@bytedance.com>
```

## 5. 架构不变量审计

### 5.1 DDD 与 Port/Adapter

执行：

```bash
rg -n '(^| )(from|import) (fastapi|sqlalchemy|celery|redis|langgraph|langchain|chromadb|boto3|psycopg)' domain
```

结果为空。说明领域层没有框架和基础设施依赖。

AST 统计：

```text
Port Protocol: 46
Use Case: 17
```

这两个数字与 README 一致，不是人工估算。

### 5.2 LangGraph 与 Celery

LangGraph 的 16 个节点是：

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

Celery 处理 Document ProcessingJob；Graph 处理案件决策、中断和恢复。两者没有互相替代。

### 5.3 LLM 与确定性规则

- Evidence Planner、Fact Proposal 和自然语言解释可以使用模型；
- `PolicyRuleEngine` 只消费 confirmed facts；
- 模型不能发布规则、批准 Assessment 或修改 Workspace scope；
- Claim-Citation 在服务端重新读取原文和版本快照；
- Run Detail 的按钮能力由服务端角色、Reviewer assignment 和 Run 状态计算。

### 5.4 Checkpoint 与业务数据库

- checkpoint 保存 ID、预算、EvidencePlan、Tool/Node trace 等轻量状态；
- Case、Document、Fact、Policy、Assessment、AgentRun/Event 由 Repository 持久化；
- resume 只接受动作字段，业务对象重新从数据库读取；
- `node_trace` 单项只允许 `stage/status/duration_ms`。

## 6. 最终命令证据

### 6.1 全量离线 CI

```bash
PATH="$PWD/.venv/bin:$PATH" make ci
```

结果：

```text
Ruff: All checks passed
Format: 433 files already formatted
mypy: Success: no issues found in 151 source files
pytest: 1357 passed, 4 skipped, 5 warnings in 32.96s
Offline Agent Eval: 39 cases / 13 categories / PASS
```

4 个 skip 均有明确原因：

- 真实 PostgreSQL migration contract 需要 `TEST_POSTGRES_URL`；
- 真实 MinIO contract 需要 `TEST_S3_*`；
- 真实 PostgreSQL 并发约束需要 `TEST_POSTGRES_URL`；
- live RAG 需要显式 `RUN_LIVE=1`。

GitHub Actions 另有 PostgreSQL job，使用 `pgvector/pgvector:pg17` 执行 migration、
schema drift 和 Repository contracts。

### 6.2 安全专项

```bash
pytest -q \
  tests/security \
  tests/app/test_typed_tool_registry.py \
  tests/api/test_v3_qa.py \
  tests/api/test_v3_assessment_runs.py
```

结果：

```text
49 passed, 5 warnings in 3.01s
```

### 6.3 Compose smoke

```bash
make docker-smoke
```

结果：

```text
demo_cases=3
app_health=healthy
worker_health=healthy
evidence_chunks=3
agent_runs=2
compose_smoke=PASS
```

运行态附加证据：

```text
liveness = ok
readiness.database = true
readiness.redis = true
Alembic = c312b95fd8a1 (head)
pgvector = 0.8.6
Prometheus riskpilot-api = up
Prometheus riskpilot-worker = up
```

### 6.4 固定 Demo

```text
Demo A:
  status=waiting_for_review
  stage=human_review
  timeline=22
  tool_calls=4
  assessment=review_required
  citation_valid=true

Demo B:
  status=waiting_for_user
  stage=human_fact_confirmation
  interrupt=fact_confirmation
  missing_fact_fields=[important_data_involved]
  timeline=16
```

Demo C 由 ProcessingJob 展示 Worker failure/retry，不伪装成 Agent Run。

## 7. Agent Eval 解释边界

数据集：

```text
39 cases
13 categories
```

类别包括：

- complete/missing materials；
- missing facts；
- fact conflict；
- citation drift；
- regulation version；
- tool failure；
- invalid schema；
- prompt injection；
- cross workspace；
- reviewer rejection；
- worker retry；
- run recovery。

Offline Eval 的价值是验证状态机、工具选择、参数约束、安全门禁和恢复协议。它不能证明：

- 真实 LLM 在企业材料上的抽取准确率；
- 真实企业案件分布；
- 生产环境延迟；
- 真实模型费用。

因此 README 只引用稳定的 Gate 指标，不把本机短 Case 的 latency 写成生产 P95。

## 8. 本次审计发现并修复的问题

### 8.1 README 漏列 Run Detail Endpoint

问题：

- README 已宣传 Run Detail 和 49 个 V3 operation；
- 但 V3 API 表没有列出 `GET /runs/{run_id}/detail`；
- 面试官能看到页面，却不容易快速找到聚合接口。

修复：

- 在 V3 API 表增加该 endpoint；
- 明确它返回安全时间线、Tool、HITL、规则、Citation 和 Assessment；
- 不改变 API 或业务行为。

为什么只改文档：

- OpenAPI 已有该路由；
- API、前端和测试已经在 Phase 10 提交；
- 缺口是可发现性，不是实现缺失。

### 8.2 README 首屏辅助能力过多

问题：

- 首屏同时平铺 Copilot、Research、Memory、Visual、Risk Profile、Evidence QA 等近 20 项能力；
- 用户需要在短时间内自己推断哪个才是主产品；
- 这与“Case Assessment 是唯一主线，其他模块是辅助能力”的路线约束不一致；
- 旧截图也更偏 Copilot/知识库，没有直接证明新案件 Agent 闭环。

修复：

- 首句改为证据驱动案件智能体；
- 第二段直接回答“与普通 RAG 有什么不同”；
- 用一条文本链路展示 Case Assessment 完整业务闭环；
- 首屏只保留三个 Agent 亮点和三个后端亮点；
- 把 Demo A/B/C 与四条启动命令前置；
- 辅助模块的详细说明继续保留在 README 后半部分，没有删除能力。

为什么这样设计：

- 秋招 README 首屏的目标是让面试官在 30 秒内建立正确心智模型，不是列技术清单；
- 六个亮点分别证明 Agent 决策/HITL/确定性门禁和 DDD/异步任务/可观测部署；
- 辅助模块仍可用于深入追问，但不会稀释 Case Assessment 主线。

## 9. 遗留风险和建议顺序

### P0/P1 之后仍值得做

1. 使用脱敏案件和固定预算运行完整真实模型 Eval；
2. 给真实模型记录 model/prompt/tool schema/dataset 版本和实际 cost；
3. 将 Run Detail 增加浏览器级 E2E，而不是只做 DOM/renderer contract；
4. 设计正式 Assessment 导出对象后，再增加 Celery 报告导出任务；
5. 用真实 OCR 图片集和中文检索数据集调 HNSW/BM25 参数；
6. 对统一镜像做 OCR/Visual optional extras，降低当前约 736MB 体积。

### P2

1. 公网部署；
2. Grafana Dashboard 与长期指标存储；
3. MCP Server；
4. Outbox Pattern；
5. 更完整的企业 IAM；
6. 更完整的前端组件/E2E 测试。

建议顺序仍是：真实模型 Eval → Run Detail E2E → 公网只读 Demo。不要先做 MCP 或 UI 动画。

## 10. 最终验收判断

### 满足

- 原始最终交付物 1～20；
- P0 全部；
- P1 全部；
- Case Assessment 唯一主线；
- 默认零密钥 CI；
- 可重复 Docker Demo；
- 可解释 Agent/HITL/Rule/Citation/Reviewer 闭环；
- 每个 Phase 均有“为什么、怎么实现、测试证据、风险”复盘。

### 有条件满足

- “接近真实生产系统”：架构、事务、Worker、对象存储、观测和部署形态满足；
  但默认口令、本地 Demo 登录和单机 Compose 只适合生产模拟；
- “真实指标”：离线协议和 Compose 指标真实；真实模型效果、生产 P95 和公网规模没有数据；
- “一键启动”：当前已在本机 Docker/Colima 真实通过；跨操作系统仍依赖 Docker
  对镜像架构和网络的正常支持。

### 最终结论

RiskPilot 已形成适合 2026 秋招展示的生产化 AI Agent 项目闭环。当前最重要的工作不再是
增加框架或功能数量，而是熟练讲解已有设计、录制固定 Demo，并在有脱敏数据和预算时补一次
真实模型 Eval。
