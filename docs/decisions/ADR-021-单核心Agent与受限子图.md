# ADR-021：采用单核心 Case Assessment Agent，不做自由 Multi-Agent

- 状态：已接受
- 日期：2026-08-17

## 背景

项目已有 Copilot、Deep Research、Evidence QA、Memory 和 Visual Evidence。如果把每个
能力包装成独立 Agent 并让它们自由对话，架构看起来“Agent 很多”，但会增加 token、
延迟、调试和权限边界，且无法证明业务收益。

数据出境合规的正式产物有清晰的责任链：证据计划、候选事实、规则计算、引用验证和人工
审批。它更适合一个受约束的核心 Agent 加确定性服务，而不是角色扮演式协作。

## 决策

只保留一个核心 `Case Assessment Agent`，负责：
- 根据案件状态生成 Evidence Plan；
- 调用 Typed Tool Registry；
- 根据证据充分性、缺失事实和冲突决定下一步；
- 在人工节点 interrupt；
- 恢复后重新读取业务数据库；
- 调用确定性规则引擎；
- 草拟风险说明和整改建议；
- 触发 Claim-Citation 验证；
- 将正式审批留给 Reviewer。

Deep Research 是受限子图：只有证据计划需要外部监管研究时才调用，拥有独立的最大轮次、
工具权限和评测指标。Copilot、Memory、Visual 和 Evidence QA 都是辅助能力，不作为
并列 Agent。

## 为什么这样设计

- Agent 价值体现在“根据环境决定下一步”，而不是 Agent 数量；
- 单一运行状态更容易 checkpoint、恢复、审计和展示；
- 工具权限和 token budget 可以集中治理；
- 面试时能够清楚解释主线，而不是描述多个模型互相聊天。

## 何时才允许拆 Agent

只有同时满足以下至少一项才考虑拆分：

1. 需要独立上下文窗口和模型配置；
2. 工具权限必须物理隔离；
3. 有独立可量化成功指标；
4. 生命周期明显不同且能异步独立运行。

即使拆分，也优先使用受限子图或应用服务，不默认采用自由消息协商。

## 备选方案

### Planner/Researcher/Reviewer/Writer 多 Agent

拒绝。Reviewer 不能由模型扮演正式审批者；Planner 与 Writer 可作为同一 Graph 的节点；
Researcher 用受限子图即可。

### 完全固定 DAG

拒绝。证据是否充分、是否补查、是否需要人工确认具有动态性，需要有限 Agent 决策。

## 代价

- 核心 Graph 的 State、工具策略和评测必须设计得更严格；
- 需要避免单 Agent 上下文膨胀；
- 子图调用必须有预算和超时，不能形成隐藏的无限循环。

## 验证

- 材料完整时自动运行到 Reviewer；
- 缺失事实时暂停并可恢复；
- 冲突事实进入 Reviewer，而不是模型自行选择；
- 最大循环、工具调用和 token budget 生效；
- Deep Research 只能在允许阶段和明确条件下调用；
- Agent 永远不能审批 Assessment。
