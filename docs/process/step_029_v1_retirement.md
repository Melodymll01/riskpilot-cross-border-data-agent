# Step 029 — v1 整体退役：删除单体 HTTP 层（service / routes / schemas）

## 1. 本步骤目标

v1 退役迁移三步走的**收官步**。Step 027（reranker 工厂）、Step 028（research 能力）
已把 v1 仅剩的两件「检索武器」迁进 v2。此前 Strangler Fig 的两条藤蔓——

- 老 ASGI 入口：`api/routes.py`（v1 端点）+ `api/schemas.py`（v1 schema）
- 老业务门面：`service.py:KnowledgeService`（一个类管检索/问答/研究/会话）

——已无任何**生产路径**依赖：前端 `frontend/api.js` 全部走 `/api/v2`，根 `/health`
无人调用（无 docker healthcheck），v1 端点早被 v2 闭包路由覆盖。

本步把这三件 v1 HTTP 层文件**整体删除**，并拆掉 `main.py` 里挂载它们的装配代码，
让 `main:app` 只剩 `api/v2/*`。至此 v1→v2 迁移完成，代码库不再有 v1/v2 并存。

## 2. 修改文件

| 文件 | 说明 |
|---|---|
| `service.py` | **删除**：v1 `KnowledgeService` 单体门面（检索/问答/研究/会话一把抓） |
| `api/routes.py` | **删除**：v1 端点（`/health` `/api/retrieve` `/api/ask` `/api/research` `/api/conversations*`）+ `limiter`（slowapi） |
| `api/schemas.py` | **删除**：v1 请求/响应 schema |
| `api/__init__.py` | 清空 `from api.schemas import *` / `from api.routes import router`，只留包 docstring |
| `main.py` | 移除 `limiter` / `RateLimitExceeded` handler / `start_service_init()` / `include_router(router)` 及相关 slowapi import；只保留 v2 装配 + 中间件 + 静态前端 + `GET /` |
| `evaluations/benchmark/run.py` | 新增 `BenchmarkRagService`（只读引擎门面），替代已删除的 `KnowledgeService` 依赖 |
| `tests/test_api.py` | **删除**：整文件测 v1 端点 |
| `tests/test_schemas.py` | **删除**：整文件测 v1 schema |
| `tests/api/test_main_integration.py` | 删 `test_legacy_api_health_still_served`（测根 `/health`）+ 更新 docstring（去掉 KnowledgeService/老 health 描述） |

## 3. 设计决策

- **D1 删 v1 HTTP 层，保留 retrieval/ 引擎模块**：被删的是 v1 的**编排/门面**
  （routes/schemas/service）。`retrieval/`（embedder/vector_store/retriever/qa_chain/
  agentic_rag/reranker…）是 v1、v2、benchmark **共享**的引擎资产，不属 v1 HTTP 层，**保留**。
- **D2 benchmark 改用引擎门面而非重写**：`evaluations/benchmark/run.py` 原依赖
  `KnowledgeService.retrieve/ask` + `.retriever/.vector_store`。新增 `BenchmarkRagService`
  直接装配引擎模块（`build_reranker()` + `Retriever` + `QAChain`，与 v2 检索同源），
  提供 benchmark 所需的 `retrieve/ask` 四个入口——复用引擎，不复制逻辑。
- **D3 移除 `limiter`（v1 专属），v2 限流列为后续**：slowapi `Limiter` 定义在
  `api/routes.py`，只有 v1 `@limiter.limit` 端点用它（grep 确认 v2 路由 0 引用）。
  随 routes.py 一并删除，`main.py` 拆掉 `app.state.limiter` / `RateLimitExceeded` handler。
  **v2 暂无 HTTP 限流**——列为后续增强（在 `api/v2` 层重新引入，不复用 v1 装配）。
- **D4 删根 `/health`（前端走 `/api/v2/health`）**：`frontend/api.js` 的 `BASE="/api/v2"`，
  `health.check()` → `GET /api/v2/health`（`api/v2/health.py`，返回 `{status:ok,version:v2}`）。
  根 v1 `/health` 无前端/无 docker healthcheck 引用，安全删除。
- **D5 保留 `GET /`（非 v1）**：静态前端入口由 `main.py` 直接 `FileResponse` 服务，
  不经 v1 路由，保留。集成测试 `test_legacy_root_still_served` 同步保留。

## 4. 验证

| 项 | 命令 | 结果 |
|---|---|---|
| 全量 | `pytest -q` | **627 passed, 1 skipped**（较 Step 028 −15：删 `test_api.py` + `test_schemas.py` + 1 个 legacy health 用例） |
| 静态 | `ruff check`（`main.py` / `api/__init__.py` / `tests/api/test_main_integration.py`） | All checks passed |
| 静态 | `ruff check`（`evaluations/benchmark/run.py` 新增 `BenchmarkRagService`） | 新增行 clean（文件其余为既有 typing 风格遗留，非本步引入） |
| Live import | `python -c "import main; TestClient(main.app)"` | ✅ `main:app` 无 v1 装配即导入成功；`/api/v2/health` → 200 `{status:ok,version:v2}`；老 `/health` → **404** |

> 注：`test_jwt_issuer::test_tampered_token_returns_none` 偶发 flaky（HMAC 篡改概率边界），
> 单独重跑通过，与本步无关。

## 5. 后续

- **迁移完成**：v1/v2 并存结束，代码库只剩 `api/v2/*`。下一阶段进入「模块策略/算法
  重设计」（查询改写调参、自适应 RAG、切分策略、prompt 策略、rerank+评测闭环等）。
- v2 HTTP 限流：在 `api/v2` 层重新引入（D3）。
- 历史文档（`docs/decisions/ADR-010-strangler-fig`、`docs/process/step_011/016`）中对
  `KnowledgeService` / `api.routes` 的描述为**历史记录**，保留不动。
