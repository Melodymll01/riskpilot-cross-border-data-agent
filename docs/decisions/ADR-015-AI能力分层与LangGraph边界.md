# ADR-015：AI 能力分层与 LangGraph 边界

- 状态：已接受
- 日期：2026-08-06

## 背景

当前系统同时存在自由 ReAct 问答和固定研究流程。简单问题也可能进入多轮 Agent，
而长流程的状态主要停留在函数局部变量中，不能精确暂停和恢复。若所有能力统一改为
LangGraph，会给简单问答增加无必要的状态和运维成本。

## 决策

AI 能力划分为三类：

1. `EvidenceQAUseCase`：普通应用服务，负责低成本、强范围、强引用的简单问答；
2. `CaseAssessmentGraph`：LangGraph 工作流，负责案件事实、证据、规则和人工审批；
3. `DeepResearchGraph`：LangGraph 工作流，负责多来源监管专题研究。

LangGraph 只属于基础设施编排层，通过 `WorkflowRuntimePort` 接入应用层。领域对象和
规则引擎不依赖 LangGraph。

LangGraph checkpoint 只保存执行状态、对象 ID 和轻量中间结果；案件事实、文档、证据
和 Assessment 仍保存在领域 Repository 中。

## 工具边界

- 模型可以检索法规、读取案件证据和比较文档版本；
- 模型不能删除文档、批准报告、发布规则、修改权限或持久化最终 Assessment；
- LLM 负责提取和解释，确定性规则引擎负责合规门槛计算；
- 原始思维链不对外展示，只展示阶段、工具、证据和人工动作事件。

## 结果

- 简单问答保持低延迟和低成本；
- 只有需要恢复和人工介入的长流程使用 LangGraph；
- 可以独立评测 Evidence QA 和两张 Graph；
- 后续替换编排框架不会污染领域模型。
