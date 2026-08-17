# RiskPilot 2～3 分钟面试演示脚本

## 0. 演示前准备

```bash
make docker-build
make docker-up
make seed-demo
make docker-smoke
```

打开：

- 应用：<http://127.0.0.1:8001>
- Prometheus（可选）：`make docker-observability` 后访问 <http://127.0.0.1:9090>

本地 Demo 登录：

```bash
curl -c /tmp/riskpilot.cookies \
  -X POST http://127.0.0.1:8001/api/v2/auth/demo
```

浏览器若未登录，可先在控制台或 API 客户端调用同一路由；该路由默认关闭，只在 Compose
本地演示 Profile 显式开启。

## 1. 0:00～0:20：一句话定位

推荐话术：

> RiskPilot 是面向企业数据出境合规的证据驱动案件智能体。它不是普通 RAG，而是围绕
> 一个 Case 自主制定证据计划、调用受控工具、识别材料缺口和事实冲突，在关键节点进入
> Human-in-the-loop，最后由确定性规则引擎计算合规路径并生成带不可变引用的 Assessment。

同时指出：

- LangGraph 负责状态、路由和恢复；
- Celery 负责 OCR、切块、Embedding 和索引；
- PostgreSQL 是业务 SSOT，checkpoint 不是业务数据库；
- Agent 永远不能自动批准正式 Assessment。

## 2. 0:20～1:00：Demo A · Happy Path

点击：

```text
案件工作台 → A · Happy Path
```

页面重点：

1. Run 状态是 `waiting_for_review`；
2. 当前节点是 `human_review`；
3. Evidence Plan 展示调查问题、必需事实、计划工具和完成标准；
4. 节点时间线展示真实执行过的节点和耗时；
5. Tool 卡片展示：
   - `retrieve_case_evidence`；
   - `retrieve_regulations`；
   - `evaluate_deterministic_rules`；
   - `verify_claim_citations`；
6. 规则结果显示候选路径；
7. Citation 校验显示 valid；
8. 最终 Assessment 显示风险、Finding 和 Citation 数量；
9. Editor 看不到 Reviewer 操作能力；切到 Reviewer/Admin 才可审批。

推荐话术：

> 这里最重要的不是节点数量，而是职责边界。模型做计划和解释，规则阈值由确定性代码
> 计算，Citation 由服务端重新读取原文验证，正式结果必须等待 Reviewer。

## 3. 1:00～1:35：Demo B · Human-in-the-loop

点击：

```text
B · HITL
```

页面重点：

1. Run 状态 `waiting_for_user`；
2. 当前节点 `human_fact_confirmation`；
3. Human-in-the-loop 卡片显示：
   - interrupt kind；
   - 缺失字段 `important_data_involved`；
   - 候选 Fact；
4. safe-empty Demo Adapter 没有猜测答案，也没有绕过事实门禁；
5. 用户/Reviewer 确认 Fact 后，点击继续可从同一 checkpoint 恢复。

推荐话术：

> 默认演示 Profile 不调用真实模型。当材料没有直接证据时，系统返回空候选并安全暂停，
> 不是为了演示效果伪造事实。真实模型 Profile 也必须经过同一字段白名单和原文校验。

## 4. 1:35～2:05：Demo C · Failure Recovery

点击：

```text
C · Recovery
```

如果 Seed 后尚未重试：

```bash
curl -b /tmp/riskpilot.cookies \
  -X POST \
  http://127.0.0.1:8001/api/v3/processing-jobs/job_demo_failure_recovery/retry
```

页面重点：

1. ProcessingJob 初始为 failed；
2. retry 后 `retry_count=1`；
3. Worker 从 MinIO 读取同一对象；
4. 文档最终 completed；
5. pgvector chunks 从 2 增到 3；
6. 重复投递已完成 Job 不会生成重复 chunk。

推荐话术：

> LangGraph 没有替代任务队列。Worker 崩溃、重试和超时属于 Celery；案件决策、中断和恢复
> 属于 LangGraph。两条链路通过数据库状态和 W3C Trace 关联。

## 5. 2:05～2:30：后端与可观测性

展示：

```bash
make docker-smoke
```

真实输出：

```text
demo_cases=3
app_health=healthy
worker_health=healthy
evidence_chunks=3
agent_runs=2
compose_smoke=PASS
```

可选展示 Prometheus target：

```text
riskpilot-api    UP
riskpilot-worker UP
```

推荐话术：

> API、Worker、PostgreSQL、Redis、MinIO 使用同一 production profile。业务 ID 在 Trace
> 中做 HMAC，Prometheus 不使用 run_id/case_id 作为 label，日志和 Trace 不记录 Prompt、
> 正文、密钥或思维链。

## 6. 2:30～2:50：评测与质量

展示 README 指标或运行：

```bash
make ci
```

当前真实结果：

```text
1357 passed, 4 skipped, 5 warnings
39 个 Agent Eval Case，13 类场景，全部 Gate PASS
```

强调：

- Offline Eval 验证状态机、工具、安全和恢复协议；
- average token/cost 为 0 是 deterministic/Fake，不代表真实模型免费；
- 未运行真实模型效果时不在 README 填写虚假准确率。

## 7. 常见追问

### 为什么不用 Multi-Agent？

只有一个核心 Case Assessment Agent。Deep Research 是受限子图，因为它有独立检索上下文，
但不会参与审批或确定性规则计算。自由 Multi-Agent 会增加成本和不可控性。

### 为什么 LangGraph 不保存 Case？

checkpoint 只表示执行位置。Case、Fact、Policy、Assessment 和 Run/Event 需要事务、约束、
权限和查询，因此由 PostgreSQL Repository 持久化。

### 为什么 Celery 和 LangGraph 都需要？

- Celery：耗时任务、重试、超时、独立扩容；
- LangGraph：决策、状态、interrupt/resume、节点路由。

### 如何防越权和 Prompt Injection？

- scope 由 Runtime Context 注入；
- 模型参数不能包含 workspace/case/actor；
- Tool Policy 限制角色、阶段和副作用；
- 文档指令视为不可信数据；
- Citation/Fact 由服务端重读原文。

### 成本怎么计算？

Provider usage metadata 分 input/output token，只有部署者显式配置每百万 token 价格和币种才
估算 cost；价格快照冻结到 AgentRun，默认 0 表示未配置价格。
