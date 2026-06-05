# Step 016d — KB 管理重构 第 4 阶段（收官）：v1 单体入口删除

## 1. 本步骤目标

完成 Step 016 完整重构方案 B 的最后一步：把已经被 016a/016b/016c
DDD 链路（10 Port / 5 use case / 6 admin-only `/api/v2/documents/*` 端点）
完整替代的 v1 单体 KB 管理入口集中删除，给 KB 管理面**只留下一条路**：
`api/v2/documents` → `container.kb_management` → `KbDocumentRepoPort` +
`DocumentLoaderPort` + `EmbedPort`。

为后续做：
- 让 mypy/ruff 不再被老 `KnowledgeService.ingest_*` 上的旧代码分散注意
- 给所有维护者一个明确信号：KB 管理走 v2，不要再回到 v1 老路
- 让 `service.py` 的职责退缩到"只读检索 / 问答 / Agentic RAG"
  （`retrieve` / `ask` / `ask_stream` / `research` / `research_stream`），
  与 KB 管理职责彻底解耦

## 2. 修改文件

| 路径 | 说明 |
|---|---|
| `service.py` | -`ingest_file` / `ingest_web` / `_ingest_document` / `list_sources` / `delete_source` 五个方法；-`IngestResult` dataclass；-`UnifiedLoader` / `build_chunks` / `ChunkWithMetadata` import；-`loader` 构造参数 + `self.loader` 字段；类 docstring 更新为"只读检索"语义 |
| `api/routes.py` | -`POST /api/ingest/file` / `POST /api/ingest/web` / `GET /api/sources` / `DELETE /api/sources/{src}` 四个端点；-`ALLOWED_EXTENSIONS` 常量 + `_safe_save_path` 助手；-`UploadFile` / `File` / `os` / `Path` / `uuid4` import；-`WebIngestRequest` / `IngestResponse` / `SourceListResponse` / `SourceItem` / `DeleteSourceResponse` schema import；模块 docstring 改为指向 `/api/v2/documents/*` |
| `api/schemas.py` | -`WebIngestRequest` / `IngestResponse` / `SourceItem` / `SourceListResponse` / `DeleteSourceResponse` 五个 schema；-`urlparse` import（之前仅 `WebIngestRequest` validator 使用） |
| `tests/test_api.py` | -`TestIngestEndpoints` 整个类（4 测试：unsupported_format / file_too_large / web_invalid_url / web_valid_url；含老 `from service import IngestResult`） |
| `tests/test_schemas.py` | -`TestWebIngestRequest`（5 测试）+ `TestDeleteSourceResponse`（2 测试）；-相关 import（顺手清理 ruff 已报的 `RetrieveRequest` 死 import） |
| `domain/ports.py` | `KbDocumentRepoPort` docstring 把"与 v1 ...是平行实现，Step 016d 删除 v1 后..."改为"v1 已删除，本端口成为唯一入口" |
| `frontend/{app,index,style}.legacy.*` | 三件归档前端：`git rm` 删除（未被 `main.py` 服务，git 历史可查；当前 UI 是 `frontend/{index.html,app.js,style.css}` v2 单聊天版） |

## 3. 设计决策

### D1：彻底删除，**不**保留 410 Gone 迁移端点

- 候选 A：保留路由壳子返回 `410 Gone` + `Location: /api/v2/documents/*`
- 候选 B（采用）：直接删除路由，命中老 URL 走 FastAPI 默认 404

理由：
1. **没有真实客户端**：v2 前端（Step 012 重写后）从来不调老端点；
   归档的 v1 前端也在本步骤里一起删；外部 SDK 不存在
2. **维护成本**：410 端点要保留 `WebIngestRequest` schema、保留
   `KnowledgeService.loader` 等"骨架"才能编译，等于半个 v1 还在
3. **测试断言模糊**：410 + Location 的契约本身要写测试，但谁也不会调
4. **404 信号已够**：日志里看到一条 `404 /api/ingest/file` 比一个被
   忽略的 410 更刺眼，更能促使老脚本被发现

### D2：`service.py.KnowledgeService` **保留**（不整个文件删除）

- `retrieve` / `ask` / `ask_stream` / `research` / `research_stream`
  这五个**检索 / 问答 / Agentic RAG** 方法不属于本次 KB 管理重构范围
- 还在被 `api/routes.py` 的 `/api/retrieve` `/api/ask` `/api/ask/stream`
  `/api/research` 端点使用
- 还在被 `evaluations/benchmark/run.py` 在脚本里 import 使用

→ 只切除"管理面"的 5 个方法 + 相关字段，**检索面**保持原样不动。

### D3：`field_validator` import **保留** 在 `api/schemas.py`

第一次编辑把整行 `from pydantic import BaseModel, Field, field_validator`
误改成 `BaseModel, Field`，触发 Pylance 报错——其他 schema
（`ResearchRequest.mode`、`ConversationMessageItem.role` 等）仍在用
`@field_validator`。立即恢复，避免连锁回归。

### D4：测试清理"顺手不深耕"

`tests/test_schemas.py` 删完目标类后 ruff 又报了 `RetrieveRequest`
是 dead import（**Step 016d 之前就已经是死 import**，并不是本步引入的）。
按"既然我已经在这个 import 列表上动手"原则一起清掉；
不去扩面修同文件其他 pre-existing 风格问题（UP006 / B904 等）。

## 4. 核心契约 / 接口

本步骤**没有新增任何 Port / use case / schema / 路由**——纯删除。
对外契约变化只有一条：

| 老端点 | 新端点 | 鉴权差异 |
|---|---|---|
| `POST /api/ingest/file` | `POST /api/v2/documents/file` | 老：无鉴权 → 新：admin-only |
| `POST /api/ingest/web` | `POST /api/v2/documents/web` | 老：无鉴权 → 新：admin-only |
| `GET /api/sources` | `GET /api/v2/documents` | 老：无鉴权 → 新：admin-only |
| `DELETE /api/sources/{src}` | `DELETE /api/v2/documents/{src}` | 老：无鉴权 → 新：admin-only |
| —（未提供） | `GET /api/v2/documents/stats` | 新增 |
| —（未提供） | `GET /api/v2/documents/{src}` | 新增 |

> 鉴权升级是 Step 013 admin baseline 落地的自然结果：KB 管理是
> 写操作，不应允许任意匿名用户调用。本步骤后此契约成为强制。

## 5. 与外部服务的关系

- **零变化**。KB 数据仍走 `infra/kb/chroma_kb_repo.py` →
  `chromadb 1.5.9`；文件加载 / 切分仍走
  `infra/kb/unified_loader_adapter.py` 包装 v1
  `UnifiedLoader + build_chunks`；embedding 仍走
  `retrieval/search/embedder.py`（Step 006 适配过的 `EmbedPort`）。
- 老 `service.py.KnowledgeService` 还在用 `vector_store.VectorStore`
  做检索——这条链路不在本次重构范围。

## 6. 当前实现范围

### 已实现

- [x] `service.py` KB 管理方法 / 字段 / import 全部清空
- [x] `api/routes.py` 4 个 KB 端点 + 助手 + 相关 import 全部清空
- [x] `api/schemas.py` 5 个 KB schema + `urlparse` import 全部清空
- [x] `tests/test_api.py` 删 `TestIngestEndpoints`
- [x] `tests/test_schemas.py` 删 `TestWebIngestRequest` / `TestDeleteSourceResponse`
- [x] `domain/ports.py` docstring 更新过时引用
- [x] `frontend/*.legacy.*` 三件归档前端 `git rm`
- [x] `pytest -q` 全绿（479 passed，-11 测试与计划一致：4 + 5 + 2）
- [x] ruff F401 死 import 清零
- [x] mypy 错误总数不增长

### 未实现（按设计跳过）

- 410 Gone 迁移端点：见 D1
- `KnowledgeService` 整类删除：见 D2
- 任何 `evaluations/benchmark/run.py` 改动：脚本只用
  `retrieve` / `ask`（KB 管理外），保持不动
- v2 前端 KB 管理 UI（admin 看到一个"知识库管理"侧栏）：
  仍未实现；当前 admin 只能用 curl 直接打 `/api/v2/documents/*`，
  和 016c 收尾结论一致——独立 PR

## 7. 暂未实现 / TODO

- v2 前端 admin 视图的"知识库管理"页面（独立 PR）
- 长期：`service.py.KnowledgeService` 整类的"检索面"也走 DDD
  （`RetrievePort` 已存在于 Step 006/008，但 api v1 路由仍直接调
  `service.retrieve`），属于"v1 检索面重构"主题，与本次 KB 管理重构正交

## 8. 测试与验证

```powershell
# 全量回归
cd d:\py\RagDataOut
.venv\Scripts\python.exe -m pytest -q
# → 479 passed, 16 warnings in 41.74s
#   （Step 016c 收尾 490 passed - 11 删除测试 = 479）

# 触碰文件的 ruff（v1 legacy 旧风格遗留 48 错全为 UP006/UP045/I001/B904/E402/UP035；
# 没有 F401，本步引入的 uuid4 死 import 已清；本步触碰文件零新增 lint）
ruff check service.py api/routes.py api/schemas.py tests/test_api.py tests/test_schemas.py domain/ports.py --statistics

# mypy 不增长
mypy service.py api/routes.py api/schemas.py
# → Found 46 errors in 13 files（与 Step 016a/b/c 基线持平，全部位于
#    v1 legacy 文件：retrieval/agent/*, retrieval/generation/qa_chain.py,
#    retrieval/search/{vector_store,retriever}.py, service.py 已存在的
#    DistanceThresholdReranker assignment 2 错, api/routes.py 327 行
#    CreateConversationRequest 默认值 1 错；本步未引入新错）
```

## 9. Step 016 整体小结

| 阶段 | 范围 | 实体新增 | 测试数 |
|---|---|---|---|
| 016a | infra 层：Port + 适配器 + Fake | `KbDocument` / `KbChunk` / `KbSourceType` / `KbDocumentRepoPort` / `ChromaKbRepo` / `FakeKbRepo` | +27（→440） |
| 016b | app 层：use case + loader port | `DocumentLoaderPort` / `UnifiedLoaderAdapter` / `FakeDocumentLoader` / `KbManagementUseCase` / `KbIngestResult` | +25（→465） |
| 016c | api 层：6 个 admin-only 端点 + schemas | `build_documents_routes` / `WebIngestRequest` / `KbDocumentOut` / `KbDocumentListResponse` / `KbDocumentStatsResponse` / `KbIngestResponse` / `DeleteDocumentResponse` | +25（→490） |
| **016d** | **删除 v1 单体 KB 入口** | **0**（纯删除） | **-11（→479）** |

总跨度：
- domain 端口 9 → 10（+1 `KbDocumentRepoPort`，+1 `DocumentLoaderPort`，实际 11）
- app use case 4 → 5（+1 `KbManagementUseCase`）
- api v2 端点 13 → 19（+6 documents 路由）
- 测试 413 → 479（净 +66）

至此 Step 016 KB 管理面 v1 单体 → DDD 四步重构方案 B 完整收官。
