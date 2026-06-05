# Step 006 - Infra 层（7 个 Port 的实现 + 测试基建）

## 1. 本步骤目标

把 domain 层定义的 9 个 Port 中**最关键的 7 个**落地到 `infra/` 层，并搭好"用 Fake 替代真实依赖"的离线测试基建。
本 step 之后，所有上层（app / api）都已经有可注入的具体实现 + 可注入的离线 Mock，
PR-5 Agent 编排可以无网络条件下完整跑通。

剩 2 个 Port 留给后续 step：

- `AuthPort` → step_007（OAuth + JWT，单独一大块）
- `MemoryPort` → step_009（记忆系统 L1~L4 单独一大块）

约束：
**只做加法，不动旧代码。**`api/routes.py`、`service.py`、`retrieval/` 全部保留原样，老 API 端到端继续可用，确保"程序运行成功"这个红线。

## 2. 修改文件

### 新增（生产）

- `infra/__init__.py`
- `infra/storage/__init__.py`
- `infra/storage/_db.py` — `SqliteConnectionPool`（线程局部）+ schema
- `infra/storage/sqlite_user_repo.py` — `UserRepoPort` 实现
- `infra/storage/sqlite_task_repo.py` — `TaskRepoPort` 实现（5 张表：tasks / messages / tool_calls / artifacts；外键 CASCADE）
- `infra/chat/__init__.py`
- `infra/chat/openai_chat.py` — `OpenAIChatAdapter`，包装 `retrieval.generation.chat_client.ChatClient`
- `infra/search/__init__.py`
- `infra/search/embedder_adapter.py` — `EmbedderAdapter`，包装 `retrieval.search.embedder.Embedder`
- `infra/search/hybrid_retriever.py` — `HybridRetrieverAdapter`，包装 `retrieval.search.retriever.Retriever`，把 dict → `Chunk`
- `infra/web/__init__.py`
- `infra/web/duckduckgo.py` — `DuckDuckGoAdapter`，包装 `retrieval.agent.web_searcher.WebSearcher`
- `infra/evidence/__init__.py`
- `infra/evidence/mock_evidence.py` — `MockEvidenceClient`，PR-3 阶段先用确定性 mock 让链路打通

### 新增（测试 / Fake）

- `tests/fakes/__init__.py`
- `tests/fakes/fake_repos.py` — `InMemoryUserRepo` / `InMemoryTaskRepo`
- `tests/fakes/fake_chat.py` — `FakeChat`（按调用序返回预设回复）
- `tests/fakes/fake_embed.py` — `FakeEmbed`（哈希派生稳定向量）
- `tests/fakes/fake_retrieve.py` — `FakeRetrieve`
- `tests/fakes/fake_websearch.py` — `FakeWebSearch`
- `tests/fakes/fake_evidence.py` — `FakeEvidence`
- `tests/infra/__init__.py`
- `tests/infra/test_sqlite_repos.py` — 19 条用例（含 fixture 临时 DB / 契约 isinstance / Cascade 删除）
- `tests/infra/test_service_adapters.py` — 16 条（Chat / Embed / WebSearch / Evidence + 契约 isinstance）
- `tests/infra/test_hybrid_retriever.py` — 10 条（重点测 dict→Chunk 字段映射 / score 方向反转）
- `tests/infra/test_fakes.py` — 7 条契约 + 5 条行为

### 修改

- `requirements.txt` — 把硬钉版本 `chromadb==0.5.23 / openai==1.59.5 / fastapi==0.115.6 / uvicorn==0.34.0 / pydantic==2.10.4` 全部放宽为 `>=, <下个大版本`，与 venv 实装的 chromadb 1.5.9 / openai 2.41.0 / fastapi 0.136.1 / uvicorn 0.49.0 / pydantic 2.13.4 对齐，避免下次 `pip install -r` 又一次卡 chroma-hnswlib MSVC 编译。

### 不动

- `retrieval/`、`processing/`、`ingestion/`、`api/`、`service.py`、`config.py`、`main.py`、`data/chat_db.py`：本 step 完全保留原样。

## 3. 设计决策

1. **适配器模式而非重写**：每个 infra 类持有一个旧实现（`ChatClient` / `Embedder` / `Retriever` / `WebSearcher`），只翻译签名。这样：
   - 老代码全程能跑（API 端到端不变）
   - 重构风险最低，回滚也容易（删掉 `infra/` 即回到 step_005）
   - 旧实现的 ruff 红字留给后续 step 单独清理，不与本 step 混淆
2. **依赖注入（DI）默认参数**：每个 adapter 的构造函数都允许 `client=None`，None 时懒构造默认实例（保留生产环境零配置启动）；测试一律传 stub。这是把测试性建到 day-1 的关键一步。
3. **Fake 与真实 adapter 都验证 `isinstance(x, Port)`**：`@runtime_checkable` 不仅是文档装饰，是约束。任何 PR 加 Port 方法漏写 Fake，会立刻被 7 条契约测试卡住。
4. **`_dict_to_chunk` 把 score 方向统一为"越大越相关"**：旧 `Retriever` 返回 `distance`（越小越相关），domain `Chunk.score` 约定"越大越相关"。adapter 的核心职责之一就是这种"语义反转"，避免上层每次都要记是哪个方向。
5. **5 张表共用一个 SQLite 文件**：`users / tasks / messages / tool_calls / artifacts`，外键 `ON DELETE CASCADE` 保证删除 task 时下挂消息/工件全部清掉。一个 `SqliteConnectionPool` 实例 = 一个 DB；UserRepo / TaskRepo 共享同一个 pool 才能跨表事务（merge_owner 一次性迁移 tasks）。
6. **MockEvidence 用 `factor_id` 哈希派生 label**：保证同一 factor 多次判定返回相同 label（Agent 重放时不会因为 mock 漂移导致结果不稳定）。
7. **WebSearch 适配器命名为 `DuckDuckGoAdapter`，但实际可能走 Bing**：保留旧实现"先试 Bing 再降级 DDG"的多后端策略，重命名只是与 `domain.ports` 中提到的"DuckDuckGoSearcher"候选实现保持一致。
8. **requirements 放宽到 minor 范围**：`chromadb>=1.0,<2.0` 而不是 `==`。理由：本地 Windows + Python 3.12 装 0.5.23 会触发 `chroma-hnswlib` 源码编译要 MSVC；放宽到 1.x 直接走 wheel。代价：未来 chromadb 1.x → 2.x 时需要主动升级测试。

## 4. 核心契约 / 接口

### 4.1 7 个 adapter 一览

| Port | adapter | 实现策略 | 关键转换 |
|---|---|---|---|
| `UserRepoPort` | `SqliteUserRepo` | 直写 SQL | upsert 用 `ON CONFLICT(user_id) DO UPDATE` |
| `TaskRepoPort` | `SqliteTaskRepo` | 直写 SQL | append_message 自动同步 `task.updated_at`；append_tool_call upsert |
| `ChatPort` | `OpenAIChatAdapter` | 委托 `ChatClient.complete` | kwargs 透传 temperature / max_tokens |
| `EmbedPort` | `EmbedderAdapter` | 委托 `Embedder.embed_texts` | 直接透传 |
| `RetrievePort` | `HybridRetrieverAdapter` | 委托 `Retriever.retrieve` | dict → Chunk；score = rerank > rrf > 1-distance |
| `WebSearchPort` | `DuckDuckGoAdapter` | 委托 `WebSearcher.search` | dataclass `WebSearchResult` → domain `WebResult`；空 url 跳过 |
| `EvidencePort` | `MockEvidenceClient` | 自实现 | label 由 factor_id ord-sum 模 3 决定；rationale 暴露 context_keys |

### 4.2 测试基建

- `tests/fakes/`：7 个 fake，全部实现对应 Port，可被任何上层测试 `import` 重用。
- `tests/infra/test_fakes.py`：7 条契约 + 5 条行为，确保 fake 自身不会与 Port 漂移。
- `tests/infra/test_sqlite_repos.py`：使用 `tmp_path` fixture 起独立文件 DB，不污染真实数据。

## 5. 与外部服务的关系

| 类型 | 外部依赖 | 测试是否真的访问 |
|---|---|---|
| LLM | OpenAI 兼容 API（智谱 GLM / OpenAI / Ollama） | ❌ 全部走 `_StubChatClient` |
| Embedding | 同上 | ❌ 走 `_StubEmbedder` |
| 向量库 | ChromaDB（本地持久化） | ❌ 走 `_StubRetriever` |
| BM25 | rank-bm25（内存） | ❌ 同上 |
| Reranker | sentence-transformers Cross-Encoder | ❌ 同上 |
| 搜索 | Bing / DuckDuckGo HTML | ❌ 走 `_StubSearcher` |
| Evidence | （未来）HTTP | ❌ 当前就是 mock |
| SQLite | 本地文件 | ✅ 测试用 `tmp_path` 创建临时 DB |

→ **整个 infra 测试套件无外部 IO 依赖**，CI 重启后随时跑得通。

## 6. 当前实现范围

已实现：

- 7 个 adapter（覆盖 7 个 Port），每个带契约测试 + 行为测试
- 7 个 Fake（覆盖 7 个 Port），可被上层测试 import
- SQLite 5 表 schema 幂等初始化 + 线程局部连接池
- requirements 与 venv 对齐，无版本冲突

未实现（按计划留给后续 step）：

- ❌ `AuthPort` 实现（GitHub OAuth + JWT） → step_007
- ❌ `MemoryPort` 实现（L1~L4 记忆） → step_009
- ❌ `EvidencePort` 真实 HTTP 客户端 → 待 schema-evidence-risk-profiling 服务上线
- ❌ `RetrievePort` 的 corpus="user_docs" / owner_id / filters 下推到底层 → 等 PR-5 接入

## 7. 暂未实现 / TODO

- ⏳ step_007：实装 `AuthPort` 的 GitHub OAuth + 匿名 + JWT
- ⏳ step_008：实装 app 层与 `ComplianceCopilotAgent`，迁移 `api/routes.py` 走新 infra；那时才能删旧的 `retrieval/`
- ⏳ step_009：实装 `MemoryPort` L1~L4
- ⏳ 把 `_dict_to_chunk` 的 owner_id / corpus 过滤真正下推到 `Retriever`（需先扩 `Retriever.retrieve` 的签名）
- ⏳ `MockEvidenceClient` 升级为 `HttpEvidenceClient`

## 8. 测试与验证

### 命令

```powershell
pytest tests/infra tests/domain -q --no-cov   # infra + domain 子集
pytest -q --no-cov                            # 全量基线对比
ruff check infra tests/infra tests/fakes
mypy infra
```

### 输出

```text
# infra + domain 子集
87 passed in 0.32s

# 全量
175 passed, 4 warnings in 14.02s
# 基线 88 → 175（+87 = 35 domain + 19 storage + 16 services + 7 fakes契约 + 5 fakes行为 + 10 retriever）

# ruff
All checks passed!

# mypy infra（仅 infra/ 自身）
0 errors
```

绿色基线：**88 → 175**（+87）。
旧测试 88 条 0 回归（修 requirements 后 `import chromadb` 仍正常）。
infra/ 自身 ruff 0、mypy 0。
