# ADR-018：LLM 与确定性规则引擎分离

- 状态：已接受
- 日期：2026-08-17

## 背景

数据出境合规包含两类问题：

- 非结构化问题：材料说了什么、还缺什么证据、不同证据是否冲突、如何解释风险；
- 确定性问题：人数/数量阈值、规则生效日期、事实确认状态、审批条件和状态转换。

LLM 适合处理语言和不完整信息，但同一输入可能产生不同输出。法规门槛和正式审批要求
可复现、可审计、可单测，不能由 Prompt 中的自由文本决定。

## 决策

### LLM 可以做

- Evidence Plan 和检索查询规划；
- Typed Tool 选择；
- 候选事实提取；
- 证据冲突说明；
- 风险解释和整改建议草拟；
- Claim/Citation 结构化生成。

### 确定性代码必须做

- Workspace/Case 权限和数据范围；
- CaseFact 状态、版本与确认人；
- PolicyRule 生效日期和法规门槛；
- Case/Run/Assessment 状态转换；
- Citation ID、原文、页码、SHA 和引用闭包校验；
- Reviewer/Admin 审批条件；
- 同一 Case 活动 Run 唯一约束。

LLM 输出必须先解析到 Pydantic Schema，再由服务端根据白名单、当前数据库状态和原文
进行复核。`PolicyRuleEngine` 的输出是正式门槛依据，LLM 只能解释该输出，不能覆盖它。

## 为什么这样设计

- 相同 confirmed facts 和规则版本必须得到相同路径；
- 模型升级不能悄悄改变历史法规判断；
- 面试和生产都能说明“AI 的不确定性被限制在哪里”；
- 评测可以分别验证模型效果和确定性协议。

## 备选方案

### 让 LLM 直接生成最终风险等级

拒绝。结果不可稳定复现，难以解释具体规则版本，且容易被 Prompt Injection 影响。

### 完全不用 LLM

拒绝。材料提取、证据规划、冲突解释和自然语言报告需要处理开放文本，纯规则维护成本
过高且召回不足。

## 代价

- 需要同时维护字段 Schema、PolicyRule 和 Prompt；
- LLM 提取的新字段必须先进入白名单与 Reviewer 流程；
- 最终报告需要把规则输出转换成自然语言，并保持 Claim-Citation 一致。

## 验证

- 同一 confirmed facts + ruleset 多次运行结果一致；
- 未确认 Fact 不能触发规则；
- 模型无法直接批准 Assessment；
- 非法 Schema、未知字段和伪造 Citation fail closed；
- 模型升级前后分别运行 Agent Eval，规则单测结果不变。
