# RiskPilot 当前范围与交付记录

项目定位：用于 AI Agent 秋招的跨境数据合规对话助手。当前明确保留三项能力：通过 Traex 内部路由调用 GPT、登录账号的跨会话记忆管理，以及对话中的证据驱动风险分析。LangChain、LangGraph、LangSmith 均继续使用。

## 当前实现

### Traex GPT 模型适配

[模型工厂](infra/chat/factory.py) 统一选择 API Key、Traex 或本地 OpenAI-compatible 通道；[TraexChatModel](infra/chat/traex_model.py) 与 API ChatModel 均通过 LangChain BaseChatModel 接入。业务层的 LangChainChatAdapter 统一服务 Claim 生成、语义验证、风险分析和会话摘要，Agent 使用同一模型工厂。

- 保留 `LLM_PROVIDER=api` 与原有 `CHAT_API_*` / `OPENAI_API_*` 配置；`LLM_PROVIDER=traex` 使用本机 Trae 登录态，运行失败不会自动切换到 API。
- 查询改写已改为使用统一工厂和容器传入的模型配置，避免 Traex 模式仍直接创建 OpenAI 客户端；禁用改写时不构造模型。
- 使用指定的 traex 可执行文件和 models 列表中的 GPT 模型标识；日志记录 cli=traex、provider=trae 与 requested_model。
- 工具声明转为受 JSON Schema 约束的决策，工具参数在服务端再次校验；应用工具由 RiskPilot 执行。
- CLI 使用独立临时目录、ephemeral 会话、只读沙箱、禁止内部工具的配置，并关闭内层记忆、插件、技能和代理委派功能。若出现内层工具事件则拒绝结果。
- 通过 stdin 传入受限请求，使用参数数组启动；超时或取消时终止本次进程组。应用预算继续限制调用和活动时间。
- 当前 exec JSONL 只观察到完成消息，不伪造逐 token 输出。Traex 模式显示实际执行阶段和完整最终回答；API 模型仍可使用原生 chunk 流。
- CLI 未暴露可验证的 temperature、stop 和远端最大 token 硬限制。stop 参数会被拒绝，其余请求参数与未强制状态明确记录；usage 仅使用 CLI 实际返回值。内部远端调用次数和 resolved_model 未返回时保持未知。

本地默认 Embedding 为 BAAI/bge-small-zh-v1.5，真实输出512维归一化向量。Embedding 与 GPT 通道独立，不使用 GPT 生成数字数组代替向量。模型首次加载需要下载；同一索引必须使用一致的模型和维度。

### 账号记忆管理

[账号记忆用例](app/use_cases/account_memory.py) 与原有会话历史/滚动摘要分开。

- 登录用户在“我的记忆”中保存、查看、编辑、删除、清空或关闭召回，也可发送“请记住：……”显式保存当前原话。
- 按 owner_id 隔离，区分表达偏好和背景事实；事实默认90天到期，访问时清理过期或失去来源的记录。
- 用户消息来源必须来自本人会话、role=user，并逐字包含保存内容。手动编辑记录新的用户确认来源和递增 revision。
- 拒绝凭据、敏感标识和控制指令。匿名身份不能写账号记忆，也不自动将匿名历史变成账号记忆。
- 召回先限制账号，再选择有界偏好及相关事实；偏好用于语言和详略，背景事实不能直接替代当前风险判断的已确认条件或法规证据。
- run 记录所用记忆的 ID/revision。删除、修改、关闭或过期后，不允许旧 checkpoint 重新注入已失效记忆，恢复会被拒绝并取消该暂停运行。
- 同轮 Agent 与最终生成器读取相同的固定引用，运行中新记忆从下一轮生效；记忆有效性检查与最终消息、run 终态在同一 SQLite 写事务内完成。PATCH 省略到期时间时保留原值，背景事实不能显式改为永久保存。
- 新 GitHub 身份使用不可变 provider ID；已有用户仅在 provider ID 一致时沿用原 owner，防止用户名变更导致记忆错属。

### 对话式风险分析

[风险分析用例](app/use_cases/risk_assessment.py) 通过 assess_risk 工具和聊天“风险分析”入口接入，不恢复企业案件或审批工作台。

- 输入来自本轮授权的法规/材料证据和可追溯用户条件，账号背景记忆不是默认已确认事实。
- 模型生成风险项、建议、引用和受限的缺失信息字段；风险事实与建议分别转换为 Claim，复用 Evidence QA 来源回读、结构及独立语义校验。
- 仅保留通过校验的部分。未知来源、未知条件、失效文档和不受支持内容被过滤；建议失败不连带删除已支持的风险事实。
- 必要证据或条件不足时为 needs_information/unknown，不折算成低风险。总体关注程度仅从保留的风险项确定性聚合。
- 输出属于当前问题的初步定性分析，不是法律机关评级，不自动决定法定出境路径。尚未实现完整的版本化法规门槛规则包。
- 风险工具直接交付已校验结果，避免为了重述结果再次调用模型。风险分析结果与 run 关联，最终消息仍受原子终态事务、取消与预算约束。

## 必须维持的已有边界

- 用户/会话/附件范围由服务端确定，选定附件在排序及 top-k 前过滤。
- 来源由服务端分配 SRC ID；附件回读当前版本、页码、SHA和原文。
- 消息历史与 LangGraph checkpoint 分离；恢复原子抢占，最终消息与终态一起提交。
- 正常 SSE EOF 只释放资源；活动连接异常断开才请求取消。
- 前端以 run_status 判定终态，answer 只负责显示消息。
- 实时回答和历史消息共用 DOMPurify 白名单清洗与安全链接处理；原始 HTML 作为文本显示。
- Agent 模型调用由 middleware 计时，模型、工具及整轮执行分别维护超时边界；读取图事件不会把整个工具执行误算成一次模型调用。
- LangSmith 适配器继续清空正文、Prompt、附件、模型输出和原始异常，只输出允许的元数据。

## 验证方式

```bash
make ci PY=.venv/bin/python
.venv/bin/python scripts/verify_traex_features.py --live --output evaluations/feature_integration/reports/latest.json
```

新增验证覆盖 Traex 子进程协议与取消、账号记忆来源/隔离/编辑/过期/删除、真实 checkpoint 的记忆失效、账号改名、风险项与建议独立校验，以及真实浏览器中的记忆管理和风险入口。

2026-10-10 模型接口层改造验证：

- [双通道协议测试](tests/infra/test_model_backends.py) 14 条通过。API 路径使用真实 SDK 与 HTTP Mock，覆盖独立/共享 API Key、JSON、流式响应和工具调用；Traex 路径使用隔离子进程验证协议、模型选择、查询改写与取消传递。
- 通过本机 `/Users/bytedance/.local/bin/traex` 实际调用 `gpt-5.4`，JSON 聊天探针通过，耗时 11.64 秒；`cli=traex`、`provider=trae`，CLI 返回 input_tokens=10893、output_tokens=545。该结果只证明模型接口连通与 JSON 协议，不代表风险联合验证通过；API Key 路径本轮没有访问真实远端。
- Ruff、格式检查、mypy 与 13 条 oracle 自检通过。全量 pytest 为 **897 passed, 1 skipped, 1 failed**；唯一失败仍为已有 `test_unsupported_recommendation_does_not_remove_supported_finding` 的测试数据缺少 `reason`，整体 CI 尚未全绿。

2026-10-11 修复后完整门禁为 **911 passed, 1 skipped**，Ruff、格式检查、mypy 与 13 条 oracle 自检通过；上轮缺少 reason 的失败测试已修复。新增回归覆盖实时/历史 Markdown 安全、嵌套工具超时、持续 chunk 的整次模型时限、记忆固定快照与原子提交、PATCH 期限、Embedding 配置和索引身份隔离。

随后修复 SSE 断连清理的上下文错误：同步生成器的推进和关闭在同一个执行上下文内串行完成，避免从事件循环回调关闭持有 ContextVar token 的生成器。最新完整门禁为 **912 passed, 1 skipped**，静态检查及 oracle 自检通过。

2026-10-12 新增[离线法规建库工具](scripts/build_regulatory_kb.py)，完整门禁为 **920 passed, 1 skipped**。当前冻结 420/84 字符切分、单块 480 token 上限、Top-K 5、BGE + BM25/RRF 配置，支持来源清单校验、原文快照、固定参数报告与独立 Chroma 产物。已用第十九条示例摘录完成真实本地 512 维向量建库和检索，版本、条号、来源 URL 与 SHA256 传递正确；正式法规收录范围和本地文件目录仍待提供，示例不代表完整法规库。使用方式见[离线法规建库指南](docs/guides/offline-regulatory-kb.md)。

真实联合脚本使用 Traex GPT、真实本地512维Embedding、已核对的官方法规摘录和合成材料；账号身份是隔离 fixture，不代表真实 OAuth 联调。真实运行的结论与限制记录在 [联合验证记录](docs/validation/traex-memory-risk.md)。普通回归与13条oracle自检不构成真实模型效果指标。

## 当前边界和下一步

本轮没有恢复企业 Workspace/Case、Reviewer 审批、正式申报报告、独立研究模式或旧版本副本。仍沿用 FastAPI、原生 JavaScript、SQLite 与 Ports/Adapters。

真实 LangSmith 外部联调与 GitHub OAuth 仍需要各自凭据。当前仅完成对应协议、身份隔离和脱敏测试。风险模型效果目前只有小规模联调证据，后续应基于冻结问题集评价正确性、拒答和召回，而不是继续扩张平台功能。
