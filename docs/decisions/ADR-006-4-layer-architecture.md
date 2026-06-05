# ADR-006: 4 层 Hexagonal 架构（api / app / domain / infra）

- 状态: accepted（augmented by ADR-009 / ADR-010）
- 日期: 2026-06-04
- 后续补充：
  - [ADR-009: Closure Router + Container DI](ADR-009-closure-router-container-di.md)（Step 010 落地路由与容器的绑定方式）
  - [ADR-010: Strangler Fig v1/v2 共存](ADR-010-strangler-fig-v1-v2.md)（Step 010-011 落地老 v1 路由保留策略）

## 背景

原型代码把 LLM 客户端、检索、记忆、业务编排揉在 `service.KnowledgeService` 一个类，新增能力（如风险画像）需要改三个文件，测试无法替换实现。需要明确的层次与依赖方向。

## 决策

引入 4 层架构：

```
L4 api/      路由层（HTTP 边界、认证中间件、异常映射）
L3 app/      能力编排层（Agent + ToolRegistry + Use Cases）
L2 domain/   领域层（Protocol + dataclass，零外部依赖）
L1 infra/    基础设施层（具体实现，可替换）
```

**强制依赖方向**：
- L4 → L3 ✅
- L3 → L2 ✅，L3 ↛ L1 ❌（只通过 Port）
- L2 ↛ 任何外部 ❌（必须纯 Python）
- L1 → L2 ✅（实现 Port）

## 后果

**正面**：
- 任何外部依赖（LLM、Embedding、Reranker、Evidence、Web、OAuth）都可在测试中替换
- 业务逻辑（Use Case / Agent）完全离线可测
- 加新能力 = 加一个 Tool / Use Case，几乎不改其它层
- 团队协作时，"接口先行"减少冲突

**负面**：
- 文件数变多，初学者需要时间理解
- 简单功能也要走完 Port → Adapter，有一定样板代码

## 备选方案

- **三层（Web / Service / Data）**：依赖方向不明确，仍可能 Service 直接 `from openai import ...`，否决
- **DDD 战术细分**（Aggregate / Repository / Domain Service）：本项目无聚合根概念，过度设计

## 关联

- [ADR-001: 不引入 LangChain](ADR-001-no-langchain.md)
- `experiment_v1.md` §3 / §4
