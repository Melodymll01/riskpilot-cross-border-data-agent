# Step 001 - v1.1 设计定稿（对话式 Copilot + GitHub OAuth）

## 1. 本步骤目标

把 RagDataOut 从「v1.0 三 Tab + session_id」的方案重写为「v1.1 对话式 Copilot + GitHub OAuth + 匿名双轨」的工程化方案，作为后续 7 个 PR 的唯一设计依据。

本步骤为什么存在：

- 原型 (`service.KnowledgeService`) 是单体硬编码，无法直接面试展示。
- 秋招 Agent 岗需要明确的 Agent 标签（Tool-Use / ReAct / 多步规划），三 Tab 形态弱化了这一点。
- 没有身份层就没有记忆、没有任务、没有多用户隔离，简历项目深度不够。
- 风险画像（schema-evidence-risk-profiling）是独立训练的模型，必须以外部服务形式接入而非内嵌。

它服务于 RagDataOut 的全局架构层：

- 文档层（`docs/experiment_v1.md` v1.1）
- 决策层（`docs/decisions/ADR-001..008`）

它为后续步骤提供的依赖：

- step_002 工程基建：依赖 §13 的 7 PR 拆分与 §11 的文档规范。
- step_004+ domain / infra / app 实现：依赖 §3 的 4 层架构、§4 的端口定义、§5 的记忆模型、§6 的 Tool 化风险画像。
- step_006+ Auth Layer：依赖 §1 §3 §10 的 GitHub OAuth + 匿名双轨方案、§4 的 `AuthPort`/`UserRepoPort`、§5 的 `owner_id` 命名空间策略。

## 2. 修改文件

- `docs/experiment_v1.md`：v1.0 → v1.1 全文重写（~1550 行），新增 §1 Copilot 定位 + 身份模型、§3 4 层架构图、§4 7 个子节（含 Models / Ports / Agent / UseCase / AppContainer / Tool Registry）、§5 owner_id 记忆映射 + merge 迁移、§6 风险画像作为 Tool、§7 Mock-first 测试矩阵、§8 chat + auth API、§9 Copilot UI（思考可见 + Artifact 面板）、§10 GitHub OAuth + JWT 配置、§13 7 PR 路线、§15 遗留问题。

## 3. 设计决策

8 篇 ADR 全部冻结，详见 `docs/decisions/`：

| ADR | 决策 | 关键替代选项 |
|---|---|---|
| 001 | 不引入 LangChain | LangGraph / LlamaIndex |
| 002 | BM25 + 向量 + RRF + Reranker | 只向量 / 只 BM25 |
| 003 | Evidence 模型独立服务（HTTP + mock 双轨） | 内嵌 transformers / 共进程 |
| 004 | Mock-first 测试 | 录制真实 LLM 响应 |
| 005 | 对话式 Copilot（取代三 Tab） | 三 Tab / 单页搜索 |
| 006 | 4 层架构（api/app/domain/infra） | MVC / DDD 六边形 |
| 007 | GitHub OAuth + 匿名双轨 | 仅匿名 / 仅 OAuth / 邮箱密码 |
| 008 | `owner_id` 命名空间前缀 + 强制索引 | 数字 ID / UUID |

## 4. 核心契约 / 接口

身份模型（`docs/experiment_v1.md` §5）：

```text
owner_id ::= "anon:" <uuid>             # 浏览器 localStorage 持久化
           | "github:" <login>          # GitHub OAuth 回调后绑定
           | "google:" <email>          # 预留，未实装
           | "email:" <email>           # 预留，未实装
```

7 PR 拆分（§13）：

| PR | 主题 | 说明 |
|---|---|---|
| PR-1 | 工程基建 | requirements-dev / pytest / ruff / mypy / pre-commit / CI / Makefile / docs |
| PR-2 | domain 层 | models + ports + errors，不动业务代码 |
| PR-3 | infra 层 + 测试基建 | 实现 Port + Fakes + Fixtures + Scenarios |
| PR-4 | Auth Layer | GitHub OAuth + 匿名 + JWT 中间件 + SqliteUserRepo |
| PR-5 | app + Agent | ComplianceCopilotAgent + ToolRegistry + AppContainer |
| PR-6 | 记忆层 L1+L2 by owner_id | 短期对话 + 长期偏好 + merge 迁移 |
| PR-7 | 风险画像 Tool + Copilot UI | risk_profile Tool + 思考可见前端 + Artifact 面板 |

## 5. 与外部模型 / 服务的关系

- **GLM-4-Flash / OpenAI 兼容 / Ollama**：通过既有 `ChatClient` 抽象接入，统一 `ChatPort`。
- **embedding-3 / nomic-embed**：通过 `EmbedderPort` 接入。
- **schema-evidence-risk-profiling (Qwen2.5-7B + LoRA, vLLM:8001)**：通过 `EvidenceClient` Port + 现有 `HTTPEvidenceClient` / `MockEvidenceClient` 接入，作为 Agent 的 `risk_profile` Tool。
- **GitHub OAuth**：通过 `AuthPort` + `GitHubOAuthProvider` 接入；本地开发用 `FakeOAuth` 替身，完全离线可测。

## 6. 当前实现范围

已实现：

- 设计文档 v1.1 全文重写（~1550 行）
- 8 篇 ADR 全部归档

未实现（按设计，留给后续 step）：

- 任何工程基建（step_002）
- 任何 domain / infra / app / api 代码改动
- 任何 Auth Layer 实现
- Copilot UI 与前端改造

## 7. 暂未实现 / TODO

- ❌ PR-1 工程基建（step_002）
- ❌ PR-2 domain 层（step_004 起）
- ❌ Auth Layer 与 GitHub OAuth 真实回调
- ❌ Copilot UI 与 Artifact 面板
- ⚠ Windows 下 Makefile 不可用，需补 `scripts/*.ps1`（PR-7 处理）

## 8. 测试与验证

本步骤仅文档变更，无代码。验证方式：

- ✅ `docs/experiment_v1.md` 全文可读、章节编号闭环
- ✅ `docs/decisions/ADR-001..008.md` 8 篇齐备
- ✅ `docs/architecture/overview.md` 与正文 §3 一致
- ✅ 7 PR 拆分在 §13 内逐项 acceptance 列出
