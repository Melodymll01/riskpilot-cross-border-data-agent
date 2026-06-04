# Step 005 - PR-2 Domain 层

## 1. 本步骤目标

为整个项目建立六边形架构的"中心"：**纯数据模型 + 端口契约**，零外部依赖、零业务侵入。
所有后续 PR（infra / app / api）都必须按本层定义的 Model 与 Port 实现，不允许绕过。

它服务于：

- 全局架构层（`docs/architecture/overview.md` §4 层 1 = domain）
- 所有后续 step 的依赖契约源

它为后续步骤提供的依赖：

- step_006+ infra：实现 9 个 Port 的具体适配器（Chroma / BM25 / OpenAI / vLLM / SQLite 等）
- step_007 Auth Layer：实现 `AuthPort` / `UserRepoPort` + JWT 中间件
- step_008 app+Agent：依赖 `Task` / `Message` / `ToolCall` / `Artifact` 与 `RetrievePort` / `ChatPort` / `EvidencePort`
- step_009 记忆 L1+L2：实现 `MemoryPort`

## 2. 修改文件

5 新增（生产）+ 2 新增（测试）：

- `domain/__init__.py`（新）：统一公开 API，下游只从本模块导入；43 个符号集中导出
- `domain/errors.py`（新）：异常树根 `DomainError` + 13 个子类（Auth / User / Task / Tool / Retrieval / Memory / Evidence / WebSearch）
- `domain/models.py`（新）：`BaseDomainModel` + 11 个 pydantic 模型（User / Task / Message / ToolCall / Artifact / Citation / Chunk / WebResult / EvidenceJudgement / SessionProfile / Fact）+ 5 个 Literal 别名（Provider / TaskState / MessageRole / ToolCallStatus / Corpus）
- `domain/ports.py`（新）：9 个 `@runtime_checkable Protocol`（AuthPort / UserRepoPort / TaskRepoPort / EmbedPort / ChatPort / RetrievePort / EvidencePort / WebSearchPort / MemoryPort）
- `tests/domain/__init__.py`（新）：空
- `tests/domain/test_models.py`（新）：35 条单测（字段约束 / Literal / frozen / extra-forbid / JSON round-trip）

不修改任何已存在的业务代码。

## 3. 设计决策

1. **pydantic v2 而非 dataclass**：与项目既有风格一致；天然 JSON round-trip；`Literal` / `ge` / `le` / `min_length` 校验零成本；mypy strict 友好。
2. **统一基类 `BaseDomainModel`（frozen + extra="forbid"）**：所有 domain 模型不可变、不接受未知字段，避免后续 infra 层"偷偷塞字段"破坏契约。
3. **时间戳统一 `float` (Unix epoch seconds)**：跨进程 / 跨 JSON 边界最简方案；不混合 datetime / 字符串。
4. **`owner_id` 命名空间前缀仅约束、不强校验**（ADR-008）：约束写在文档与 ADR；不在模型层用 regex 拒绝，保留为 infra 层"翻译入口"的职责（避免 domain 层耦合具体 provider 列表）。
5. **9 个 Port 不拆得太细**：例如检索三段式（BM25 / 向量 / RRF / Reranker）合并为单一 `RetrievePort`，由 infra 层内部组合；domain 不感知组合策略。这与 PhonePilot 项目的 `AndroidEnv` 抽象同源。
6. **`MemoryPort` 一次性覆盖 L1~L4**：避免后续多次扩展接口；L2 摘要的触发阈值 `threshold=20` 作为缺省值暴露给 app 层。
7. **不暴露 `BM25Port` / `VectorStorePort` / `RerankerPort`**：这些是 infra 内部组合细节，外界只见 `RetrievePort`。`docs/experiment_v1.md` §4.2 也未把它们列入 ports；如未来需要单测某子组件，再向 infra 内部添加 internal Protocol。
8. **`MemoryError` 故意 shadow builtins.MemoryError**：domain 异常树要求 `isinstance(e, DomainError)` 收敛；在 `# noqa: A001` 注释下显式声明此覆盖。
9. **Ports 全部 `@runtime_checkable`**：方便 fake/mock 用 `isinstance(fake, ChatPort)` 校验；运行时无开销。

## 4. 核心契约 / 接口

### 4.1 数据模型一览

| 模型 | 关键字段 | 用途 |
|---|---|---|
| `User` | user_id / provider / display_name / created_at / last_active_at | 统一身份 |
| `Task` | task_id / owner_id / state / user_goal / collected_facts | 替代 conversation 概念 |
| `Message` | msg_id / task_id / role / content / tool_call_id? / citations | 对话历史 |
| `ToolCall` | tool_call_id / tool_name / input_json / output_json? / status / duration_ms | Agent 工具调用快照 |
| `Artifact` | artifact_id / artifact_type / payload_json | Agent 中间产出 |
| `Citation` | source_type / source_name / title / source_url? / text_snippet | 引用来源 |
| `Chunk` | chunk_id / text / source_* / score / metadata | 检索片段 |
| `WebResult` | title / url / snippet | 联网搜索单条 |
| `EvidenceJudgement` | factor_id / label / rationale / confidence | 风险画像判定 |
| `SessionProfile` | owner_id / facts / updated_at | L3 用户画像 |
| `Fact` | fact_id / owner_id / text / tags | L4 语义事实 |

### 4.2 端口一览（9 个）

| Port | 关键方法 | infra 候选实现（PR-3+） |
|---|---|---|
| `AuthPort` | begin_oauth / complete_oauth / issue_jwt / verify_jwt / create_anonymous | `GitHubOAuthProvider` + `JwtIssuer` + `AnonymousIssuer` |
| `UserRepoPort` | upsert / get / merge_owner / touch | `SqliteUserRepo` |
| `TaskRepoPort` | create / get / list_for_owner / update / delete / append_message / list_messages / append_tool_call / append_artifact | `SqliteTaskRepo` |
| `EmbedPort` | embed | `OpenAIEmbedder` / `NomicEmbedder` |
| `ChatPort` | chat | `OpenAIChat` / `GLMChat` / `OllamaChat` |
| `RetrievePort` | retrieve | `HybridRetriever`（内部组合 BM25 + Chroma + RRF + Reranker） |
| `EvidencePort` | judge | `HTTPEvidenceClient` / `MockEvidenceClient` |
| `WebSearchPort` | search | `DuckDuckGoSearcher` / `BingSearcher` |
| `MemoryPort` | append_message / recent_messages / get_summary / maybe_summarize / get_profile / update_profile / recall_semantic | `SqliteMemory` + `ChromaSemanticMemory` |

## 5. 与外部服务的关系

无。本层不允许 `import` 任何 infra 模块或第三方服务客户端。
所有外部交互的入口在本层定义为 Port，由 PR-3+ 的 infra 层实现。

## 6. 当前实现范围

已实现：

- 11 个数据模型 + 5 个 Literal 别名 + 14 个异常类 + 9 个 Port Protocol
- 35 条单测，全部通过
- ruff 0 红字，mypy strict 0 红字

未实现：

- ❌ 任何 infra 层适配器（PR-3+）
- ❌ app 层用例 / Agent 编排（PR-5）
- ❌ Port 的运行时契约测试（留给 PR-3 的 infra 测试用 isinstance + 调用验证）

## 7. 暂未实现 / TODO

- ⏳ PR-3：基于本层 9 个 Port 实现 infra 适配器与 Fakes
- ⏳ PR-4：基于 `AuthPort` / `UserRepoPort` 实现 GitHub OAuth + 匿名 + JWT
- ⏳ PR-5：基于全部 Port + `Task` / `Message` 实现 `ComplianceCopilotAgent`
- ⏳ 若后续 infra 实现暴露出需要 domain 感知的字段（如 `Chunk.score` 方向反转 / `EvidenceJudgement` 字段不够），需回头扩本层

## 8. 测试与验证

### 命令

```powershell
pytest tests/domain -q --no-cov
pytest -q --no-cov
ruff check domain tests/domain
mypy domain
```

### 输出

```text
# pytest tests/domain
35 passed in 0.12s

# pytest 全量
123 passed, 4 warnings in 8.03s   # baseline 88 → 123, +35

# ruff
All checks passed!

# mypy
Success: no issues found in 4 source files
```

绿色基线提升：**88 → 123**。无回归。
