# Step 016c — api/v2/documents 路由（KB 管理重构第 3 阶段）

> 对应即将提交的 commit（本步与代码同 commit）
> 计划标题：`feat(api): /api/v2/documents 路由接通 KbManagementUseCase（PR-8c / Step 016c）`

## 1. 本步骤目标

在 [Step 016b](step_016b_kb_management_use_case.md) 装好但**没人调用**的
`AppContainer.kb_management` 之上，引入 `api/v2/documents.py` 6 个端点把 use
case 真正端到端联通。守门策略采用 admin-only（KB 管理是后台运维资源）。

继续保持"只增不删"——v1 `api/routes.py` 的 `/api/ingest/file` `/api/ingest/web`
`/api/sources` 仍然挂着，使用同一份 `KnowledgeService`；v1 删除推迟到 [Step 016d]。

前端范围：v2 新前端 (`frontend/app.js` + `index.html`) 目前**不包含**任何 KB
管理 UI（KB 面板仅存在于 `app.legacy.js` / `index.legacy.html` 归档代码，
`main.py` 不挂载这两个文件）。本步骤**只做后端**，KB 管理面板的 v2 UI 留给
独立 PR；016d 删 v1 时 legacy 前端代码可一并归档。

测试基线：016b 的 465 → 本步骤 490（+25），零回归。

## 2. 修改文件

| 文件 | +/- | 关键改动 |
|---|---|---|
| [api/v2/schemas.py](../../api/v2/schemas.py) | +63 | 新增 6 个 schema：`KbDocumentOut` / `KbDocumentListResponse` / `KbDocumentStatsResponse` / `KbIngestResponse` / `DeleteDocumentResponse` / `WebIngestRequest`（含 `urlparse` http(s) 协议 + 域名校验） |
| [api/v2/documents.py](../../api/v2/documents.py) | +236 | 新建 `build_documents_routes(container)` 闭包；6 个端点全部 `Depends(make_require_admin)`；文件上传：后缀白名单 + 大小校验 + UUID 重命名落 `settings.upload_dir`；同步 use case 用 `anyio.to_thread.run_sync` 让出事件循环 |
| [api/v2/router.py](../../api/v2/router.py) | +2 | `build_v2_router` 装入 `build_documents_routes(container)` |
| [tests/api/conftest.py](../../tests/api/conftest.py) | +4 | container fixture 注入 `kb_repo=FakeKbRepo()` + `document_loader=FakeDocumentLoader()`（避免 default 路径构造真 ChromaKbRepo） |
| [tests/api/test_documents.py](../../tests/api/test_documents.py) | +362 | 25 个用例：6 个 auth 守门 + 19 个业务用例（list 2 / stats 2 / get 2 / delete 2 / ingest_file 6 / ingest_web 5）；用 `_login_as_admin` helper 走 GitHub fake 流走管理员登录 |
| [docs/process/step_016c_api_v2_documents.md](step_016c_api_v2_documents.md) | new | 本工程留痕文档 |
| [docs/process/README.md](README.md) | +1 row | 索引新增 016c 行 |

合计 **~+670**，7 个文件。

## 3. 设计决策

| 选择 | 取代方案 | 原因 |
|---|---|---|
| **全部端点 admin-only（含 list / get / stats）** | list/get 仅要求 owner（任何登录用户）/ stats 完全公开 | KB 是后台运维数据；普通用户的"知识库总览"诉求由其他端点（如 health）满足。统一 admin-only 减少守门策略碎片 |
| **`POST /documents/file` 和 `/documents/web` 都返回 201 Created** | 200 OK | 语义上确实是"新建资源"；同时空文档返回 `success=False` + 201 比 4xx 更适合"无害但无效"的输入（同 use case 内对齐） |
| **`DELETE /documents/{src}` 命中删 0 时返回 404** | 200 + `deleted_count=0` | 符合 REST 资源不存在语义；前端 toast 也更明确（区分"删了但是 0 条"和"根本没找到这个文档"） |
| **`GET /documents/{source_name}` 用 path 参数而非 query** | `GET /documents?source_name=...` | path 参数可被 URL 编码携带任何字符（包括中文文件名）；FastAPI 自动 decode；与 `DELETE /documents/{src}` 对称 |
| **`POST /documents/file` 后缀白名单同 v1（`.pdf .txt .docx`）** | 仅 MIME 类型校验 | MIME 来自客户端可伪造；后缀+UUID 重命名是最低成本的"双校验"；与 v1 行为一致让回归更可控 |
| **UUID 重命名 + UPLOAD_DIR finally 清理** | 用原文件名落地 / tempfile.NamedTemporaryFile | UUID 消除路径穿越；`settings.upload_dir` 让运维可控制磁盘位置；`finally` 清理避免临时文件泄漏（test_upload_temp_file_cleaned_on_success 验证） |
| **upload `category` 走 query 参数而非 form 字段** | multipart form 里再加 `category` 字段 | 同 v1 接口设计；前端代码改动最小；FastAPI 同时支持 file + query 不冲突 |
| **`WebIngestRequest` 用 `field_validator` 校验 http/https + 域名** | 不校验放给 use case | 与 v1 `api/schemas.py:WebIngestRequest` 行为对齐；422 在请求边界拒绝比 ValueError 翻译 4xx 更标准 |
| **`anyio.to_thread.run_sync` 包同步 use case** | 直接调用 `container.kb_management.ingest_file(...)` | KbManagementUseCase 是同步实现（embedder / chroma 都是阻塞 IO）；在 async 端点里直接调会阻塞事件循环，影响其他请求 |
| **conftest 给 container 注入 FakeKbRepo + FakeDocumentLoader** | 让默认 `build_kb_repo` 走 ChromaKbRepo（用 tmp dir） | tmp dir + chromadb 启动慢（~1s+）；测试间 collection 状态污染；in-memory Fake 是干净选择 |
| **测试 `_login_as_admin` helper 只断言 cookie 存在，不查 /auth/me** | 走 /auth/me 验证 authenticated=True | FakeAuth.complete_oauth 不向 user_repo 写 user → /auth/me 会判 authenticated=False；但 `require_admin` 只校验 JWT 不读 user_repo，所以 cookie 足矣 |
| **测试用 `KbChunk` 直接喂 FakeKbRepo 而非走完整 loader** | 测试里通过 `FakeDocumentLoader(chunks=...)` 注入 | 测试只关心"路由层"是否正确编排；用 `_seed_chunks` helper 直接喂底层 repo 更显式（loader 测试归 `test_kb_management.py`） |
| **`build_documents_routes` 用闭包持 container 而非 `Depends(get_container)`** | FastAPI Depends 注入 container | 与 `build_auth_routes` / `build_task_routes` 等已有 router 对齐；闭包模式让单测更直观（无需 override dependency） |
| **路由层 `try/except ValueError` 翻译为 400** | 让异常落到全局 handler | use case 的 ValueError 是"语义错误的请求"（空 source_name 等）；400 比默认 500 更合适；同时保留 `from e` 链让日志可追溯 |
| **未引入 slowapi 限流（与 v1 不同）** | 也加 `@limiter.limit(settings.rate_limit_ingest)` | v2 还没整体接入 slowapi；admin-only 已极大降低滥用面；限流方案统一在专门 step 处理（不是本 step 的范围） |

## 4. 核心契约 / 端点表

| 方法 | 路径 | Schema 出 | 守门 | 说明 |
|---|---|---|---|---|
| `GET` | `/api/v2/documents` | `KbDocumentListResponse` | admin | 列出全部文档（按 source_name 聚合） |
| `GET` | `/api/v2/documents/stats` | `KbDocumentStatsResponse` | admin | 文档数 + chunk 数 |
| `GET` | `/api/v2/documents/{source_name}` | `KbDocumentOut` (404 `DOCUMENT_NOT_FOUND`) | admin | 单文档详情 |
| `DELETE` | `/api/v2/documents/{source_name}` | `DeleteDocumentResponse` (404 `DOCUMENT_NOT_FOUND`) | admin | 删除（连带所有 chunks） |
| `POST` | `/api/v2/documents/file` | `KbIngestResponse` (201) | admin | multipart 上传；400 `UNSUPPORTED_FILE_TYPE`；413 `FILE_TOO_LARGE` |
| `POST` | `/api/v2/documents/web` | `KbIngestResponse` (201) | admin | JSON 体 `{url, category}`；422 协议/域名非法 |

错误响应统一走 `api/v2/errors.py:install_exception_handlers` 装的 schema：
`{error_code, message, details?}`。

## 5. 验证清单

```powershell
# scoped ruff
ruff check api/v2/documents.py api/v2/router.py api/v2/schemas.py `
  tests/api/test_documents.py tests/api/conftest.py
# → All checks passed!

# scoped mypy（新代码 0 错；遗留 46 错全在 retrieval/ingestion/service.py/api/routes.py）
mypy api/v2/documents.py api/v2/router.py api/v2/schemas.py

# focused tests
pytest -q tests/api/test_documents.py
# → 25 passed

# full regression
pytest -q
# → 490 passed（016b 基线 465 + 新增 25），零回归
```

## 6. 与 v1 共存策略（v1 删除继续延后到 016d）

依然**不删任何 v1 代码 / 不动 v1 前端**：

- `service.py:KnowledgeService.ingest_file/ingest_web/list_sources/delete_source` 仍在；
- `api/routes.py` 的 `/api/ingest/file` `/api/ingest/web` `/api/sources` 仍可用；
- `app.legacy.js` 仍指向 `/api/sources` 等老 URL（legacy HTML 也没被 `main.py` 服务，仅作归档）；
- 新 `/api/v2/documents/*` 端点暂时**没人调用**——v2 前端没 KB 面板，
  也没有迁移脚本。

下一步统一处理（[Step 016d]）。

## 7. 已知风险 / 后续工作

| 风险 | 缓解 | 跟进 |
|---|---|---|
| `ingest_file` 端点同步调用 use case 走 `anyio.to_thread`，大文件场景下仍会阻塞工作线程数十秒 | embedder + chunks 长度在测试环境可控；生产可调 uvicorn `--limit-concurrency` | 长期：把 embedder Port 异步化或 ingest 放任务队列 |
| v2 前端尚无 KB 管理 UI，新端点等同"隐式接口"，无 UI 端到端验证 | 25 个 API 测试覆盖业务面；可通过 `httpie`/`curl` 手测 | 计划做独立 PR 给 v2 加 KB 面板（不在 016 范围） |
| `WebIngestRequest.url` 校验仅做协议 + 域名，没做 SSRF 拒内网 | v1 也未做；admin-only 已降低风险面 | 长期：加 IP 段白名单或 outbound proxy |
| `ALLOWED_EXTENSIONS = {.pdf, .txt, .docx}` 与 v1 重复硬编码 | 若变更需同时改两处；目前只 3 个值，重复成本低 | 016d 删 v1 后此重复消失 |
| 临时文件清理依赖 `finally`，进程崩溃时仍可能残留 | `UPLOAD_DIR` 在测试用 tempdir；生产可独立挂载 + 定期清理 | 长期：用 NamedTemporaryFile + 异步清理 task |

## 8. 下一步

- **Step 016d**：v1 集中删除
  - `service.py:KnowledgeService` 的 4 个 KB 方法（list/get/delete/ingest_file/ingest_web）
  - `api/routes.py` 的 `/api/ingest/*` 和 `/api/sources*` 端点（保留 410 Gone 迁移测试）
  - 归档 `frontend/app.legacy.js` `index.legacy.html` `style.legacy.css`（或一并删）
  - 给现存"知识库管理" v2 UI 的 issue 链接占位
  - 最后**整体一次 push**（016a/b/c/d 共 4 个 commit）

——继续维持"三步之间 commit + 测试绿，整体一次 push"的节奏。
