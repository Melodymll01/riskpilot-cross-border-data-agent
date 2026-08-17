# RiskPilot 简历描述与面试展开

## 1. 项目名称

**RiskPilot 数据出境合规案件智能体**

## 2. 一句话描述

面向企业数据出境合规场景的证据驱动案件智能体，基于 LangChain Tool Calling 与
LangGraph 构建可暂停、可恢复、可审计的案件评估闭环，并使用 FastAPI、PostgreSQL、
pgvector、Redis/Celery 和 MinIO 实现生产化后端。

## 3. 简历三条版本

### 版本 A：AI 应用开发岗

- 设计并实现证据驱动的 Case Assessment Agent：通过 LangChain structured tool calling
  生成 Evidence Plan，使用 LangGraph 编排 16 个受控节点，支持事实缺失/冲突检测、
  Human-in-the-loop、PostgreSQL checkpoint 恢复和最大 token/tool/loop 预算。
- 构建 Typed Tool Registry 与 Tool Policy，服务端注入 Workspace/Case/Actor scope，
  使用 Pydantic 校验模型输入输出；将法规阈值、权限、状态机、Citation 完整性和审批保留
  在确定性代码中，防止 Prompt Injection、越权调用和 Agent 自动审批。
- 建立 39 个版本化 Agent 轨迹评测 Case，覆盖缺失材料、事实冲突、Citation 漂移、
  工具失败、非法 Schema、跨租户、Reviewer 拒绝与 checkpoint 恢复；离线协议评测
  13 类场景全部门禁通过。

### 版本 B：Python 后端 / AI 平台岗

- 基于 DDD + Port/Adapter 重构合规案件后端，保持 `api → app → domain` 与
  `infra → domain ports`；使用 SQLAlchemy 2.x、Alembic 和 PostgreSQL 实现 Case、
  Document、Fact、Policy、Assessment、AgentRun/Event 持久化、事务与乐观锁。
- 使用 Redis/Celery 将文档解析、OCR、切块、Embedding 和 pgvector 索引移出 API，
  实现指数退避、超时、幂等重放、failed Job retry 和独立 Worker；MinIO 共享原始材料，
  API/Worker 可独立重启且 named volume 数据不丢失。
- 接入 JSON Log、OpenTelemetry、Prometheus 和可选脱敏 LangSmith，打通
  HTTP → Agent → Graph Node → Tool → Celery Trace；统计 latency、retry、token、显式
  cost、refusal 与 Citation failure，业务 ID 使用 HMAC，Trace 不含正文/Prompt/密钥。

### 版本 C：一条精简版

> 基于 LangChain + LangGraph 实现数据出境合规案件 Agent，支持 Typed Tool Calling、
> Evidence Plan、HITL、checkpoint 恢复、确定性规则与 Claim-Citation 校验；使用
> FastAPI/PostgreSQL/pgvector/Redis/Celery/MinIO 完成异步处理、租户隔离、可观测性和
> Docker Compose 一键部署，当前离线回归 1357 passed、39 个 Agent Eval Case 全部门禁通过。

## 4. 真实性边界

简历可以写：

- 1357 个离线测试通过；
- 39 个 Agent Eval Case / 13 类场景；
- Offline 协议指标和安全门禁；
- Docker Compose 真实启动、Seed、retry、重启和 Prometheus target；
- deterministic Demo 的工程协议。

简历不能写：

- 未执行的真实模型准确率；
- 未采样的生产 P95；
- 未发生的企业落地规模；
- 未配置价格时的“零成本”；
- 合成数据代表真实法规判断准确率；
- Agent 可以自动审批正式报告。

## 5. 项目亮点展开顺序

面试回答建议按以下顺序，不要从技术名词堆砌开始：

1. **业务闭环**：Case → Document → Evidence Plan → Tool → Fact → HITL → Rule →
   Citation → Reviewer → immutable Assessment；
2. **Agent 行为**：根据材料和事实状态决定继续、补查、暂停或拒答；
3. **确定性边界**：规则阈值、权限、审批和引用完整性不交给 LLM；
4. **状态与任务分离**：LangGraph vs Celery；
5. **业务与 checkpoint 分离**：PostgreSQL Repository vs LangGraph saver；
6. **安全**：scope 注入、Tool Policy、SSRF、Prompt Injection、跨租户；
7. **评测**：完整轨迹，而非只看最终回答；
8. **可观测性**：run_id、node/tool duration、token/cost、Worker retry；
9. **部署**：同镜像多命令、migration、health、named volume、Seed Demo。

## 6. 面试深挖问题

### Agent 在哪里？

`infra/workflows/case_assessment_graph.py` 是核心 Agent 工作流；`EvidencePlan`、
`TypedToolRegistry`、Tool Policy、interrupt/resume 和 AgentBudget 共同形成真实 Agent 行为。

### 与普通 RAG 有什么不同？

普通 RAG 通常是“检索后回答”。RiskPilot 会规划调查问题、跨案件材料与法规工具收集证据、
生成候选事实、检测缺失/冲突、暂停等待人、调用确定性规则、验证 Claim-Citation，最后等待
Reviewer。

### 最难的工程问题是什么？

真实 Docker 验收发现了本机测试看不到的问题：

- PyJWT 只在 dev requirements；
- PostgreSQL DateTime 微秒精度导致 AgentRun 乐观锁误判；
- RapidOCR Python 包存在但缺 OpenCV 动态库；
- API/Worker Compose Profile 原来不共享数据库和对象存储。

这些问题通过真实容器、Repository contract 和 smoke 门禁闭环，而不是只修改配置文件。

### 如何保证恢复后不信任客户端？

resume 只接收允许的动作字段，Use Case 会重新读取 Document、Fact、Policy 和 Assessment
数据库状态；客户端不能提交完整业务对象，checkpoint 也只保存轻量 ID/状态。

## 7. 技术栈

```text
Python 3.12 / FastAPI / Pydantic v2
LangChain / LangGraph / LangSmith(optional)
PostgreSQL / SQLAlchemy 2.x / Alembic / pgvector
Redis / Celery / MinIO
OpenTelemetry / Prometheus / JSON Logging
Docker Compose / GitHub Actions
```
