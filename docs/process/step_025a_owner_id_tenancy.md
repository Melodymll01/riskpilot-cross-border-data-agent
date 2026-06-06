# Step 025a — 私人知识库 owner_id 多租户隔离落地

> ADR-008 早在 2026-06-04 就把 `owner_id` 定为统一身份键与数据隔离边界。Step 008 起任务（`Task.owner_id`）和会话（`Conversation.owner_id`）已经实装；本步把同样的隔离推到知识库（`KbChunk` / Chroma metadata），让普通登录用户能拥有**自己的**私人 KB，而不再共享同一片"公共法规库"。

## 1. 目标

- 让 `KbChunk` 携带 `owner_id`，`None` 表示公共库（admin 上传 / Step 016 之前的历史数据）
- 解锁普通登录用户的"上传 / 删除自己文档"能力——之前 Step 019 把所有写端点锁死成 admin-only
- 在不引入 per-user Chroma collection 的前提下，做到「读时按可见性过滤、写时按 owner 维度幂等覆盖、启动时把历史无 owner 数据迁成公共」三件事一气呵成
- 前端 KB 面板增加 `scope` 三态切换（全部 / 公共 / 我的）+ owner 徽章；admin 上传增加「入公共库」复选框

## 2. 改动清单

### 后端 14 文件

| 文件 | 改动 |
|---|---|
| [domain/models.py](../../domain/models.py) | `Chunk` / `KbDocument` / `KbChunk` 全部加 `owner_id: str \| None = None` |
| [processing/metadata.py](../../processing/metadata.py) | 顶部加 `PUBLIC_OWNER_MARKER = "__public__"`；`ChunkWithMetadata` 加 `owner_id`；`to_metadata_dict()` 输出物化 marker（`None → "__public__"`），保证 Chroma where 子句可命中（Chroma 不支持 `{"owner_id": None}` 过滤） |
| [retrieval/search/vector_store.py](../../retrieval/search/vector_store.py) | helper `_build_owner_clause(owners)` / `_and_clause(*clauses)`；`_Unset` sentinel 类区分「未传 owner_id」与「显式传 None=public」；`query` / `keyword_search` / `get_all_sources` / `delete_by_source` / `get_neighbor_chunks` 全部加 `owners`/`owner_id` 关键字；`get_all_sources` 按 `(source_name, owner_id)` 双键聚合；新增 `migrate_owner_id_marker() -> int` 启动幂等迁移 |
| [infra/kb/chroma_kb_repo.py](../../infra/kb/chroma_kb_repo.py) | 同样 `_UnsetType` sentinel；`list_documents` / `get_document` / `delete_document` 加 `owners` / `owner_id`；`add_chunks` 按 `(source_name, owner_id)` 维度先删后插（不再粗暴按 `source_name` 全删，避免普通用户上传同名文件把公共库同名文档抹掉）；委托 `migrate_owner_id_legacy()` 给 vector_store；`_to_kb_document` / `_to_chunk_with_metadata` 透传 owner_id |
| [infra/kb/unified_loader_adapter.py](../../infra/kb/unified_loader_adapter.py) | `load_file` / `load_web` / `_raw_to_kb_chunks` 全链加 `owner_id` |
| [tests/fakes/fake_kb_repo.py](../../tests/fakes/fake_kb_repo.py) | `_store` 改为 `dict[(source_name, owner_id), list[KbChunk]]`；所有方法支持 owners 过滤；同样的"先删后插"语义 |
| [tests/fakes/fake_document_loader.py](../../tests/fakes/fake_document_loader.py) | 加 `owner_id`；调用记录里也记 owner_id |
| [domain/ports.py](../../domain/ports.py) | `KbDocumentRepoPort` 加 `owners` / `owner_id`；`DocumentLoaderPort` 同步加 |
| [app/use_cases/kb_management.py](../../app/use_cases/kb_management.py) | 顶部加 `Scope = Literal["public", "mine", "all"]` + `_resolve_owners(viewer_id, viewer_is_admin, scope)` helper（admin scope=all → None=不过滤；普通用户 scope=all → `[None, viewer_id]`=公共+自己）；`list_documents` / `get_document` 加 viewer 上下文；`delete_document` 加 `actor_id` / `actor_is_admin`（非 admin 必须有 actor_id，否则抛 ValueError）；`ingest_file` / `ingest_web` 加 `owner_id`，透传给 loader |
| [api/v2/schemas.py](../../api/v2/schemas.py) | `KbDocumentOut` 加 `owner_id: str \| None = None` |
| [api/v2/documents.py](../../api/v2/documents.py) | 全员 `require_owner`（移除 require_admin import）；`admin_set` + `_is_admin(uid)` helper；3 GET 端点加 `scope: Literal["public","mine","all"] = Query("all")`；POST 加 admin-only `as_public: bool = Query(False)`：admin `target_owner = None if as_public else owner_id`（保守默认入私人，与前端 UI "不勾就是自己" 一致）；普通用户 `target_owner = owner_id`（强制私人，忽略 `as_public`）；DELETE 由 use case 内部判 owner 一致性 + 数据库返回 0 时统一 404（不暴露存在性） |
| [retrieval/search/bm25_index.py](../../retrieval/search/bm25_index.py) | `_owner_matches(meta_owner, viewer_set)` helper（折叠 `PUBLIC_OWNER_MARKER ↔ None` 等价语义）；`search` 加 `viewers` 参数，候选 `top_k*4` 再后置过滤；BM25 全局索引不拆，靠后置过滤减少索引重建 |
| [retrieval/search/retriever.py](../../retrieval/search/retriever.py) | `retrieve` 加 `viewers` 透传给 vector_store / bm25_index / get_neighbor_chunks；`_expand_context` 同样加 |
| [infra/search/hybrid_retriever.py](../../infra/search/hybrid_retriever.py) | `retrieve` 把 `owner_id` 翻译为 `viewers=[None]`（未登录）或 `[None, owner_id]`（登录用户）；`_dict_to_chunk` 把 metadata 里的 PUBLIC_OWNER_MARKER 折回 `None` 给 domain |
| [app/container.py](../../app/container.py) | 新增 `startup_migrations() -> int`：hasattr 检查后调 `kb_repo.migrate_owner_id_legacy()`；失败仅 logger.warning 不影响启动 |
| [main.py](../../main.py) | `import asyncio`；`lifespan` 内 `await asyncio.to_thread(container.startup_migrations)` |

### 前端 5 文件

| 文件 | 改动 |
|---|---|
| [frontend/api.js](../../frontend/api.js) | `documents.list` / `stats` / `get` 加 `scope` 参数；`ingestFile` / `ingestWeb` 加 `{ asPublic }` 选项 |
| [frontend/kb.js](../../frontend/kb.js) | `_currentScope` 模块状态；scope toggle bindUI；`renderOwnerBadge`（公共=绿 / 我的=蓝 / 他人=灰）；`syncAdminUI` 同步 `as_public` 复选框（admin 默认勾上，UI 层默认入公共）；行级删除权限（`owner_id == myId` 或 `isAdmin`）；refresh 内调 `syncAdminUI` |
| [frontend/index.html](../../frontend/index.html) | `.kb-scope-toggle` 三按钮（全部=is-active / 公共 / 我的）；`#kb-file-aspublic-wrap` / `#kb-web-aspublic-wrap` 复选框；表格加 `.kb-col-owner` owner 列（7 列） |
| [frontend/style.css](../../frontend/style.css) | `.kb-scope-toggle` / `.kb-scope-btn` / `.kb-badge-public/mine/other` / `.kb-aspublic-wrap` 新增样式 |
| [frontend/app.js](../../frontend/app.js) | `applyKbGate` 让 KB 入口对所有登录用户显示（写区按钮也对所有登录用户显示，UI 不再以 admin 作为唯一信号；admin 仅决定 `as_public` 默认值与他人删除按钮）；只读 banner 永久隐藏 |

### 新增测试

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| [tests/infra/test_vector_store_owner.py](../../tests/infra/test_vector_store_owner.py) | 17 | `_build_owner_clause` 4 case + `query` owners 过滤 4 case + `get_all_sources` 按 owner 聚合 2 case + `delete_by_source` 三态（默认全删/仅 public/仅某 user）3 case + `migrate_owner_id_marker` 幂等 + 回填 legacy 2 case |
| [tests/api/test_documents.py::TestOwnerScope](../../tests/api/test_documents.py) | 11 | scope=public/mine/all 在 admin / 普通用户视角下的可见集合 + GET 详情 owner 守门 + 普通用户上传强绑自己（即便传 `as_public=true` 也忽略） + admin `as_public=true` → owner=None + 普通用户能删自己 / 不能删他人 / 不能删公共（404）+ admin 删任意 |

### 修改测试

| 文件 | 改动 |
|---|---|
| [tests/api/test_documents.py](../../tests/api/test_documents.py) | `_seed_chunks` 加 `owner_id` 参数；`TestAuthGating` 三个非 admin 写测试改为 200 路径（旧 forbidden → 现在 allowed）；admin happy path 期望 `owner_id="github:alice"`（与 API 保守默认对齐） |
| [tests/app/test_kb_management.py](../../tests/app/test_kb_management.py) | `test_delete_returns_count` 走 `actor_is_admin=True`；`test_actor_id_default_when_none` 改为 admin 路径下 actor_id 缺省 → 审计 `system:unknown`；`test_delete_success_records_audit` ingest 时传 `owner_id=actor` 让删时 owner 匹配 |
| [tests/infra/test_chroma_kb_repo.py](../../tests/infra/test_chroma_kb_repo.py) | `_StubVectorStore` 适配 `owners` / `owner_id` 关键字 + 记录 `last_owners` / `migrate_calls`；`delete_calls` 升级为 `list[tuple[str, Any]]` 并更新两处断言 |
| [tests/infra/test_hybrid_retriever.py](../../tests/infra/test_hybrid_retriever.py) | `_StubRetriever.retrieve` 接受 kw-only `viewers` |

## 3. 五大决策（D1-D5）

### D1：`owner_id: str | None`，None 等价公共（domain 视角）

**选**：domain 层 `owner_id` 是 `str | None`，`None` 表示公共；物理存储层把 `None` 映射成 `PUBLIC_OWNER_MARKER = "__public__"` 字符串。

**否**：domain 直接用 `"__public__"` 字符串。

**理由**：domain 不该泄露存储细节，`None` 是 Python 习惯（"无 owner"）；marker 字符串只在 `processing/metadata.py` ↔ `infra/kb/chroma_kb_repo.py` 这一道边界存在，前后都是 `None`。Chroma 不支持 `{"owner_id": None}` 过滤（视作不传），所以**必须**有 sentinel marker。

### D2：单 ChromaDB collection + metadata.owner_id 过滤

**选**：保留唯一 collection `rag_knowledge_base`，所有 chunk 共享同一向量索引，通过 metadata.owner_id 在查询时过滤。

**否**：每个用户一个独立 collection（`user_docs_github_alice`、`user_docs_anon_xxx`）。

**理由**：(1) Chroma collection 数量上升后内存/句柄成本陡增；(2) 公共 + 私人合检（普通用户 scope=all）需要跨 collection 合并 + 重排，复杂度高；(3) 当前用户规模远未到分库 ROI。被否方案留作 ADR-008 后续 escape hatch。

### D3：API 加 `?scope=public|mine|all`

**选**：3 个 GET 端点都加 `scope` 查询参数，默认 `all`（普通用户 = 公共 ∪ 自己 / admin = 全库）。

**否**：暴露 owner_id 过滤参数 `?owner_id=xxx`。

**理由**：scope 是**视角语义**（业务概念），owner_id 是**数据维度**（实现概念）。暴露 owner_id 等于让客户端能伪造他人 id 查别人（即便后端会校验也增加攻击面）；scope 三态足够覆盖所有真实场景。

### D4：解锁普通用户写权限，但 owner 强绑自己

**选**：解除 Step 019 把 POST/DELETE 锁死成 admin-only 的约束；普通用户 POST 自动绑 `owner_id=actor_id`，DELETE 只能删 `owner_id == actor_id`；admin 写时 `as_public=true` → 入公共库（`owner_id=None`）、`as_public=false`（默认）→ 入 admin 自己私人库。

**否方案 A**：admin 默认 `as_public=true`（不勾才入私人）。
**否方案 B**：普通用户上传时 `as_public` 字段也生效（"公共上传按钮"）。

**理由**：(1) 保守安全：API 默认值是 `False`，"不显式说要公开就一定私人"，避免误入公共；前端 UI 层 `admin` 默认勾选 `as_public=true` 是体验优化，不污染 API 契约。(2) 普通用户公共写权限属于"内容审核"语义，不在本步范围。

### D5：启动时调用 `migrate_owner_id_marker()` 懒迁移

**选**：`AppContainer.startup_migrations()` 在 FastAPI lifespan 里 `await asyncio.to_thread(...)`，扫描 metadata 缺少 `owner_id` 字段的 chunk → 一次性 update 成 `PUBLIC_OWNER_MARKER`。幂等：第二次调用为 0。

**否**：写一个脚本 `scripts/migrate_owner_id.py`，部署时手动跑。

**理由**：(1) 单实例 / 小数据规模下迁移耗时可忽略；(2) 启动期自迁移对开发者本地切换分支后零负担；(3) `hasattr` 守门 + 失败 warning 不阻塞启动，老 stub 实现安全；(4) 大数据集场景可以未来加 `KB_SKIP_MIGRATION=1` 环境变量短路，YAGNI 不提前做。

## 4. 不做

- ❌ 不引入 `ListAuditLogsUseCase` 风格的 `KbViewUseCase`（D3 后端已最轻量）
- ❌ 不拆 BM25 全局索引为 per-user 索引（D2 同理，规模未到）
- ❌ 不暴露 `owner_id` 给 RetrievePort 的客户端调用方（domain 仍只看 `Chunk.owner_id`）
- ❌ 不为普通用户加"上传到公共"按钮（D4 否方案 B 已说明）
- ❌ 不动 `service.py`（v1 删除已在 Step 016d 完成，本步严格隔离）

## 5. 验证

### 自动

```powershell
.\.venv\Scripts\python -m pytest tests/ --no-header -q
# => 555 passed（+28：11 TestOwnerScope + 17 test_vector_store_owner）

.\.venv\Scripts\python -m ruff check `
  domain/models.py processing/metadata.py `
  retrieval/search/vector_store.py retrieval/search/bm25_index.py retrieval/search/retriever.py `
  infra/kb/chroma_kb_repo.py infra/kb/unified_loader_adapter.py infra/search/hybrid_retriever.py `
  domain/ports.py app/use_cases/kb_management.py app/container.py `
  api/v2/schemas.py api/v2/documents.py main.py `
  tests/api/test_documents.py tests/app/test_kb_management.py `
  tests/infra/test_chroma_kb_repo.py tests/infra/test_hybrid_retriever.py tests/infra/test_vector_store_owner.py `
  tests/fakes/fake_kb_repo.py tests/fakes/fake_document_loader.py
# => All checks passed!
```

### 验证矩阵（手测 / TestOwnerScope 覆盖）

| 视角 \ scope | public | mine | all |
|---|---|---|---|
| 未登录 | 401 | 401 | 401 |
| 普通用户 alice | 仅公共 | 仅 alice 自己 | 公共 ∪ alice（不见 bob） |
| admin alice | 仅公共 | 仅 alice 自己 | 全库（含 bob 私人） |

| 操作 \ 角色 | 未登录 | 普通用户（owner=自己） | 普通用户（owner=他人） | admin |
|---|---|---|---|---|
| POST `/documents/file` 不传 `as_public` | 401 | 201（入私人） | — | 201（入自己私人） |
| POST `/documents/file?as_public=true` | 401 | 201（**忽略 as_public，仍入私人**） | — | 201（入公共） |
| DELETE 自己文档 | 401 | 200 | — | 200 |
| DELETE 他人/公共文档 | 401 | **404**（不暴露存在性） | **404** | 200 |

## 6. 与现有架构的关系

- **ADR-006 四层 DDD**：domain `Chunk.owner_id` 是契约，infra/repo 负责物化（`PUBLIC_OWNER_MARKER`），use case 负责权限编排（`_resolve_owners` + `actor_is_admin` 分支），api 负责把 HTTP 参数翻译成 use case 入参——边界清晰
- **ADR-008 owner_id 隔离**：本步是 KB 域的实装；Step 008 时 `Task.owner_id` / `Conversation.owner_id` 已落地，本步补齐 `KbChunk.owner_id`
- **ADR-012 admin RBAC**：和 owner_id 正交——admin 决定**视野**（scope=all 见全库 / 删任意）和**入公共**特权；owner_id 决定**所属**
- **ADR-013 审计副作用语义**：本步未改 audit，但 `KbManagementUseCase` 在 D4 决策下 actor_id 强制传入，审计天然记录到谁在改

## 7. 后续候选

| 编号 | 类型 | 描述 |
|---|---|---|
| 025b | 工程 | mypy 复活 |
| 025c | 工程 | 登录端点接入 `AuditLogPort` |
| 026a | 工程 | audit 时间范围过滤 + 导出 CSV |
| 026b | 文档 | `evaluations/` 三套评测的方法论文档化 |
| 027 | 工程 | 文件级权限分享（私人 → 指定其他用户可见） |
| 028 | 工程 | KB 配额 / 容量监控（per-owner chunk 数上限）|
