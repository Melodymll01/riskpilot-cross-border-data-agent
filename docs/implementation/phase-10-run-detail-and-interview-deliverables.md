# Phase 10 实施复盘：Run Detail 与秋招展示材料

- 状态：已完成
- 日期：2026-08-17
- 前置提交：`03189be`

## 1. 本阶段目标

不重写整个前端，只把 Case Assessment 主线压缩成面试官 2～3 分钟可看懂的页面：

1. Run 基础状态、token、cost、latency；
2. 16 个 LangGraph 节点的结构化时间线；
3. Evidence Plan；
4. Tool 调用、脱敏参数、结果摘要、耗时和重试；
5. Human-in-the-loop 中断原因；
6. Fact 候选、冲突与原文证据；
7. Policy Rule 结果；
8. Claim-Citation 校验；
9. 最终 Assessment；
10. retry/cancel/continue/review 操作；
11. Demo A/B/C 固定入口；
12. 2～3 分钟演示脚本和简历项目描述。

页面不得展示：

- Chain of Thought；
- 原始 Prompt；
- 模型完整输出；
- Authorization/Cookie/API Key；
- 未脱敏案件正文；
- LangGraph 原始 checkpoint。

## 2. 实施前审计

### 2.1 已有后端数据

- `GET /runs/{run_id}`：Run 状态、stage、token、cost、retry、时间；
- `GET /runs/{run_id}/events`：结构化 RunEvent；
- `GET /runs/{run_id}/plan`：EvidencePlan；
- `GET /cases/{case_id}/facts`：Fact；
- `GET /cases/{case_id}/assessments/active`：最终 Assessment；
- RunEvent 已保存：
  - `stage_started/stage_completed`；
  - `tool_completed`；
  - `facts_proposed`；
  - `fact_confirmation_required`；
  - `conflict_detected`；
  - `human_review_required`；
  - terminal event。

### 2.2 已有前端

`frontend/cases.js` 已能：

- 选择 Workspace/Case；
- 上传和处理文档；
- 查看/确认 Fact；
- 继续 Run；
- 图片证据召回；
- 显示 Run ID、状态、阶段、重试。

### 2.3 缺口

1. 前端没有节点时间线；
2. 没有 Tool 卡片；
3. 没有 Evidence Plan；
4. 没有中断原因；
5. 没有 token/cost/latency；
6. 没有规则和 Citation 结果；
7. 没有最终 Assessment；
8. retry/cancel/review 没有统一入口；
9. 前端若逐个拼 Run/Event/Plan/Fact/Assessment，会产生多次请求和重复业务解释；
10. 没有固定 Demo 快捷入口；
11. 没有演示脚本、面试问答和简历描述。

## 3. 为什么新增 Run Detail 聚合 API

前端不应该自行解释事件语义。服务端已经知道：

- 哪些事件是节点；
- 哪些 payload 可以展示；
- 哪些字段必须脱敏；
- 哪个 Assessment 属于该 Run；
- 哪些 Fact 是 candidate/conflicting；
- latency 如何计算。

因此新增：

```text
GET /api/v3/runs/{run_id}/detail
```

由 application use case 从业务 Repository 聚合：

```text
AgentRun
+ RunEvent
+ EvidencePlan
+ CaseFact
+ AssessmentBundle
→ RunDetail DTO
```

不读取 LangGraph checkpoint 正文，不新增业务 SSOT。Run/Event/Assessment 仍是原始事实。

## 4. 聚合 DTO 设计

### 4.1 Run Summary

```text
run_id
status
stage
token_usage
cost
duration_ms
retry_count
interrupt
```

`duration_ms`：

- completed/failed/cancelled：`completed_at - started_at`；
- active：`updated_at - started_at`；
- 未开始：0。

### 4.2 Timeline

每条只包含：

```text
sequence
event_type
stage
status
summary
created_at
```

不返回原始 checkpoint 或模型思维过程。

### 4.3 Tool Calls

从 `tool_completed` 事件提取：

```text
tool_name
stage
sanitized_arguments
result_summary
duration_ms
retry_count
token_usage
safe_output_summary
```

输出只允许既有 RunEvent 安全 JSON；页面默认折叠。

### 4.4 HITL

从最近的中断事件提取：

```text
kind
reason
missing_fact_fields
conflict_field_names
candidate_fact_ids
```

### 4.5 Rule/Citation

从 Tool Event 提取：

- deterministic rule tool：rule IDs、missing fields、status；
- citation tool：valid、citation_count、finding_count。

### 4.6 Assessment

仅当活动 Assessment 的 `generated_by_run_id == run_id` 时关联，避免串错版本。

## 5. 固定 Demo 入口

Seed ID 已由 Phase 9 冻结：

| Demo | Case ID | 页面重点 |
| --- | --- | --- |
| A | `case_demo_happy_path` | 自动走到 Reviewer、规则与 Citation |
| B | `case_demo_human_loop` | 缺失事实、中断与恢复 |
| C | `case_demo_failure_recovery` | failed Job、retry、Worker 恢复 |

前端只提供快捷选择，不自动修改数据库。

## 6. 实际修改：为什么改、怎么实现

### 6.1 LangGraph Node 耗时

| 文件 | 为什么改 | 怎么实现 |
| --- | --- | --- |
| `infra/workflows/case_assessment_graph.py` | OTel span 有节点耗时，但页面刷新后无法读取 | `_node()` 在轻量 state 追加 `node_trace={stage,status,duration_ms}`；不保存 Prompt、正文或异常 message |
| `infra/workflows/langgraph_runtime.py` | 新 Run 需要明确初始化 trace 容器；提交前审计还发现 `_safe_state()` 会过滤新字段，导致耗时无法进入持久化 RunEvent | 初始 state 增加空 `node_trace`，并把它加入轻量状态白名单；仍不允许正文、Prompt、凭证或任意客户端字段进入返回状态 |
| `app/use_cases/assessment_runs.py` | RunEvent 才是持久化展示 SSOT | 新 node trace 转成带 `duration_ms/status` 的 `stage_completed` 事件；旧 checkpoint 没有 node trace 时兼容 0ms |

### 6.2 安全 Run Detail 聚合

| 文件 | 为什么改 | 怎么实现 |
| --- | --- | --- |
| `app/use_cases/assessment_runs.py` | 前端不应自行解释多类业务事件 | 新增 `AssessmentRunDetail`、Timeline、Tool、Interrupt、Action DTO；复用 Run 授权；读取 Event/Plan/Fact/Assessment Repository |
| `api/v3/schemas.py` | HTTP 输出需要严格 Pydantic schema | 新增 Run Detail response 及 Timeline/Tool/Interrupt/Action schema |
| `api/v3/assessment_runs.py` | 页面需要一次请求 | 新增 `GET /runs/{run_id}/detail`，复用 Fact/Assessment serializer |

聚合规则：

- Tool argument/output 均按工具名字段白名单二次裁剪；
- 只关联 `generated_by_run_id == run_id` 的活动 Assessment；
- 终态 Run 不显示历史 interrupt；
- duration 根据 started/updated/completed 计算；
- cost currency 从服务端模型快照读取；
- capability 同时检查 Run 状态和当前用户 Workspace 角色；
- 指定 Reviewer/Admin 才得到 `can_review=true`。

### 6.3 前端 Run Detail

| 文件 | 为什么改 | 怎么实现 |
| --- | --- | --- |
| `frontend/api.js` | 需要聚合与操作端点 | 增加 detail/retry/cancel/review |
| `frontend/index.html` | 面试官需要一页看完整闭环 | 新增 Demo A/B/C 快捷入口、Run 操作、Plan、Timeline、Tool、HITL、Rule/Citation、Assessment 容器 |
| `frontend/cases.js` | 原页面只显示 4 个 Run 字段 | 刷新时加载 detail；按 capabilities 控制按钮；纯 DOM 渲染；Tool 默认折叠；Reviewer 支持 approve/reject |
| `frontend/style.css` | 新页面需在桌面和窄屏可读 | 增加双栏 Detail、时间线、Tool card、HITL 警示、chips、Assessment 指标和响应式布局 |

安全边界：

- 继续禁止 `.innerHTML`；
- 所有动态值通过 `textContent`；
- 不请求 checkpoint；
- 不显示模型正文；
- Tool 只显示后端白名单结构。

### 6.4 Demo 登录可发现性

| 文件 | 为什么改 | 怎么实现 |
| --- | --- | --- |
| `api/v2/schemas.py`、`api/v2/auth.py` | 前端需要知道本地 Demo 是否启用，但不能猜环境变量 | `/auth/me` 增加 `demo_login_enabled`；普通部署默认 false |
| `frontend/auth.js`、`frontend/app.js` | Seed 后浏览器应能进入固定 Workspace | 仅服务端声明可用时展示“进入固定 Demo”；调用隐藏 `/auth/demo` 后切到案件工作台 |
| `frontend/index.html` | 提供可发现入口 | 用户菜单增加默认隐藏的 Demo 登录按钮 |

### 6.5 面试交付物

| 文件 | 内容 |
| --- | --- |
| `docs/guides/interview-demo-script.md` | 0:00～2:50 固定讲解顺序、Demo A/B/C、命令和常见追问 |
| `docs/guides/resume-project-description.md` | AI 应用岗/后端岗/精简版三种简历描述、真实性边界、深挖问题 |
| `README.md` | Run Detail 能力、49 个 V3 路由、演示与简历文档入口 |
| `docs/roadmap/autumn-recruitment-production-plan.md` | Phase 0～10 完成，进入最终交付审计 |
| `tests/infra/test_langgraph_runtime.py` | 防止未来再次出现“Graph 已记录、Runtime 安全投影却静默丢弃”的回归 | 断言 `node_trace` 被保留，且每项只能有 `stage/status/duration_ms` 三个字段 |

## 7. API 变化

新增：

```text
GET /api/v3/runs/{run_id}/detail
```

响应包括：

```text
run
duration_ms
cost_currency
timeline
evidence_plan
tool_calls
interrupt
facts
rule_evaluation
citation_verification
assessment
actions
```

兼容变更：

```text
GET /api/v2/auth/me
```

新增 `demo_login_enabled=false` 字段，供前端决定是否展示本地 Demo 登录。

## 8. Agent 状态变化

新增 checkpoint 轻量字段：

```text
node_trace[]
  stage
  status
  duration_ms
```

不新增业务数据库表。`node_trace` 最终转为 RunEvent，业务事实仍由 Run/Event/Fact/Assessment
Repository 管理。

未保存：

- Chain of Thought；
- Prompt；
- 文档正文；
- 模型回答；
- 凭证；
- OTel Span 对象。

## 9. 测试与真实证据

### 9.1 聚焦回归

```text
Run Detail / Frontend / Auth / Graph：63 passed, 5 warnings in 4.35s
核心 Detail API / Frontend：40 passed, 5 warnings in 2.57s
提交前 Runtime 安全投影回归：51 passed, 5 warnings in 5.00s
Ruff: All checks passed
mypy: 151 source files
Node --check: app/auth/api/cases 全部通过
```

API 测试验证：

- Editor `can_review=false`；
- 指定 Reviewer `can_review=true`；
- HITL missing fields；
- Assessment 只关联同一 Run；
- Tool output 白名单；
- 跨 Workspace detail 返回 404；
- 响应不含 `chain_of_thought/raw_prompt/authorization/api_key/password/secret`。
- `node_trace` 通过 Runtime 安全白名单进入结果，但单项只包含
  `stage/status/duration_ms`。

### 9.2 真实 Compose API

使用 Phase 9 PostgreSQL Seed 数据：

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
  interrupt=fact_confirmation
  missing_fact_fields=[important_data_involved]
  timeline=16

safe_payload=PASS
frontend_dom=PASS
```

### 9.3 Docker smoke

```text
demo_cases=3
app_health=healthy
worker_health=healthy
evidence_chunks=3
agent_runs=2
compose_smoke=PASS
```

### 9.4 最终全量

```text
PATH="$PWD/.venv/bin:$PATH" make ci

Ruff: All checks passed
Format: 433 files already formatted
mypy: Success: no issues found in 151 source files
pytest: 1357 passed, 4 skipped, 5 warnings in 32.96s
Offline Agent Eval: 39 cases / 13 categories / PASS
```

该命令默认没有配置真实模型 API Key，不访问网络、不下载模型、不产生模型费用。

## 10. 验收标准

- [x] Run Detail 一次请求返回展示所需聚合数据；
- [x] Timeline 覆盖实际事件，不伪造未执行节点；
- [x] Tool 参数和结果经过安全白名单；
- [x] HITL 原因可见；
- [x] Evidence Plan 可见；
- [x] Fact/Conflict/Rule/Citation 可见；
- [x] token/cost/latency 可见；
- [x] Assessment 只关联同一 Run；
- [x] 页面不展示思维链、Prompt、密钥和正文；
- [x] retry/cancel/continue/review approve/reject 可用；
- [x] 三个 Seed Demo 可固定打开；
- [x] 页面刷新后状态来自后端持久化；
- [x] 2～3 分钟演示脚本完成；
- [x] 简历项目描述完成；
- [x] `make docker-smoke` PASS；
- [x] 最终全量 `make ci` 通过。

## 11. 尚未解决的风险

1. 页面是原生 HTML/JS，适合项目演示，但没有引入前端组件测试框架；
2. Timeline duration 是后端单节点 wall-clock，不等于 distributed trace 总耗时；
3. 旧 Run 没有 node trace 时显示 0ms，避免伪造历史耗时；
4. Demo C 文档恢复后固定入口仍可展示最终 Job 状态，但不是 Agent Run Detail；
5. 真实模型 token/cost 只有 Provider 返回 usage 且配置价格时才有非零值；
6. 公网部署、Grafana Dashboard 和完整企业 IAM 仍是 P2，未冒充完成。

