# RiskPilot 架构总览

## 当前架构

当前生产入口仍为 `/api/v2`，采用四层结构：

```text
api → app → domain
       ↑
     infra
```

- `domain`：纯领域模型和 Port；
- `app`：用例、Agent 编排和依赖装配；
- `infra`：数据库、检索、模型、记忆、鉴权等适配器；
- `api`：FastAPI 路由、鉴权依赖和 SSE；
- `frontend`：无构建步骤的浏览器端工作台。

现有 QA 使用自研 ReAct，Research 使用旧 Agentic RAG 适配器。两者在 V2 完成替代前
继续由 `/api/v2` 提供服务。

## V2 目标架构

```text
Frontend
  → API V3
    → Application Use Cases
      → Domain
      → WorkflowRuntimePort
        → LangGraph Runtime
      → Infrastructure Ports
        → SQL / Object Store / Retrieval / LLM
```

V2 的关键边界：

1. Evidence QA 是普通应用服务，不依赖 LangGraph；
2. Case Assessment 和 Deep Research 使用 LangGraph；
3. LangGraph 只保存执行状态，不取代领域 Repository；
4. 合规门槛由版本化规则引擎计算；
5. 文档、事实、证据和 Assessment 均为一等领域对象；
6. `/api/v2` 与 `/api/v3` 在迁移期并行运行。

完整产品和技术设计见：

- `docs/design/riskpilot-v2.md`
- `docs/design/v2-migration-baseline.md`
- `docs/decisions/ADR-014-v2-增量迁移与领域内核.md`
- `docs/decisions/ADR-015-AI能力分层与LangGraph边界.md`
- `docs/decisions/ADR-016-案件证据与规则快照.md`
