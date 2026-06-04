# ADR-001: 不引入 LangChain / LlamaIndex 等编排框架

- 状态: accepted
- 日期: 2026-06-04

## 背景

构建一个对话式 Agent 项目，业界主流路径是用 LangChain / LlamaIndex / LangGraph 做编排。
作为秋招 Agent 项目，需要在"上手快"和"展示工程能力"之间做权衡。

## 决策

**不引入 LangChain 等高层编排框架**，自行编写检索流水线、Agent 主循环、记忆系统。

## 后果

**正面**：
- 每一行代码都可解释，避免"黑盒调用"，面试可深入到 Prompt/Token/重排细节
- 依赖图清晰，离线测试容易（不需要 mock 框架内部）
- 不被框架版本升级 break；不引入额外学习成本
- 4 层架构能落地干净，Port/Adapter 边界由我们自己定义

**负面**：
- 部分胶水代码需要自己写（如多步规划、tool calling 协议）
- 错过框架内置的某些便利（如内置 retriever、debugging UI）

## 备选方案

- **LangChain**：生态最大，但抽象层多、源码沉重、版本不稳定；否决
- **LlamaIndex**：检索导向，但 Agent 能力弱；否决
- **LangGraph**：与本项目"自实现 ReAct"重叠，未来可在单独沙盒分支做对照

## 关联

- [ADR-006: 4 层架构](ADR-006-4-layer-architecture.md)
