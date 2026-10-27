# RiskPilot 当前范围与交付记录

项目定位：用于 AI Agent 秋招的跨境数据合规对话助手。仓库已完成 `services/control-plane` + `services/agent-runtime` 双服务迁移；Go 拥有业务数据与运行终态，Python 只执行内部 AI 计算。当前明确保留模型调用、跨会话记忆管理和证据驱动风险分析三项能力。

## 当前实现

### LangChain 模型初始化

[模型工厂](../../services/agent-runtime/infra/chat/factory.py) 统一选择远端 API 或本地 OpenAI-compatible 通道，并通过 LangChain `init_chat_model()` 创建 `BaseChatModel`。业务层的 LangChainChatAdapter 统一服务 Claim 生成、语义验证、风险分析和会话摘要，Agent 使用同一模型工厂。

- `LLM_PROVIDER` 仅保留 `api` 和 `local`；两者均显式使用 `model_provider="openai"` 接入 OpenAI-compatible 协议。
- 查询改写使用统一工厂和容器传入的模型配置；禁用改写时不构造模型。
- 模型工厂在请求前校验模型名、API Key 和 URL，固定关闭隐藏重试，并传入项目超时、流式和 usage 参数。
- 工具声明转为受 JSON Schema 约束的决策，工具参数在服务端再次校验；应用工具由 RiskPilot 执行。

本地默认 Embedding 为 BAAI/bge-small-zh-v1.5，真实输出512维归一化向量。Embedding 与 GPT 通道独立，不使用 GPT 生成数字数组代替向量。模型首次加载需要下载；同一索引必须使用一致的模型和维度。

### 账号记忆管理

账号记忆与会话历史、滚动摘要分开。Go control plane 是记忆记录、来源、revision、开关和期限的唯一写入方；Python agent runtime 只使用 Go 固定的记忆快照做相关性排序、摘要和 Agent 推理。

- 登录用户在“我的记忆”中保存、查看、编辑、删除、清空或关闭召回，也可发送“请记住：……”显式保存当前原话。
- 按 owner_id 隔离，区分表达偏好和背景事实；事实默认90天到期，访问时清理过期或失去来源的记录。
- 用户消息来源必须来自本人会话、role=user，并逐字包含保存内容。手动编辑记录新的用户确认来源和递增 revision。
- 拒绝凭据、敏感标识和控制指令。匿名身份不能写账号记忆，也不自动将匿名历史变成账号记忆。
- 召回先限制账号，再选择有界偏好及相关事实；偏好用于语言和详略，背景事实不能直接替代当前风险判断的已确认条件或法规证据。
- Go run 记录所用记忆的 ID/revision。删除、修改、关闭或过期后，Go 拒绝旧 checkpoint 对应的结果提案。
- 同轮 Agent 与最终生成器读取 Go 提供的同一固定快照，运行中新记忆从下一轮生效；记忆有效性检查与最终消息、run 终态在 Go 的同一业务事务内完成。
- 新 GitHub 身份使用不可变 provider ID；已有用户仅在 provider ID 一致时沿用原 owner，防止用户名变更导致记忆错属。

### 对话式风险分析

内部风险分析执行链路通过聊天“风险分析”入口接入，不恢复企业案件或审批工作台。Python 只生成已校验提案，Go 负责 run 关联和最终提交。

- 输入来自本轮授权的法规/材料证据和可追溯用户条件，账号背景记忆不是默认已确认事实。
- 模型生成风险项、建议、引用和受限的缺失信息字段；风险事实与建议分别转换为 Claim，复用 Evidence QA 来源回读、结构及独立语义校验。
- 仅保留通过校验的部分。未知来源、未知条件、失效文档和不受支持内容被过滤；建议失败不连带删除已支持的风险事实。
- 必要证据或条件不足时为 needs_information/unknown，不折算成低风险。总体关注程度仅从保留的风险项确定性聚合。
- 输出属于当前问题的初步定性分析，不是法律机关评级，不自动决定法定出境路径。尚未实现完整的版本化法规门槛规则包。
- 风险工具向 Go 返回已校验结果提案，避免为了重述结果再次调用模型。Go 把提案与 run 关联，并在提交最终消息前复核取消、预算、版本与引用范围。

## 必须维持的已有边界

- 用户/会话/附件范围由服务端确定，选定附件在排序及 top-k 前过滤。
- 来源由服务端分配 SRC ID；附件回读当前版本、页码、SHA和原文。
- Go 消息历史与 Python LangGraph checkpoint 分离；Go 负责恢复抢占，并把最终消息与终态原子提交。
- 正常 SSE EOF 只释放资源；活动连接异常断开才请求取消。
- 前端以 run_status 判定终态，answer 只负责显示消息。
- 实时回答和历史消息共用 DOMPurify 白名单清洗与安全链接处理；原始 HTML 作为文本显示。
- Agent 模型调用由 middleware 计时，模型、工具及整轮执行分别维护超时边界；读取图事件不会把整个工具执行误算成一次模型调用。
- LangSmith 适配器继续清空正文、Prompt、附件、模型输出和原始异常，只输出允许的元数据。

## 验证方式

```bash
make ci PY=.venv/bin/python
```

自动验证覆盖 LangChain 模型初始化、OpenAI-compatible JSON/流式/工具调用协议、账号记忆来源/隔离/编辑/过期/删除、checkpoint 记忆失效、风险项与建议独立校验，以及浏览器中的记忆管理和风险入口。

2026-10-10 模型接口层改造验证：

- [模型协议测试](../../services/agent-runtime/tests/infra/test_model_backends.py) 使用真实 LangChain SDK 与 HTTP Mock，覆盖独立/共享 API Key、JSON、流式响应、工具调用和查询改写的统一工厂。
- Ruff、格式检查、mypy 与 13 条 oracle 自检通过。全量 pytest 为 **897 passed, 1 skipped, 1 failed**；唯一失败仍为已有 `test_unsupported_recommendation_does_not_remove_supported_finding` 的测试数据缺少 `reason`，整体 CI 尚未全绿。

2026-10-11 修复后完整门禁为 **911 passed, 1 skipped**，Ruff、格式检查、mypy 与 13 条 oracle 自检通过；上轮缺少 reason 的失败测试已修复。新增回归覆盖实时/历史 Markdown 安全、嵌套工具超时、持续 chunk 的整次模型时限、记忆固定快照与原子提交、PATCH 期限、Embedding 配置和索引身份隔离。

随后修复 SSE 断连清理的上下文错误：同步生成器的推进和关闭在同一个执行上下文内串行完成，避免从事件循环回调关闭持有 ContextVar token 的生成器。最新完整门禁为 **912 passed, 1 skipped**，静态检查及 oracle 自检通过。

2026-10-12 新增[离线法规建库工具](../../services/agent-runtime/scripts/build_regulatory_kb.py)，完整门禁为 **920 passed, 1 skipped**。当前冻结 420/84 字符切分、单块 480 token 上限、Top-K 5、BGE + BM25/RRF 配置，支持来源清单校验、原文快照、固定参数报告与独立 Chroma 产物。已用第十九条示例摘录完成真实本地 512 维向量建库和检索，版本、条号、来源 URL 与 SHA256 传递正确；正式法规收录范围和本地文件目录仍待提供，示例不代表完整法规库。使用方式见[离线法规建库指南](../guides/offline-regulatory-kb.md)。

## 当前边界和下一步

本轮没有恢复企业 Workspace/Case、Reviewer 审批、正式申报报告、独立研究模式或旧版本副本。仍沿用 FastAPI、原生 JavaScript、SQLite 与 Ports/Adapters。

真实 LangSmith 外部联调与 GitHub OAuth 仍需要各自凭据。当前仅完成对应协议、身份隔离和脱敏测试。风险模型效果目前只有小规模联调证据，后续应基于冻结问题集评价正确性、拒答和召回，而不是继续扩张平台功能。
