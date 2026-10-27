# Evidence QA

`POST /api/v2/qa` 是独立证据问答入口。请求必须有当前用户 session cookie；服务端派生 `owner_id`，请求 schema 拒绝额外授权字段。

```json
{"question":"境外接收方承担哪些责任？","corpora":["regulatory","user_docs"],"top_k":5}
```

指定材料时可附加 `document_ids`，仅能引用本人已处理完成的附件；先用 `/api/v2/files` 上传，再查询 `/files/{id}` 等待 ready。未指定时只检索当前用户的授权附件。

返回包含 `status`、`answer`、`claims`、`citations`、`verification`、`support_verification` 和 `repair_report`。附件引用包括 document/version/page/SHA/quote。结构无效、原文漂移、来源越权、语义不支持或校验异常均不会作为可信 Claim 放行。

此接口不读取企业案件、Workspace、Assessment 或长期用户画像。Copilot 在完成工具调用后，会把本轮服务端收集的候选证据交给同一个 `answer_from_citations` 入口，因此聊天与独立 QA 共享来源回读和 Claim 校验；两者的检索编排与 HTTP 入口仍然不同。

协议自检：

```bash
.venv/bin/python -m evaluations.evidence_qa.run --oracle-self-check --no-write
```

这是 gold 标签回放，不是模型效果。真实验证器评测仍需要显式 `--live`、真实模型服务和输出审阅，详见命令的 `--help`。
