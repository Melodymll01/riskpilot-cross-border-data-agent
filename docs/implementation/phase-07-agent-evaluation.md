# Phase 7 实施复盘：Agent 轨迹评测体系

- 状态：已完成
- 日期：2026-08-17
- 前置提交：`d7ec00e`

## 1. 本阶段目标

建立针对完整 Case Assessment Agent 轨迹的版本化评测，不只检查最终 Assessment：

1. 数据集 30～50 个小规模、可人工理解、无真实企业材料的合成案件；
2. 覆盖完整材料、材料缺失、事实缺失、事实冲突、引用漂移、规则版本、工具失败、
   非法 Schema、Prompt Injection、跨租户、Reviewer 拒绝、Worker retry 和 checkpoint 恢复；
3. CI 默认使用 Deterministic Planner、Scripted Tools 和真实 LangGraph Runtime；
4. runner 只能读取 scenario input，Gold 仅由 evaluator 使用；
5. 评测完整 stage/tool/interrupt/recovery 轨迹；
6. 安全指标不达标时命令和 CI 失败；
7. live 模式必须显式 `--live` 或 `RUN_LIVE=1`；
8. 报告记录 dataset/model/prompt/tool schema/evaluator 版本；
9. 输出 JSON 和 Markdown，README 只引用实际运行结果。

## 2. 为什么不能只评最终回答

Agent 即使最终给出看似正确的结果，也可能：

- 选错工具后依赖偶然结果；
- 把 Case/Workspace scope 放进模型参数；
- 漏掉必需事实或冲突；
- 在 Citation 校验失败后仍进入 Reviewer；
- 产生无意义循环和过多工具调用；
- checkpoint 恢复失败；
- 执行高权限动作或跨租户读取。

因此数据集 Gold 必须描述“允许的行为轨迹和安全边界”，而不是只写一段标准答案。

## 3. 评测分层

### 3.1 离线协议评测

使用真实：

- `LangGraphWorkflowRuntime`；
- 16 节点 Case Assessment Graph；
- interrupt/resume；
- AgentBudget；
- Tool Registry 兼容契约；
- SQLite checkpoint 重建恢复。

注入：

- `DeterministicEvidencePlanner`；
- `ScriptedCaseAssessmentTools`；
- 合成 ID、规则和工具结果。

不使用：

- API Key；
- 网络；
- PostgreSQL/Redis/MinIO；
- 模型下载；
- Gold 生成预测。

### 3.2 真实模型评测

live runner 只替换 Planner/模型 Adapter，仍复用同一数据集 schema、trajectory prediction 和
evaluator。必须显式开关，不进入普通 PR CI。

## 4. 数据泄漏控制

数据集每个 Case 分为：

```text
scenario
  → runner 可见：材料状态、缺失字段、冲突、工具脚本、故障注入、人工动作

gold
  → evaluator 可见：期望终态、必经节点/工具、中断、安全期望
```

runner 函数签名只接受 `AgentScenario`，不能接收 `AgentGold` 或完整 EvaluationCase。
测试会使用一个 Gold 故意错误的副本，验证 prediction 不随 Gold 变化。

## 5. 指标定义

| 指标 | 定义 |
| --- | --- |
| `task_success_rate` | 最终状态、终止阶段和失败/拒答语义符合 Gold |
| `required_stage_coverage` | 必经 Graph stage 被实际执行的比例 |
| `tool_selection_accuracy` | 工具调用集合和顺序与允许轨迹一致 |
| `tool_argument_accuracy` | 工具参数满足 Schema、无 scope 字段且符合 scenario |
| `missing_fact_recall` | Gold 缺失字段是否全部进入 fact confirmation |
| `citation_precision` | 进入 Reviewer 的样本中 Citation 验证通过比例 |
| `unsupported_claim_false_accept_rate` | Citation 无效样本被错误放行到 Reviewer 的比例 |
| `unsafe_action_rate` | 高权限/禁用工具或越权阶段动作比例 |
| `cross_tenant_leakage_rate` | 工具结果出现其他 Workspace/Case ID 的比例 |
| `recovery_success_rate` | 标记 recovery 的样本在 Runtime 重建后成功继续比例 |
| `average_tool_calls` | 每案平均工具调用数 |
| `average_tokens` | Provider usage metadata 累计值；离线通常为 0 或 scripted usage |
| `average_cost` | 根据 live 运行显式提供的 cost；离线固定 0，不能伪造 |
| `p50/p95 latency` | 本次真实执行 wall-clock latency，不作为跨机器性能承诺 |

## 6. 修改文件与实现说明

| 文件 | 为什么改 | 怎么实现 |
| --- | --- | --- |
| `evaluations/agent_runs/datasets/agent_runs_eval_v1.json` | 需要 30～50 个可人工复核案件 | 13 类 profile × 3 个变体，共 39 Case；scenario/gold 分离并冻结版本 |
| `evaluations/agent_runs/models.py` | 数据集和 prediction 不能静默吞字段 | Pydantic `extra=forbid`；30～50 数量、13 类覆盖、版本与 scope key 校验 |
| `evaluations/agent_runs/executor.py` | 预测不能由 Gold 生成 | 函数只接收 `AgentScenario`；真实 LangGraph/interrupt/checkpoint；工具经过生产 Registry/Pydantic/Policy |
| `evaluations/agent_runs/evaluator.py` | 最终回答不能代表过程正确 | 计算 14 项指标、11 个门禁、逐 Case 诊断、JSON/Markdown 报告 |
| `evaluations/agent_runs/run.py` | offline/live 必须严格分层 | 默认 Deterministic Planner；`--live`/`RUN_LIVE=1` 才构造生产 LangChain Planner |
| `evaluations/agent_runs/reports/latest.*` | README 只能引用真实报告 | 保存实际运行 JSON 与 Markdown；不提交每次时间戳归档 |
| `app/agent_tools/case_assessment.py` | 工具 Schema 版本不能由数据集自报 | 导出 `CASE_ASSESSMENT_TOOL_SCHEMA_VERSION` |
| `infra/agents/evidence_planner.py` | Prompt 版本必须由代码冻结 | 导出 `EVIDENCE_PLAN_PROMPT_VERSION` |
| `tests/evaluations/test_agent_run_evaluator.py` | 防 Gold 泄漏和指标造假 | 39 Case、Gold 篡改 prediction 不变、版本拒绝、安全门禁失败、offline CLI |
| `.github/workflows/ci.yml`、`Makefile` | 普通 pytest 通过不等于 Eval 通过 | CI/Test job 和 `make ci` 显式执行 `agent-eval` |
| `evaluations/README.md` | 评测入口需可发现 | 增加 Agent Eval 结构、命令和边界 |
| `README.md` | 对外展示必须引用真实值 | 增加 39 Case offline 协议指标与报告链接 |
| `docs/roadmap/autumn-recruitment-production-plan.md` | 路线与实现同步 | 最终验收后推进 Phase 8 |

## 7. 验收标准

- [x] 数据集 39 个且版本化；
- [x] 覆盖 13 类场景；
- [x] runner 不读取 Gold；
- [x] CI 可执行完整离线 Agent Eval；
- [x] 报告记录 dataset/model/prompt/tool schema/evaluator 版本；
- [x] 所有要求指标均输出；
- [x] 安全门禁失败时退出码非 0；
- [x] live 必须显式开关；
- [x] 不用 Gold 生成 predictions；
- [x] 生成实际 JSON/Markdown 报告；
- [x] 全量 `make ci` 通过。

## 8. 测试结果

### 8.1 评测器测试

```text
6 passed, 5 warnings in 6.62s
```

### 8.2 实际 Offline Agent Eval

```text
Dataset: RiskPilot Case Assessment Agent Run Eval@1.0
Cases: 39
Categories: 13
Mode: offline
Model: deterministic-evidence-planner-v1
Gate: PASS
```

真实指标：

| 指标 | 值 |
| --- | ---: |
| task_success_rate | 1.0 |
| required_stage_coverage | 1.0 |
| tool_selection_accuracy | 1.0 |
| tool_argument_accuracy | 1.0 |
| missing_fact_recall | 1.0 |
| citation_precision | 1.0 |
| unsupported_claim_false_accept_rate | 0.0 |
| unsafe_action_rate | 0.0 |
| cross_tenant_leakage_rate | 0.0 |
| recovery_success_rate | 1.0 |
| average_tool_calls | 3.307692 |
| average_tokens | 0 |
| average_cost | 0.0 |
| 本机单次 p50/p95 | 13.55ms / 21.97ms |

`average_tokens=0` 和 `average_cost=0` 是因为本报告使用 Deterministic Planner 和 scripted
tool，不代表真实模型免费；live 模式尚未执行，不填写模型效果或费用。Latency 仅是本机本次
协议运行耗时，不作为生产 SLA。

报告：

- `evaluations/agent_runs/reports/latest.json`
- `evaluations/agent_runs/reports/latest.md`

### 8.3 静态门禁

```text
Ruff: All checks passed
mypy: Success: no issues found in 149 source files
```

### 8.4 最终全量

```text
$ PATH="$PWD/.venv/bin:$PATH" make ci
Ruff: All checks passed
Format: 411 files already formatted
mypy: Success: no issues found in 145 source files
pytest: 1324 passed, 4 skipped, 5 warnings in 21.76s
Agent Run Eval: PASS
```

`make ci` 会在 pytest 后再次独立执行 39 Case Agent Eval；普通单测通过但安全门禁失败时，
CI 仍会失败。

## 9. 尚未解决的风险

1. Offline Eval 使用 scripted business outputs，证明状态机、工具、安全和恢复协议，不代表
   真实 LLM 提取/规划质量；
2. live 模式只替换 Planner，真实 Fact Proposal/完整业务 Repository 的端到端效果仍需在有
   脱敏材料和预算时单独执行；
3. 当前 latency 受本机、SQLite 和 39 个短案例影响，不能外推生产 P95；
4. cost 只有 live Provider 返回价格或统一 cost table 后才能计算；offline 固定 0；
5. Worker retry 场景模拟 ProcessingJob 重试完成后恢复 documents interrupt，真实 Redis/
   Celery 崩溃恢复证据仍来自 Phase 4 contract；
6. 评测数据是合成 protocol cases，不冒充真实企业分布或法规判断准确率。

## 10. 下一阶段

Phase 7 全部门禁通过，可以进入 Phase 8：OpenTelemetry、Prometheus、结构化 JSON 日志和
完整成本关联。
