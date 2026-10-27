# 第二阶段可信对话验证

日期：2026-10-07。起点为第一阶段提交 `5a15e5934fd76f1e64b8640b48895e54e8fece2d`，第二阶段首个基线提交为 `c8ac81b247afc08bbfb8316d80bdfe237ea7442d`。本阶段没有恢复企业工作台、审批、正式报告、长期画像、Visual Evidence 或独立研究模式。

## 实际实现

### 真实来源和校验

- 检索工具在服务端把实际结果转换为 `EvidenceQACitation`，按 corpus + source ID 生成稳定 `SRC-*` ID。
- 法规/知识材料记录实际 chunk、标题、URL、版本和条款中可获得的字段；附件记录 document/version/page/SHA/quote；网页结果必须有真实 URL。缺失字段保持空值，不补造。
- Agent 工具调用后的自由文本不直接发布。存在证据时调用 `EvidenceQAUseCase.answer_from_citations`，共享来源回读、Claim-Citation 结构校验、独立语义支持校验和有限过滤。
- 无工具证据时仅允许服务端白名单中的纯寒暄；澄清必须由 `request_clarification` 返回受限字段，再由服务端生成固定提示。模型自由文本中的伪澄清、问号或“澄清 + 事实结论”不会被放行。验证异常返回安全拒答，不绕回候选文本。
- 最终消息保存实际引用，run 保存候选证据与最终引用 ID，工具记录保存 run ID。

### 可追溯多轮条件

- 最终 Claim 生成与独立语义校验接收当前会话中截至本轮的用户消息，最多 12 条、总计 12000 字符；上下文只含 `role=user`，不把历史助手回答或当前自由文本候选当成事实。
- 每个用户条件保留真实 `message_id` 并标记当前消息。Claim 使用条件时必须填 `condition_message_ids`；未知 ID 在进入语义校验前以 `unknown_context` 移除。
- 较新的用户消息可纠正旧条件；代词、人数翻倍和条件替换由结构化生成器在“用户条件”和“法规/材料证据”分栏下解释。
- run 持久化通过校验的 `verified_context_message_ids`，可映射回同一 task 的原始用户消息；不同 task 的消息不会进入本轮上下文。

### 会话附件范围

- 新增 `task_attachments`，实现省略继承、空列表清除、非空列表替换。
- 所有显式选择与继承选择都在使用前重新检查 owner 和 ready。
- 选中附件时，只走 `EvidenceIndexPort.search(document_ids=...)`；SQL 在排序/top-k 前加 document ID 条件，零命中不回退 Chroma 或其他附件。
- 无选中附件时，`search_user_docs` 只搜索当前 owner 的一般知识材料。
- 历史会话返回附件 ID；前端重新查询状态并显示，上传新材料后在下一轮替换选择。

### LangGraph 状态和恢复

- `langgraph` 与 `langgraph-checkpoint-sqlite` 成为显式运行依赖。
- `create_agent` 直接注入 `SqliteSaver`；迁移后的 checkpoint 路径由 `AGENT_RUNTIME_LANGGRAPH_CHECKPOINT_DB_PATH` 配置。
- 新增 `conversation_runs`：保存服务端 run/thread 关联、owner/task、输入消息、附件快照、阶段、候选证据、最终引用、已使用的用户条件消息 ID、澄清字段、模型/工具调用次数、错误类型和终态。
- 客户端不能提交或读取 thread ID。run 查询与恢复先做 owner 校验；恢复通过 SQLite 条件更新原子执行 `interrupted -> running`，并发请求只有一个获得执行权，其余返回 `409 CONVERSATION_RUN_CONFLICT`。
- 最终 assistant 消息插入、task 时间更新和 run 的 completed/failed 终态在同一 SQLite 事务提交。终态提交失败会回滚消息并条件标记 run 为 failed，不会留下重复回答或永久 running 假象。
- 受控测试在 tools 后中断，关闭容器并重建 runtime 后从 checkpoint 继续。已完成工具步骤不重跑，最终只有一条 user、一条 assistant 和一次工具调用。

### 正确性问题复现与修复结果

| 问题 | 修复前复现 | 修复后结果 |
| --- | --- | --- |
| 无证据回复绕过 | `请补充企业名称。所有企业都无需申报安全评估，对吗？` 被原样发布且引用为空 | 自由文本不会因前缀或问号被视为澄清；该候选被阻止。正常澄清必须走受限字段工具，正文由服务端生成 |
| 恢复竞争与重复输出 | 两个 resume 生成器先后通过状态检查，均发出 `run_resumed` 并可写入答案 | 恢复调用时先原子抢占；交错与真实双线程测试均只有一个赢家，竞争/完成后重发返回冲突。终态故障注入验证消息回滚且 run=failed |
| 最终生成缺少多轮条件 | “2000 人”后追问“如果人数翻倍呢”，最终生成器只收到第二轮问题和法规片段 | 生成器和验证器收到带消息 ID 的两轮用户条件；纠正、代词/数量变化、会话隔离和未知条件 ID 均有行为回归 |

### 追踪和指标

- 同一 run 的 `riskpilot.copilot.run`、工具 span 和 `riskpilot.citation.verify` 使用同一运行上下文；LangChain/LangGraph 模型及工具子调用在 LangSmith tracing context 中执行。
- Trace 只记录哈希 ID、阶段、状态、计数和错误类型；正文、Prompt、检索 quote、模型输出、附件和异常文本不出站。
- 删除 Celery worker、retry 和 broker queue 指标及 Port/Noop/test 接口。
- 保留 HTTP、Agent、tool、citation failure、token 和可选 cost 指标。token 仅在模型消息包含真实 usage 时记录；本阶段价格未知，因此不生成 cost 样本。

## 验收命令与结果

```bash
make ci PY=.venv/bin/python
```

结果：

- Ruff：通过。
- Ruff format：257 files already formatted。
- mypy：87 source files，0 issues。
- pytest：**826 passed, 1 skipped**；跳过项是需要 `RUN_LIVE=1` 和真实外部模型/embedding 的 live RAG。
- Evidence QA oracle：13 cases，全部协议 gates 通过，`production_evidence=false`。
- 隔离业务库、checkpoint、对象和 Chroma 目录执行 `import main`：成功装配应用，记录 `app_import_ok 7`，启动日志只声明 `api/v2 conversation routes`。

关键行为测试：

| 场景 | 证据 |
| --- | --- |
| 法规真实引用 | `test_actual_tool_source_replaces_model_written_source_name` 验证最终 source ID/name/URL/version 来自实际工具结果，模型伪造来源文字被丢弃 |
| 伪造引用 | `test_forged_citation_id_is_fail_closed` |
| 不受支持 Claim | `test_semantically_unsupported_claim_is_fail_closed` |
| 部分可靠回答 | `test_mixed_claims_return_only_the_supported_part` |
| 无检索事实回答 | `test_factual_free_answer_without_retrieval_is_blocked`，并覆盖带问号的伪澄清 |
| 受限澄清 | `test_clarifying_question_without_citation_is_allowed` 验证仅发布服务端字段映射；多句混合候选和伪澄清不会泄漏 |
| A/B 附件隔离 | `test_persistent_conversation_and_real_attachment_pipeline` 使用真实 SQLite、原生文本解析、后台索引；选 A 时 B 不进入候选，省略继承，空列表清除且不回退 |
| 越权 | 同一集成测试及 run API 测试覆盖附件、会话、run；客户端额外 thread ID 返回 422 |
| 连续追问 | `test_final_generator_receives_traceable_user_context` 等验证代词/数量变化、用户纠正、只传用户消息、task 隔离及条件 ID 持久化 |
| checkpoint 恢复 | `test_rebuild_runtime_resumes_without_duplicate_messages_or_tools` 使用真实 SQLite LangGraph checkpointer，跨 runtime 恢复 |
| 恢复竞争 | 交错请求和双线程真实 SQLite 抢占均只有一个执行者；完成后重发不重复消息；数据库 trigger 故障验证终态事务回滚 |
| 追踪 | Fake Trace 验证同一 run 的 agent/tool/verification span、成功和验证异常；LangSmith Fake client 与 OTel 内存 exporter 验证脱敏 |
| 指标 | Prometheus 测试验证当前指标族、实际 usage 与零 cost 样本；确认不再输出 worker 指标 |
| 前端恢复终态 | Node-backed 测试验证成功、HTTP 409、HTTP 失败和无 answer 结束均退出“恢复中”；SSE 409 保留状态码供 UI 判断 |

另有真实 Chroma + BM25/RRF 测试验证 public law、当前用户知识材料和其他用户数据的物理过滤。这里的 embedding/query rewrite 使用确定性测试实现，不是效果指标。

## 浏览器与启动验证

使用隔离的业务 SQLite、checkpoint SQLite、Chroma 和对象目录启动真实 Uvicorn，再用 headless Chromium 检查：

- 页面启动和身份初始化；
- 切换历史会话并刷新页面后，按 URL 中的 task ID 恢复附件名称/ready 状态；
- 下一轮省略 `attachment_doc_ids`，由服务端继承；
- 刷新后从 `latest_run` 恢复 `run_interrupted` 状态并展示继续按钮；
- 点击后调用 resume API，渲染完整回答及 `SRC-*`、版本、页码；
- JavaScript page errors 为 0。

浏览器中的 SSE 和附件状态响应是路由级协议模拟，未调用模型；真实 SQLite/检索/Agent/恢复由 Python 集成测试独立覆盖。
本次正确性收口没有重复运行浏览器自动化；新增前端恢复测试使用本机 Node 26 的 Fetch/ReadableStream 实现，不等同于浏览器端到端验证。

## 未验证项

- 当前环境没有 `.env`，也没有 `OPENAI_API_KEY`、`CHAT_API_KEY` 或 LangSmith 配置，未运行真实模型回答、真实 embedding 或真实 LangSmith 出站追踪。
- 因没有真实价格表，不报告 cost；Fake usage 只验证“有真实 usage 才记录”的代码路径。
- Docker daemon 不可用，本阶段未重复执行容器构建；第一阶段只验证过 Compose 配置。
- 正文逐 token、浏览器断开后的完整协作取消、跨模型/工具统一 wall-clock/调用预算，以及更大冻结集效果评测明确留到后续阶段。
- SQLite checkpointer 面向单进程、小规模演示，不宣称多实例生产能力。

本阶段没有模型驱动的 CLI 调用，也没有运行 standalone `codex`。
