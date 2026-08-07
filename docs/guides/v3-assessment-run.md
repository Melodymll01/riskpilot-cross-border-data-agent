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

关键事实只能由 `reviewer` 或 `admin` 确认：

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
- Reviewer/Admin 才能批准正式 Assessment；
- 同一 Case 同一工作流只允许一个活动 Run；
- 产品 Run 使用 revision 乐观锁，LangGraph 使用独立 SQLite checkpointer；
- 恢复时重新读取 Document、Fact、Policy Repository，不相信客户端提交的业务状态。
