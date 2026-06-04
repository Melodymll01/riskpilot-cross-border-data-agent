# 架构总览

> 详细设计请见 [`../experiment_v1.md`](../experiment_v1.md)。本文是精简索引版本。

## 一句话定位

数据出境合规 Copilot —— 对话式 Tool-Use Agent，集成自研 evidence-state LoRA 模型 + 混合检索 + 显式规则引擎 + Schema-guided 风险画像。

## 4 层 + Agent 编排 + 身份层

```
L4 api/        FastAPI 路由 + JWT 认证中间件
L3 app/        ComplianceCopilotAgent + ToolRegistry + Use Cases
L2 domain/     纯 Protocol + dataclass，零外部依赖
L1 infra/      retrieval / generation / embedding / evidence /
               memory / auth / ingestion / processing / storage
```

依赖方向：L4 → L3 → L2，L1 → L2，**L3 ↛ L1**。

## 核心抽象

| 抽象 | 文件 | 作用 |
|---|---|---|
| `ChatPort` / `EmbedPort` / `RetrievePort` | `domain/ports.py` | LLM / 向量 / 检索 |
| `EvidencePort` | `domain/ports.py` | LoRA 证据模型客户端 |
| `MemoryPort` | `domain/ports.py` | 4 层记忆 |
| `AuthPort` / `UserRepoPort` / `TaskRepoPort` | `domain/ports.py` | 身份与持久化 |
| `ToolSpec` + `ToolRegistry` | `app/agent/tools.py` | Agent 能力声明式注册 |
| `ComplianceCopilotAgent` | `app/agent/copilot.py` | ReAct 主循环 |

## 身份模型

- 匿名：`owner_id = "anon:{uuid}"`，前端 localStorage 持久化
- 登录：`owner_id = "github:{login}"`，httpOnly JWT cookie 30 天
- 切换登录时通过 `UserRepoPort.merge_owner` 一并迁移所有数据

## 关键决策一览

| ADR | 决策 |
|---|---|
| [ADR-001](../decisions/ADR-001-no-langchain.md) | 不用 LangChain，自实现编排 |
| [ADR-002](../decisions/ADR-002-bm25-rrf-fusion.md) | BM25 + 向量 + RRF + Rerank |
| [ADR-003](../decisions/ADR-003-evidence-as-external-service.md) | LoRA 证据模型作为独立服务 |
| [ADR-004](../decisions/ADR-004-mock-first-testing.md) | Mock-first 离线测试 |
| [ADR-005](../decisions/ADR-005-conversational-copilot-form.md) | 对话式 Copilot 形态 |
| [ADR-006](../decisions/ADR-006-4-layer-architecture.md) | 4 层 hexagonal 架构 |
| [ADR-007](../decisions/ADR-007-github-oauth-with-anonymous.md) | GitHub OAuth + 匿名 |
| [ADR-008](../decisions/ADR-008-owner-id-tenancy.md) | owner_id 统一身份键 |

## 技术栈

FastAPI · Chroma · rank-bm25 + jieba · BAAI/bge-reranker-base · 智谱 GLM-4-Flash / OpenAI 兼容 / Ollama · Qwen2.5-7B + LoRA（外部 vLLM）· pytest + responses + httpx · ruff + mypy · GitHub Actions
