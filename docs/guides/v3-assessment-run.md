# V3 Case Assessment Run 演示指南

本指南演示 RiskPilot V2 的核心闭环：

```text
Workspace
→ Case
→ Document
→ Fact confirmation
→ PolicyRule
→ LangGraph Assessment Run
→ Reviewer approval
→ immutable Assessment
```

## 1. 前置条件

1. 启动服务并完成登录；
2. 使用同一 HTTP 会话保留 `copilot_session` Cookie；
3. 创建者默认为 Workspace `admin`；
4. Case 必须设置 `assessment_date`；
5. 启动 Run 前至少存在一个已发布且在评估日期生效的规则。

LangGraph checkpoint 默认保存在：

```text
./data/langgraph-checkpoints.sqlite3
```

可通过环境变量覆盖：

```bash
LANGGRAPH_CHECKPOINT_DB_PATH=./data/langgraph-checkpoints.sqlite3
```

浏览器打开首页后，左侧选择 `案件工作台`，输入 `case_id` 即可查看当前 Case、材料、
Fact 和 Assessment Run。工作台在 Run 停于 `detect_missing_facts` 时会读取最新
`fact_confirmation_required` 事件，自动列出缺失字段。

## 2. 创建 Workspace 与成员

```http
POST /api/v3/workspaces
Content-Type: application/json

{"name":"跨境合规组"}
```

创建 Reviewer：

```http
PUT /api/v3/workspaces/{workspace_id}/members/github:reviewer
Content-Type: application/json

{"role":"reviewer"}
```

## 3. 创建并推进 Case

```http
POST /api/v3/cases
Content-Type: application/json

{
  "workspace_id":"{workspace_id}",
  "title":"海外客服项目",
  "assessment_date":"2026-08-07",
  "reviewer_id":"github:reviewer"
}
```

依次推进：

```http
POST /api/v3/cases/{case_id}/transitions
{"target":"collecting"}
```

```http
POST /api/v3/cases/{case_id}/transitions
{"target":"ready_for_assessment"}
```

Case 进入 `review_required` 后不能通过通用状态接口绕过审批。只有活动 Assessment 审批
或新版本重评可以更新该状态。

## 4. 上传并处理材料

```http
POST /api/v3/cases/{case_id}/documents
Content-Type: multipart/form-data

file=@case.txt
```

接口返回 `job_id`。依次执行：

```http
POST /api/v3/processing-jobs/{job_id}/parse
POST /api/v3/processing-jobs/{job_id}/index
```

文档达到 `ready` 后才满足 Assessment Run 的文档门禁。若文档仍在处理中，Run 会停在：

```text
status=waiting_for_user
current_stage=validate_documents
```

处理完成后调用：

```http
POST /api/v3/runs/{run_id}/continue
```

## 5. 创建并确认 Fact

可以先让服务端基于当前案件的 `ready` 文档生成候选：

```http
POST /api/v3/cases/{case_id}/fact-proposals
Content-Type: application/json

{
  "field_names":[
    "important_data_involved",
    "destination_country"
  ],
  "document_ids":["{document_id}"]
}
```

约束：

- `field_names` 是强制白名单，模型不能返回其他字段；
- 单次最多 20 个字段、20 个文档、20 万字符正文，防止 Prompt 成本失控；
- 只读取当前 Case 已完成索引的当前 DocumentVersion；
- 每个候选必须带页码和逐字 quote，服务端会重新读取解析快照核验；
- 候选统一写成 `critical`，状态只能是 `proposed` 或 `conflicting`；
- 同字段已有未拒绝事实且值不同，新候选会返回 `conflicting`；
- Reviewer/Admin 确认其中一个值时，同字段其他 active facts 会在同一事务中转为
  `rejected`，确保规则引擎只看到一个 confirmed 值；
- 一批候选原子写入，任一证据非法时整批不落库；
- 模型不负责确认，所有候选都不能直接进入规则引擎。

也可以人工创建 Fact：

```http
POST /api/v3/cases/{case_id}/facts
Content-Type: application/json

{
  "field_name":"important_data_involved",
  "value":true,
  "source_type":"user",
  "confidence":1.0,
  "criticality":"critical"
}
```

关键事实和模型生成的全部候选只能由 `reviewer` 或 `admin` 确认：

```http
POST /api/v3/facts/{fact_id}/transitions
Content-Type: application/json

{"target":"confirmed"}
```

若规则需要的 confirmed facts 不完整，Run 会停在：

```text
status=waiting_for_user
current_stage=detect_missing_facts
```

补齐并确认事实后再次调用 `POST /api/v3/runs/{run_id}/continue`。

当前 Graph 不会在节点内直接调用模型或写入 Fact。`fact_confirmation` 中断后，由前端或
调用方显式请求 `fact-proposals`、展示证据和冲突，再由 Reviewer 确认，最后继续 Run。

案件工作台对应操作：

1. 点击“从文档生成候选”；
2. 检查每个候选的状态、值和页级 quote；
3. Reviewer/Admin 点击“Reviewer 确认”；
4. 点击“继续 Run”，等待状态进入下一中断或 `waiting_for_review`。

Editor 可以生成候选，但确认按钮会收到 403 并提示切换 Reviewer/Admin。

## 6. 创建并发布规则

```http
POST /api/v3/workspaces/{workspace_id}/policy-rules
Content-Type: application/json

{
  "rule_id":"SYNTHETIC-001",
  "ruleset_version":"synthetic-v1",
  "jurisdiction":"CN",
  "effective_from":"2026-01-01",
  "required_fact_fields":["important_data_involved"],
  "condition":{
    "field":"important_data_involved",
    "operator":"eq",
    "value":true
  },
  "result":{
    "candidate_path":"security_assessment",
    "risk_level":"high",
    "required_actions":["提交安全评估材料"]
  },
  "source_clause_ids":["synthetic-clause"]
}
```

发布：

```http
POST /api/v3/workspaces/{workspace_id}/policy-rules/SYNTHETIC-001/synthetic-v1/publish
```

规则创建和发布只允许 Workspace `admin`。

## 7. 启动 Assessment Run

```http
POST /api/v3/cases/{case_id}/assessment-runs
Content-Type: application/json

{
  "ruleset_version":"synthetic-v1",
  "model_config_snapshot":{
    "provider":"deterministic",
    "model":"rule-engine"
  }
}
```

`model_config_snapshot` 只允许非敏感标签。`api_key`、`password`、`secret`、原始 prompt 和
思维链字段会被拒绝。

材料和事实齐备时，运行会自动：

```text
validate documents
→ evaluate deterministic rules
→ generate immutable Assessment
→ pause for human review
```

返回：

```text
status=waiting_for_review
current_stage=human_review
```

生成后的 `rule_trigger` Finding 同时返回：

- `fact_ids`：规则实际消费的 confirmed Fact；
- `evidence_ids`：document Fact 在本次 Assessment 中冻结的证据快照；
- `clause_ids`：PolicyEvaluation 对应的规则条款快照；
- `evidence_citations`：原 evidence ID、Fact/version、Document/version、页码、quote、
  offset 和 source SHA。

生成和 Reviewer 批准前都会重新读取当前 Fact 与文档原文。Fact 版本、当前
DocumentVersion、SHA、quote 或 offset 发生漂移时，Assessment 不能生成或批准。
user 来源 Fact 不伪造文档引用，仍通过 `fact_ids + fact_versions` 审计。

## 8. 查询 Run 与事件

```http
GET /api/v3/runs/{run_id}
GET /api/v3/cases/{case_id}/assessment-runs
GET /api/v3/runs/{run_id}/events?after_sequence=0
```

事件使用连续 `sequence`，可通过 `after_sequence` 增量拉取。响应不会暴露：

- LangGraph `thread_id`；
- checkpoint 内容；
- 模型配置快照；
- 原始 prompt、文档正文或思维链。

## 9. Reviewer 审批

切换为指定 Reviewer 后：

```http
POST /api/v3/runs/{run_id}/review
Content-Type: application/json

{
  "decision":"approved",
  "comment":"证据和规则核验通过"
}
```

批准后的原子结果：

```text
Assessment.status=approved
Case.status=completed
AgentRun.status=completed
```

拒绝必须填写 comment：

```http
POST /api/v3/runs/{run_id}/review

{
  "decision":"rejected",
  "comment":"需要补充传输链路材料"
}
```

拒绝后的结果：

```text
Assessment.status=rejected
Case.status=ready_for_assessment
AgentRun.status=completed
```

## 10. 重试与取消

技术失败的 Run：

```http
POST /api/v3/runs/{run_id}/retry
```

非终态 Run：

```http
POST /api/v3/runs/{run_id}/cancel
```

取消是幂等操作，保留 LangGraph checkpoint、产品 Run 和事件用于审计，不会删除证据或
Assessment。

## 11. 设计边界

- 简单问答仍不使用 LangGraph；
- Graph 不直接持久化最终 Assessment；
- 规则计算只消费 confirmed facts；
- Fact 提议模型只生成候选，不得确认事实或直接推进 Run；
- Reviewer/Admin 才能批准正式 Assessment；
- Finding 的 Fact / Evidence / Clause 引用必须形成可验证闭包，漂移时 fail closed；
- 同一 Case 同一工作流只允许一个活动 Run；
- 产品 Run 使用 revision 乐观锁，LangGraph 使用独立 SQLite checkpointer；
- 恢复时重新读取 Document、Fact、Policy Repository，不相信客户端提交的业务状态。
