# AI Agent / AI 全栈简历片段（RiskPilot 当前实现）

> 项目部分按 2026-10-27 最终架构核对。仓库已完成 `services/control-plane` + `services/agent-runtime` 双服务迁移：Go 拥有认证、会话、记忆、文件目录、run 和最终消息/终态，Python 只负责内部 Agent、RAG、材料计算与证据校验。下面的项目描述可用于当前简历。

## 个人总结

27届北邮硕士，曾在字节跳动、Momenta 实习，方向为 AI Agent、RAG、Tool Calling、Agent 评测与 AI 应用工程化。具备从业务问题拆解、后端接口设计、工具调用、状态管理到效果验证的端到端实践经验。主力语言为 Python，在 RiskPilot 中实现「前端 + Go 业务控制面 + Python AI 运行时」架构：Go 管理业务数据和运行终态，Python 执行 Agent、RAG 与证据校验。求职方向为 AI 应用研发、Agent 工程化、后端平台、AI 全栈、研发效能/质效平台。

## 技能栈

- 编程语言：Python，Go 基础，TypeScript/JavaScript 基础；熟悉数据结构、算法、操作系统、计算机网络、数据库基础。
- 后端开发：熟悉 FastAPI、RESTful API、SSE 流式输出、异步任务、状态管理、接口联调与错误处理；使用 Go + Gin 实现业务控制面、OAuth/JWT、会话/记忆/run 管理、终态事务与流式转发。
- 前端开发：使用 HTML/CSS 和原生 JavaScript 实现聊天工作台、历史会话、附件上传与选择、记忆管理和引用展示，完成 REST API/SSE 联调与 Playwright 浏览器验证。
- AI 应用工程：熟悉 RAG、Embedding、向量检索、Tool Calling、Function Calling、Agent Workflow、Prompt Engineering、结构化输出与证据链生成。
- 工程化能力：熟悉 Git、Docker、Linux、SQLite/MySQL 基础、Redis 基础、日志追踪、评测回归、Bad Case 分析与可观测性设计。
- AI Coding：熟练使用 Codex、Claude Code、Cursor 等 AI 编程工具辅助需求拆解、代码生成、调试、测试与文档沉淀。

## 项目经历：RiskPilot｜跨境数据合规 Agent 全栈应用

**技术栈：** HTML/CSS、JavaScript、Python、Go、FastAPI、Gin、LangChain、LangGraph、Chroma、SQLite、SSE、Playwright。

面向企业出海的数据合规咨询场景，构建「Web 前端 + Go control plane + Python agent runtime」全栈应用，支持法规与用户材料检索、对话式风险分析、长期记忆和引用溯源，打通材料上传、工具调用、结果核验与前端展示流程。

- **前端交付：** 使用 HTML/CSS 与原生 JavaScript 构建聊天工作台，完成历史会话、附件上传与选择、记忆开关及增删改、引用溯源展示；基于 SSE 展示工具过程与待核验草稿，以服务端运行终态处理完成、取消和失败，并通过 Playwright 验证浏览器交互链路。
- **Agent 与 RAG：** 基于 LangChain `create_agent` 实现法规检索、用户材料检索和风险分析工具调用，结合查询改写、BM25/向量召回与 RRF 融合；在检索排序前约束账号和附件范围，支持基于指定材料的多轮问答。
- **回答可信性：** 实现“来源回读 → Claim 结构校验 → 独立语义支持校验”链路，核对附件版本、页码、SHA 与原文引用；过滤无依据结论，证据不足时返回部分回答、澄清或拒答。
- **长期记忆：** 由 Go 按账号保存用户原话、来源、有效期与 revision，并在每轮固定记忆快照；Python 仅做相关性排序和上下文装配，Go 在恢复和最终提交前复核版本，阻止失效记忆参与回答。
- **执行可靠性：** Go 管理 run、attempt、取消、事件游标和最终消息/终态事务；Python 用 LangGraph checkpoint 恢复 AI 计算并返回有序事件。迟到提案、版本漂移或取消后的结果不能覆盖 Go 终态。
- **Go/Python 协作：** 将 Go 业务控制面与 Python AI 运行时分别收敛到 `services/control-plane` 和 `services/agent-runtime`；Go 构造不可变运行快照，Python 执行 Agent/RAG/Evidence QA 并返回结果提案，Go 复核权限和版本后原子提交最终消息与终态。

### 岗位侧重与可选补充

- **投 Agent 应用研发：** 顺序建议为 Agent 与 RAG、回答可信性、长期记忆、执行可靠性、Go/Python 协作、前端交付。前端保留为正式条目，体现产品交付能力。
- **投 AI 全栈：** 顺序建议为 Go/Python 协作、前端交付、Agent 与 RAG、回答可信性、长期记忆、执行可靠性。篇幅有限时可合并回答可信性与 Agent/RAG，保留前端条目。
- **验证体系（篇幅允许时补充）：** 将 Ruff、mypy、pytest、Go vet/竞态测试和浏览器集成验证纳入 CI，覆盖认证隔离、记忆失效、运行恢复和流式异常；以真实模型的固定风险场景单独验证回答与引用链路。

### 面试时的实现边界

1. **Go 当前做了什么？** `services/control-plane` 是业务后端：拥有认证、会话、消息、记忆、文件目录、run、取消/恢复和最终终态；`services/agent-runtime` 不写这些业务数据，只返回 AI 执行事件与结果提案。
2. **LangGraph 是怎么接入的？** LangChain `create_agent` 使用底层 LangGraph 的图执行和 checkpoint；项目补充业务 run 状态、预算、恢复权限与事务校验。当前没有单独手写 `StateGraph`。
3. **记忆是不是向量 RAG？** 当前是结构化字段与关键词召回。姓名、职业等受限个人问答直接使用有来源的档案值；复杂问题进入 Agent。长期记忆尚未接入向量语义召回。
4. **数据库与运行规模是什么？** Go 管理 SQLite 业务库，Python 使用独立 SQLite checkpoint 和 Chroma 索引；材料计算在有界执行器中运行。本项目没有宣称 Redis/MySQL、可靠分布式队列或多实例生产调度已经落地。
5. **效果与验证如何区分？** 2026-10-26 的工程回归记录为 Python 1055 passed / 1 skipped，Go vet 与竞态测试通过。这是结构迁移前后的回归证据；真实风险验证只有一个固定场景，不能作为法规准确率或性能改善指标。真实 GitHub 回调及完整容器部署尚未验收。

### 核对依据

- [当前架构与状态边界](../architecture/overview.md)
- [Go/Python 历史验证记录](../validation/go-python-phase1.md)
- [记忆 V2 验证与召回边界](../validation/memory-v2.md)
- [运行控制与浏览器流式验证](../validation/phase3-runtime-control.md)

以上测试数量引用已有验证记录，本次简历编辑未重新运行测试。

## 实习经历改写建议

### 字节跳动｜AI Agent 质效研发实习生

- 参与 AI Agent Skill 评测与回归体系建设，围绕工具调用、任务执行、页面自动化和结果校验构建可复用评测流程。
- 基于 Playwright 封装自动化执行与验证能力，支持 Agent 在真实任务中的执行轨迹采集、失败定位和结果复核。
- 设计通过率、耗时、稳定性、失败类型等指标，沉淀 Bad Case 分析与回归机制，辅助定位工具调用失败、环境异常和模型输出不稳定等问题。
- 参与 Agent 运行链路的日志分析和问题归因，提升评测过程的可观测性与问题定位效率。

### Momenta｜AI 全栈 / Agent Workflow 实习生

- 参与 AI Workflow 到代码化 Agent 的工程化改造，将原有 Dify Workflow 能力拆解为 Router、Tool、Agent 执行链路，提升流程可控性和可维护性。
- 设计 Router 驱动的 Workflow / Agent 双模式执行逻辑，根据任务类型选择确定性流程或 Agent 推理执行，平衡灵活性与稳定性。
- 参与工具调用、接口封装、状态流转和异常处理逻辑开发，支持 Agent 与业务系统的集成。
- 使用 AI Coding 工具辅助代码生成、调试和测试，对模型生成代码进行复核、改造和工程化落地。

## HR / BOSS 简短话术

您好，我是27届北邮硕士，曾在字节、Momenta 实习，方向是 AI Agent、RAG、Tool Calling、Agent 评测和 AI 应用工程化。个人 RiskPilot 项目采用 Go 业务控制面 + Python AI 运行时：Go 管理会话、记忆和运行终态，Python 执行 Agent/RAG 与证据核验，并完成前端交互。想请您看看是否匹配 AI 应用研发或 AI 全栈岗位，感谢。
