# 第三阶段运行控制与真实流式验证

日期：2026-10-07。基线提交为 `ff06372136e43a4e187a71d7106acf2215de2bde`。本阶段继续使用单机 SQLite、LangChain、LangGraph 和 LangSmith，不恢复已退出的企业流程。

## 运行控制语义

每轮对话由一个服务端 `RunControl` 统一约束：

- 默认活动执行时间为 120 秒；Agent 主模型和其他模型调用的单次等待上限为 45 秒，工具调用为 20 秒。
- 默认模型调用额度为 12 次，工具调用额度为 6 次。额度和已用次数写入 `conversation_runs`，恢复不会清零。
- Agent 决策、查询改写、查询 embedding、Cross-Encoder reranker、Claim 生成及独立语义验证均计入模型调用数。没有使用模型的缓存命中和确定性排序不计数。
- LangChain/OpenAI 客户端的隐藏自动重试被关闭。当前显式 embedding retry 和网页搜索 fallback 会记录 retry，并在每次新尝试前再次消耗对应调用额度。
- `input_tokens`、`output_tokens` 只累计 provider 实际返回的 usage。`usage_status` 为 `unknown`、`partial` 或 `complete`；未返回 usage 时不把它解释成零用量。没有价格表，因此不生成费用值。
- LangGraph recursion limit 仍保留为框架自身的防护，但不作为运行预算。

活动时间只在 run 为 `running`/`cancel_requested` 且有执行器时消耗。进入 `interrupted` 时保存剩余秒数并清除 deadline，恢复时以剩余秒数建立新 deadline，因此人工暂停等待不计入预算。

后台 L2 摘要不共享前台 run 的额度。它仍由独立线程池调度，每次任务最多进行一次摘要模型调用，并使用独立的 `MEMORY_SUMMARY_MAX_TOKENS`（默认 800）；其调用不会写入对话 run 的调用计数。

## 状态与取消

状态机区分：

- 活动态：`running`、`cancel_requested`；
- 可恢复暂停：`interrupted`；
- 终态：`completed`、`cancelled`、`timed_out`、`budget_exhausted`、`failed`。

`POST /api/v2/copilot/runs/{run_id}/cancel` 按 owner 查询并幂等执行：

- running run 先写 `cancel_requested`，当前执行器观察后写 `cancelled`；
- interrupted 或尚未开始消费事件的 run 可直接进入 `cancelled`；
- 已处于终态时返回原终态，不覆盖结果；
- 其他用户看不到也不能取消该 run。

SSE 客户端断开会调用同一取消路径，原因记录为 `client_disconnected`。模型、工具、检索子模型、Claim 生成和验证都会在调用前及等待期间检查取消和 deadline。同步 I/O 无法安全强杀时，主执行链会立即停止等待；底层调用仍受 adapter timeout 约束，其迟到结果不会推进 LangGraph、启动后续调用或提交答案。

SSE 生命周期明确区分正常 EOF、资源释放和异常断连。正常 EOF 后只调用资源 `close()`；只有响应尚未消费完成时才发送一次 `disconnect()` 通知。断连通知也只取消仍为 `running`/`cancel_requested` 的 run，不会把已经进入 `interrupted` 的 run 改成 cancelled。显式 `/cancel` 不受此限制，用户仍可取消暂停 run。

最终 assistant 消息、task 时间和 run 终态仍在一个 SQLite 事务中提交。取消先于提交生效时，条件更新失败并回滚消息；服务启动时会把没有活动执行器的遗留 `running`/`cancel_requested` run 收敛为 `failed`/`cancelled`，避免永久运行状态。

## 增量输出语义

Agent 使用 LangGraph 的 `messages + updates` 流模式。支持 streaming 的 ChatModel 产生 `AIMessageChunk` 后，服务端直接发送 `draft_delta`，不是等待完整答案后再拆字。

边界如下：

- 只有完成一次真实证据检索后产生的自然语言候选才作为“待核验草稿”显示；
- tool-call chunk、内部推理字段、原始工具结果 JSON 和结构化 Claim JSON 不发送到页面；
- 页面把草稿明确标成“待核验”，不能反馈、复制或作为历史正式回答；
- 进入验证后仍执行来源回读、Claim-Citation 结构校验及独立语义校验；
- `answer` 仍由通过校验的 Claim 直接渲染，不再额外调用模型改写；
- 最终回答、拒答、取消、超时、预算耗尽或失败都会移除草稿。只有 `answer` 会作为 assistant 消息持久化。

前端通过 `run_status` 表达正在执行、检索、生成、核验、停止、完成、取消、超时、预算耗尽和失败。每次开始、恢复或切换会话都会更新本地流代际编号，旧 run 的迟到事件无法写入新会话 DOM。

事件契约中，`answer` 只携带可展示并已持久化的助手消息，不代表 run 成功。成功、正常安全拒答、技术失败和其他终态均由先到达的服务端 `run_status` 决定；前端不会因后续 `answer` 或流结束回调覆盖该终态。刷新页面时重新读取任务的 `latest_run`，与数据库状态保持一致。

## 三个框架的实际职责

| 框架 | 本阶段职责 |
| --- | --- |
| LangChain | ChatModel 的真实 chunk 流、Agent 工具绑定、调用前预算 middleware，以及结构化 Claim 生成/验证模型调用 |
| LangGraph | `create_agent` 图执行、`messages + updates` 双流、SQLite checkpoint、中断与同 thread 恢复；不承担业务预算判定 |
| LangSmith | 在同一 run 下关联 Agent、工具、Claim 生成、语义验证、调用计数、重试和结束原因；继续删除正文、输出、附件及原始异常 |

## 验收覆盖

| 场景 | 自动化证据 |
| --- | --- |
| 真正模型增量 | `test_genuine_model_chunks_arrive_before_model_call_completes` 在模型第二次调用尚未完成时收到首个 `draft_delta` |
| 草稿校验失败 | `test_streamed_draft_is_discarded_when_semantic_verification_fails` 验证草稿被丢弃，持久化内容仅为安全拒答 |
| 模型执行中停止 | `test_cancel_during_model_stops_before_tools_or_verification` 验证后续工具、生成和验证均未启动 |
| 工具执行中停止 | `test_cancel_during_tool_discards_late_result` 验证迟到检索结果不产生答案 |
| 工具/验证超时 | `test_tool_timeout_discards_late_result`、`test_verification_timeout_discards_streamed_draft` 验证终态为 timed_out 且无正式回答 |
| 总时限与调用额度 | `test_overall_timeout_during_model_has_no_final_message`、模型和工具 limit 回归验证对应 end reason |
| 重试与恢复 | `test_pause_time_and_call_counts_are_not_reset_on_resume` 及真实 SQLite checkpoint 恢复验证计数和剩余时间不重置 |
| 取消/提交竞态 | SQLite 并发测试验证 cancel 或 terminal commit 只能有一个结果，消息与终态保持一致 |
| 首事件和断连 | run 在返回 iterator 前创建；首事件前取消、消费中关闭以及 SSE pending-next 断连均收敛为 cancelled |
| 前端状态与协议 | Node 测试覆盖成功/冲突/失败/无 answer、分段 UTF-8、CRLF、心跳及旧流代际过滤 |
| 正常 SSE 收尾 | 真实 SQLite checkpoint 经 HTTP SSE 发出 `run_interrupted` 并正常 EOF 后仍为 interrupted；重建 runtime 后通过 HTTP resume 完成 |
| 权威失败终态 | 可控模型抛出 `RuntimeError` 后，真实浏览器显示安全失败正文且状态保持“失败”；刷新后继续与数据库 failed/runtime_error 一致 |

### 浏览器验收

当时的浏览器回归启动真实 HTTP/SSE 服务并用本机 Google Chrome 连接页面，后端注入可控 streaming ChatModel、异常模型和慢检索器。该旧测试文件已随双服务职责收敛删除；以下仅保留历史验收结果：

1. `draft_delta` 在模型调用完成前显示为“待核验草稿”；
2. 工具区域只显示受控摘要，不显示原始 JSON；
3. 校验完成后草稿被移除，并显示带 `SRC-*` 的正式回答；
4. 第二轮在模型阻塞期间点击“停止生成”，页面显示“已取消”，没有正式回答；
5. 模型异常时安全失败正文正常显示，但 `answer` 不会把“失败”覆盖成“已完成”；
6. 刷新失败会话后从数据库恢复 `failed/runtime_error`，停止按钮和等待状态均已结束；
7. 页面 JavaScript error 为零。

这是真实浏览器到真实 HTTP API/SSE 的链路，但模型、检索、Claim 生成和语义验证使用可控 Fake；不属于真实模型效果测试。

## 验证命令与结果

```bash
make ci PY=.venv/bin/python
```

结果：

- Ruff 与 Ruff format：通过，261 个 Python 文件格式一致；
- mypy：88 个源文件，0 issues；
- pytest：**863 passed, 1 skipped**；跳过项仍是需要 `RUN_LIVE=1` 和真实外部模型/embedding 的 live RAG；
- Evidence QA oracle：13 个场景全部通过，`production_evidence=false`；
- 浏览器验收：历史回归通过，Chrome 连接真实 HTTP/SSE，后端使用可控 streaming 模型与慢检索器；该结果不替代当前 Go/Python 合同测试。
- 隔离业务库、checkpoint、对象与 Chroma 目录导入应用成功，共 7 条顶层路由；OpenAPI 包含 owner-scoped run cancel 入口。

## 独立审查回归修复记录

修复前实际复现：

- 真实 SQLite checkpoint 在 tools 后暂停并发出 `run_interrupted`；通过 SSE 包装完整消费到 EOF 后，数据库却变为 `cancelled/client_disconnected`，resume 返回冲突。
- 可控模型抛出 `RuntimeError` 后，数据库正确记录 `failed/runtime_error`，页面也显示安全失败正文，但随后收到 `answer` 时 UI 状态变成“已完成”。

修复后实际结果：

- 正常 EOF 只释放流资源，暂停 run 保持 `interrupted`，重建 runtime 后通过真实 HTTP resume 成功；显式取消暂停 run 仍为 `cancelled/user_cancelled`，再次取消幂等且 resume 返回 409。
- 活动流异常断开仍通知后端取消；重复断连通知和资源关闭只执行一次，不产生重复消息或错误终态。
- `answer` 不再参与终态判定。技术失败正文显示后状态保持“失败”，页面刷新后仍与数据库 `failed/runtime_error` 一致；正常安全拒答继续以 `completed` 结束。

## 未验证与已知限制

- 当前环境没有真实模型、embedding 或 LangSmith 凭据，因此未执行真实模型效果、真实 token usage 和 LangSmith 出站联调。
- Python 同步 I/O 不能被线程强制终止；取消/超时保证的是主执行链停止、迟到结果隔离和有限 transport timeout，不宣称底层远端计算立即停止。
- 已统一 wall-clock、模型调用和工具调用预算；token 预算与基于真实价格的费用预算尚未实现。
- 待核验草稿是真实模型增量，但经校验的最终回答仍整段发送。流式结构化 Claim JSON 不直接暴露，以避免半截 JSON 和未校验结论被误认成正式答案。
- 当前控制注册表是进程内的，持久状态在 SQLite；不支持多实例执行租约或跨节点主动信号广播。

本阶段没有调用任何模型驱动 CLI，也没有运行 standalone `codex`。
