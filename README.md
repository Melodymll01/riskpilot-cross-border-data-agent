# RiskPilot

RiskPilot 是一个面向 AI Agent 秋招展示的跨境数据合规对话助手。用户可以持续提问、选择或上传材料；单个 Copilot 根据问题调用法规、用户文档或公开网页检索，并只把通过来源与 Claim-Citation 校验的事实性内容作为最终回答。

默认语言模型通过 `traex` 内部路由调用 GPT，本地 `BAAI/bge-small-zh-v1.5` 提供真实的 512 维中文向量。登录账号可管理跨会话偏好和背景记忆；聊天中可发起带证据校验的初步风险分析。

当前完成 Go/Python 迁移第一阶段：Go + Gin 接管页面入口、GitHub/匿名登录、Cookie、请求控制及 SSE 转发；Python 保留 Agent、记忆、材料与最终消息事务。用户表由 Go 写入，Python 通过签名身份读取账号并执行业务。后续会话、记忆与运行状态迁移见 [迁移方案](docs/architecture/go-python-migration.md)。

## 模型接入

- `LLM_PROVIDER=traex` 复用本机 Trae 认证。`TraexChatModel` 将应用工具声明映射为结构化模型决策，实际工具始终由 RiskPilot 执行；每次 CLI 请求使用临时目录和临时会话，并接入取消与超时。
- 当前 Traex `exec --json` 提供完成消息事件，适配器不会伪造 token 增量。该模式仍有真实工具与阶段 SSE；原生 API 模型模式继续支持已有 chunk 草稿流。usage 记录 CLI 实际返回值；内部模型调用次数和未返回的实际后端模型 ID 保持未知。
- Traex CLI 未暴露可验证的 temperature、stop 或远端最大输出 token 控制。适配器拒绝不支持的 stop 参数，记录其余请求参数及未强制状态；时间、调用次数和输入/输出体积仍由应用限制。摘要的 800-token 参数在该通道是请求提示，不宣称远端硬 token 上限。

所有语言模型入口共用 `infra/chat/factory.py` 的 `build_chat_model`：Agent 工具决策、查询改写、会话摘要、风险分析、Claim 生成和语义验证均由 `LLM_PROVIDER` 选择通道。`LangChainChatAdapter` 将两个后端统一为业务层的 `ChatPort`；切换后端不需要修改用例。配置错误直接报错，运行失败不会自动换用另一通道。

保留原有 API Key 方式，兼容 OpenAI 协议的服务可这样配置：

```dotenv
LLM_PROVIDER=api
CHAT_API_KEY=your-api-key-here
CHAT_API_BASE=https://open.bigmodel.cn/api/paas/v4
CHAT_MODEL=glm-4-flash
```

`CHAT_API_KEY` 和 `CHAT_API_BASE` 留空时，分别沿用原有 `OPENAI_API_KEY` 和 `OPENAI_API_BASE`。API 模式无需安装 Traex。切换到本机 Trae 登录态时使用：

```dotenv
LLM_PROVIDER=traex
TRAEX_BIN=/Users/bytedance/.local/bin/traex
TRAEX_MODEL=gpt-5.4
TRAEX_TIMEOUT_SECONDS=95
```

`TRAEX_MODEL` 从 `/Users/bytedance/.local/bin/traex models` 返回的 `config_name` 中选择。Traex 模式无需聊天 API Key；`EMBED_PROVIDER` 单独控制向量模型，选择 API Embedding 时仍需其对应的 `OPENAI_API_KEY`。已有 `LLM_PROVIDER=local` 的 Ollama 配置继续支持。修改 `.env` 后重启应用生效。

## 账号记忆与风险分析

- 登录后在账户菜单打开“我的记忆”，可自动识别或选择用户档案、表达偏好、业务背景，并管理有效期、编辑、复核和删除。没有设置过开关的账号默认关闭召回，保存和开启分别控制，也可发送“开启记忆”。
- “请记住：我叫马乐乐，是学生”会按原话拆为姓名和身份字段。姓名与偏好默认长期保留，身份/组织默认一年有效、180 天复核，业务背景默认 90 天。开启后可问“我叫什么”“我的职业是什么”，答案附记忆来源；当前会话的新声明优先，法规与风险结论继续经过证据校验。
- GitHub 新账号以不可变 provider ID 建立身份；已有账号仅在 provider ID 验证一致时沿用原 owner，避免用户名修改导致记忆错属。
- 记忆按账号隔离，记录来源与 revision。匿名用户不写账号记忆；凭据、敏感标识和控制指令被拒绝。过期或失去来源的记忆在访问时清理。删除、修改、关闭或过期后，引用旧记忆的 checkpoint 不允许继续执行。
- 点击“风险分析”或在聊天中要求风险评估，Agent 检索法规和授权材料后调用 `assess_risk`。结果包含已验证风险项、建议、引用和缺失信息；信息不足保持 `unknown`，不默认为低风险。
- 风险结果是针对当前材料的初步定性分析，不是法律机关评级，也不自动决定法定出境路径。总体关注程度由保留风险项确定性聚合；法规阈值缺少可核对条件时应补充信息。

## 当前能力

- 原生 HTML/JavaScript 聊天界面、新建会话、历史消息、附件选择、SSE 工具过程、真实模型草稿增量和经校验的完整最终回答。
- LangChain `create_agent` 负责单 Agent 工具调用；工具仅使用服务端注入的 owner 和会话附件范围。
- 每条检索证据由服务端分配稳定的 `SRC-*` ID。附件引用保留 document/version/page/SHA/quote；法规和网页保留真实可得的标题、URL、版本或条款信息，不补造缺失字段。
- Copilot 工具调用后的自由文本只是候选。最终事实回答复用 Evidence QA 的结构化 Claim 生成、来源回读、独立语义支持校验和有限过滤；未知引用、漂移来源和不受支持 Claim 不会放行。
- 没有检索证据时，只允许服务端白名单中的纯寒暄；澄清由受限工具选择缺失字段并由服务端生成提示。未经验证的事实性自由回答、伪澄清和混合结论会被阻止。
- 最终生成和语义校验接收同一会话中带 `message_id` 的有界用户条件，支持代词、数量变化和后续纠正；不把历史助手回答当事实。通过校验的条件消息 ID 随 run 持久化，跨会话不串数据。
- 会话附件选择持久化：省略字段继承，显式空列表清除，非空列表替换。指定附件时，过滤发生在检索排序和 top-k 之前，不会回退到其他用户材料。
- LangGraph 使用独立 SQLite checkpoint 保存单轮图状态；服务端生成并保存 run/thread 关联、阶段、附件快照、候选证据和最终引用。恢复前通过 SQLite 原子抢占执行权，最终消息与 run 终态原子提交，不重复消息或已经完成的工具副作用。
- 每轮使用统一运行控制：默认活动执行时间 120 秒、模型调用最多 12 次、工具调用最多 6 次；Agent 决策、查询改写、embedding、reranker、Claim 生成和语义验证共享额度。中断等待不计时，恢复沿用剩余时间和已用次数。
- `POST /api/v2/copilot/runs/{run_id}/cancel` 支持 owner 隔离和幂等取消。运行态先进入 `cancel_requested`，执行器确认停止后进入 `cancelled`；另有 `timed_out`、`budget_exhausted`、`failed` 和 `completed` 终态。
- 模型正文通过 LangGraph message chunks 作为“待核验草稿”增量展示；最终答案仍由结构化 Claim、来源回读和独立语义验证生成。草稿在拒答、取消、超时或失败时被丢弃，绝不作为正式消息持久化。
- LangSmith/TracePort 以脱敏 span 关联 Agent、工具、检索、引用校验和最终状态；Prometheus 保留 HTTP、Agent、工具、真实 token 用量和引用失败指标。
- SQLite 保存身份、会话、消息、工具调用、附件选择和执行记录；Chroma 保存法规及一般知识材料；本地对象目录保存附件原件。

企业 Workspace/Case、Reviewer 审批、正式申报报告、独立 Deep Research、Visual Evidence 和外部风险画像服务不在当前产品中。账号记忆与对话式风险分析是本轮明确新增的能力。

## 本地启动

需要 Python 3.12+、Go 1.26+ 与本机 C 编译工具链（SQLite 驱动使用 CGO）：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
cp .env.example .env
```

编辑 `.env`。默认 Traex 模式需要本机已登录 Trae，首次本地 Embedding 加载会下载模型；不需要独立 GPT API Key。若选择 API 模型或 API Embedding，则填写对应服务凭据。GitHub 登录和 LangSmith 使用各自独立凭据。健康检查成功不表示外部模型、OAuth 或追踪平台已经联调通过。

```bash
make serve PY=.venv/bin/python
```

访问 <http://127.0.0.1:8765>，这是 Go 入口；Python 内部服务监听 8766，不接受浏览器 Cookie。GitHub OAuth 默认回调仍为 `http://127.0.0.1:8765/api/v2/auth/github/callback`。

启动器自动创建独立的内部服务密钥、构建 Go 程序并管理两个进程。运行状态与日志分别为 `data/dev-stack.json`、`logs/dev-stack.log`。停止用 `make serve-stop PY=.venv/bin/python`，查看状态用 `make serve-status PY=.venv/bin/python`。后台启动可用 `.venv/bin/python scripts/dev_stack.py start --detach`。`make serve-python` 仅用于迁移阶段的独立回归与调试。

`/docs` 展示当前 Python 内部业务模型，对外认证由 Go 实现，合同说明见 [contracts](contracts/README.md)。Mac 编译工具与最新 SDK 不兼容时，可在本地 `.env` 设置 `GO_SDKROOT`；它仅影响项目 Go 构建，不修改系统配置。

启用 GitHub 登录需先在 [GitHub OAuth Apps](https://github.com/settings/developers) 创建 OAuth App：Homepage URL 填 `http://127.0.0.1:8765`，Authorization callback URL 填上述回调地址。将该应用的 Client ID 和生成的 Client Secret 分别填入本地 `.env` 的 `GITHUB_CLIENT_ID`、`GITHUB_CLIENT_SECRET`，然后重启服务。示例中的 `dev-placeholder-*` 只是占位值；未替换时登录入口会提示尚未配置，访客仍可使用。Client Secret 仅保存在本地配置中，不提交到仓库。

管理员登录后会在左侧看到“知识库”入口，可查看和维护公共法规材料。将 GitHub 用户的数字 ID 按 `github:id:<数字 ID>` 写入本地 `.env` 的 `ADMIN_USER_IDS`（例如 `ADMIN_USER_IDS=github:id:123456`）并重启服务；企业私有材料仍按账号隔离，不会出现在公共知识库管理页。

持久数据默认位于：

- 业务与执行元数据：`data/rag.sqlite3`
- LangGraph checkpoint：`data/conversation-checkpoints.sqlite3`
- 法规/知识材料向量：`data/chroma_db`
- 用户附件原件：`data/objects`

文件任务在 API 进程内的有界线程池执行；当前本地基线使用单个 API 进程。

`.env.example` 为本地 BGE Parent-Child 索引使用独立的 `data/chroma_db_bge_parent_child_v1`。物理记录仍是 420 字子块；章节父块由 `parent_section_id` 在检索时重建。附件 SQLite 索引和 Chroma 均必须使用与入库时一致的 Embedding 模型与索引 schema。

Embedding、查询改写、检索和入库均使用容器传入的配置。同步与异步向量缓存按 provider、模型、接口地址指纹和维度配置隔离；Chroma 与附件索引还记录模型身份、索引 schema、Embedding 输入策略和实际向量维度。模型或 schema 不匹配时在查询或替换材料前报错。没有模型身份元数据的旧索引也要求显式重建：为新索引选择独立的 `CHROMA_PERSIST_DIR` 和 `SQLITE_DB_PATH`，重新导入原始材料。新的 SQLite 路径会建立独立的会话与附件数据库，原文件不会被自动迁移、删除或重标为新模型。

可复现的真实三项联合验证（数据和账号均为隔离验收 fixture）：

```bash
.venv/bin/python scripts/verify_traex_features.py --live --output evaluations/feature_integration/reports/latest.json
```

它使用真实 Traex GPT、真实本地向量和官方法规摘录，测试账号记忆召回与合成材料风险分析。它不验证真实 GitHub OAuth、LangSmith 外部服务或完整法规准确率。

## 对话与附件语义

法规库可以在提问前批量离线构建，来源清单、固定参数、建库命令与接入方式见 [离线法规建库](docs/guides/offline-regulatory-kb.md)。

`POST /api/v2/copilot/chat` 和 `/chat/stream` 的 `attachment_doc_ids` 是三态字段：

- 省略：继承该会话当前选择；
- `[]`：清除该会话选择；
- 非空数组：用经过归属和 ready 状态校验的新列表替换。

切换历史会话时，`GET /api/v2/tasks/{task_id}` 返回已保存的附件 ID，前端逐项重新查询授权状态。附件选中时 `search_user_docs` 只查询这些附件；没有选中附件时，它只查询当前用户的一般知识材料。

每轮响应先发 `run_started`，随后通过 `run_status` 表达 Agent、工具、生成、核验及终态，通过 `draft_delta` 增量展示明确标注的待核验草稿，最后才发送持久化的 `answer`。`answer` 只表示有一条助手消息可展示，不能单独判定运行成功；前端以服务端终态 `run_status` 为准，因此技术失败附带的安全说明不会把页面状态改成“已完成”。可恢复中断会发 `run_interrupted`，页面提供继续按钮和运行中的“停止生成”按钮。

执行状态可通过 `GET /api/v2/copilot/runs/{run_id}` 查询，通过 `POST /api/v2/copilot/runs/{run_id}/resume` 恢复，通过 `POST /api/v2/copilot/runs/{run_id}/cancel` 请求取消。同一 run 同时只有一个恢复执行者，竞争或完成后重发返回 HTTP 409。API 不接受客户端 `thread_id`，也不返回内部 thread ID。

SSE 正常 EOF 只关闭迭代器并释放线程资源，不触发业务取消；只有响应未正常消费完成时才发送 `client_disconnected`。因此安全暂停后的 HTTP 响应可以正常结束并继续恢复，显式取消暂停 run 仍会进入 `cancelled`。

## 主要接口

| 入口 | 用途 |
| --- | --- |
| `/api/v2/auth/*` | 匿名身份、GitHub 登录、当前身份、退出 |
| `/api/v2/tasks`、`/tasks/{id}` | 会话、消息和当前附件选择 |
| `/api/v2/copilot/chat`、`/chat/stream` | 同步与 SSE 对话 |
| `/api/v2/copilot/runs/{id}`、`/resume`、`/cancel` | 授权执行状态、恢复和取消 |
| `/api/v2/files`、`/files/{id}`、`/download` | 附件上传、状态和原件下载 |
| `/api/v2/files/search` | 当前用户附件检索 |
| `/api/v2/qa` | 独立法规/用户附件 Evidence QA |
| `/api/v2/documents/*` | 法规和一般知识材料管理 |
| `/api/v2/health/*`、`/metrics` | 健康检查和聚合指标 |

## 三个框架的实际职责

| 框架 | 当前作用 |
| --- | --- |
| LangChain | 流式模型接入、工具定义、`create_agent`、调用前预算 middleware 和结构化生成/验证调用 |
| LangGraph | Agent 图循环、`messages + updates` 增量事件、SQLite checkpoint、节点中断与同一 thread 恢复 |
| LangSmith | 通过 TracePort 的隐私适配器关联 Agent、工具、Claim 生成、语义验证和最终原因；默认关闭，正文和输出被清空 |

消息历史和 graph checkpoint 分开：前者服务跨轮上下文与 UI，后者只服务一轮执行恢复，防止重复注入和重复副作用。

## 检查

```bash
make ci PY=.venv/bin/python
```

该命令运行 Ruff、格式检查、mypy、pytest 和 Evidence QA oracle 协议自检。Fake、oracle、真实模型和真实 LangSmith 结果必须分开解释。详细证据见：

- [第一阶段清理基线](docs/validation/phase1-baseline.md)
- [第二阶段可信对话验证](docs/validation/phase2-conversation.md)
- [第三阶段运行控制与真实流式验证](docs/validation/phase3-runtime-control.md)
- [安全、记忆一致性与真实风险联合验证](docs/validation/traex-memory-risk.md)

## 当前限制

- 增量内容是明确标注的“待核验草稿”；为了维持引用正确性，经 Evidence QA 校验的正式回答仍以完整 `answer` 事件一次发送，而不是把未完成的结构化 JSON 暴露给用户。
- Python 同步 I/O 无法被线程强制终止。取消和超时会立即阻断后续节点与最终提交，底层迟到结果被丢弃；HTTP/模型适配器另有有限超时作为资源回收上界。
- 本阶段统一了 wall-clock 和调用次数预算；token 仅在 provider 返回真实 usage 时累计，缺失时标为 `unknown` 或 `partial`，尚未用估算值代替。费用没有价格表时保持未知。
- 真实模型回答质量、真实 LangSmith 平台追踪及更大冻结集评测需要可用凭据后单独执行；运行成功不代表回答正确。
- 法规语料需要导入有官方出处和适用时间的材料；系统不会把缺失 URL、版本或页码伪造成已知字段。
- checkpoint 是面向单机、小规模演示的 SQLite 实现，不是多实例生产调度系统。

架构见 [docs/architecture/overview.md](docs/architecture/overview.md)，追踪边界见 [docs/guides/langsmith-observability.md](docs/guides/langsmith-observability.md)。模型驱动的 CLI 子进程必须通过 `/Users/bytedance/.local/bin/traex`；本阶段未运行 standalone Codex。

[MIT License](LICENSE)
