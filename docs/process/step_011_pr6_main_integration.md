# Step 011 — PR-6 收尾：`api/v2` 接入 `main.py`

## 1. 本步骤目标

Step 010 把 `/api/v2` 全部路由写完，但**没接进生产 ASGI 应用**——`main.py` 里仍只挂了老 `api.routes.router`。
本步骤把 Step 010 的成果端到端跑通：`uvicorn main:app` 启动后，浏览器即可访问 `/api/v2/health`、
`POST /api/v2/auth/anonymous` 等新接口，**老 `/api/*` 完全不动继续服务老前端**。

下游受益：

- Step 012 起的前端改造可以直接 `fetch('/api/v2/...')`，无需再起独立服务
- Step 014（PR-7）的 risk 子路由只要 `app.include_router(build_risk_routes(container), prefix="/api/v2/risk")` 即可加挂

## 2. 修改文件

| 文件 | 改动 | 行数 |
|---|---|---|
| [main.py](../../main.py) | 装配 `AppContainer` + 挂 `/api/v2` + `install_exception_handlers` + CORS `allow_credentials=True`；同时把混乱的 import 排序 + 删未用 `os` | +18 / -8 |
| [api/v2/errors.py](../../api/v2/errors.py) | `install_exception_handlers(app, *, path_prefix="/api/v2")` 加守门员；不在 v2 前缀的请求走 FastAPI 默认错误格式（保持老 API 契约） | +30 / -8 |
| [tests/api/test_main_integration.py](../../tests/api/test_main_integration.py) | 新增 7 个集成用例：真 `main.app` 同时验证 v1+v2 共存 | +90 (new) |

## 3. 设计决策

| 决策 | 取代方案 | 原因 |
|---|---|---|
| **container 在 `main.py` 模块级构造** | 放进 `lifespan` 异步上下文 | `include_router` 在 app 创建时就要拿到 router；container 构造本身只装组件、不连远端，无需异步；后续如需异步初始化（如 chromadb warmup）可往 lifespan 加阶段 |
| **`install_exception_handlers(app, path_prefix="/api/v2")` 守门员** | 全局重写 HTTPException → 老 API 也吃 | 老 `/api/ingest/file` 等返回 `{"detail": "<中文消息>"}`；v2 返回 `{"error_code","message"}`；二者前端契约不同，必须按 path 路由。Strangler Fig 的关键护栏 |
| 守门员命中老路由时**手动构造默认响应** | `raise exc` 让 starlette 兜底 | 注册过的 exception_handler 就是终点站，`raise` 不会触发其他 handler，只会冒到 500 ASGI middleware。直接构造 `JSONResponse({"detail": ...})` 与 FastAPI 默认行为对齐 |
| **CORS `allow_credentials=True`** | 保留默认 False | cookie session 必须打开；同时给出注释提醒：浏览器规定打开 credentials 时禁用 `origin="*"`，生产部署要把 `CORS_ORIGINS` 配成显式白名单 |
| **`expose_headers=["X-Request-ID"]`** | 不暴露 | 前端拿到失败响应时能从 header 抓 request_id 报给后端排查（既有 request_context_middleware 已经写了这个 header） |
| **集成测试用 `module` 作用域 fixture** | 每个测试新建 client | 走 main 路径包含 `KnowledgeService` 后台初始化线程；复用同一个 app 实例显著加速（3.9s vs 单测各自 5s+） |
| **测试里用 `os.environ.setdefault("LLM_PROVIDER", "local")`** | 在 conftest 全局注入 | `setdefault` 只在用户没指定时生效，本地真要测 api 通道也不会被覆盖；且本测试文件只在自己导入时影响 |

## 4. 核心契约

### `main.py` 装配段（核心 7 行）

```python
container = AppContainer(settings)
app.state.container = container
app.include_router(build_v2_router(container), prefix="/api/v2")
install_exception_handlers(app)            # 默认 path_prefix="/api/v2"
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,                 # cookie 必需
    ...
)
```

### `install_exception_handlers` 守门员语义

| 请求路径 | 异常类型 | 响应 |
|---|---|---|
| `/api/v2/*` | DomainError | `{error_code, message}` + 映射状态码（见 Step 010 表） |
| `/api/v2/*` | HTTPException(detail=dict) | 透传 detail（401/403/404 结构化 body） |
| `/api/v2/*` | HTTPException(detail=str) | 包成 `{error_code:"HTTP_ERROR", message:str}` |
| `/api/v2/*` | ValueError / PermissionError | 400 / 403 ErrorResponse |
| `/api/*`（老） | HTTPException | `{detail: str/dict}`（FastAPI 默认行为，**不重写**） |
| `/api/*`（老） | DomainError | 500（理论上不应发生；当 bug 报） |

## 5. 与外部服务的关系

- 启动时 `start_service_init()` 后台线程仍负责老 `KnowledgeService`（chromadb 加载）
- 新 container 复用 [config.py](../../config.py) 同一组路径，但用的是独立的 sqlite 池 + retriever 适配器
  （二者各自管自己的资源；老的 chroma client 与新的 `infra/search/Embedder` 通过 settings 指向同一个目录）
- 进程退出由操作系统回收 fd；不显式 `container.close()`——SQLite 线程局部连接随线程终止自动关闭

## 6. 当前实现范围

✅ 已实现：

- main.py 装 container + v2 router + 异常处理 + CORS credentials
- 异常处理按 path_prefix 限定作用域，**老 API 0 回归**
- 集成测试：v2/health、ready、anonymous（带 cookie）、require_owner 401、老 `/` 与 `/health` 仍服务
- ruff 0 / mypy 0 / 380 passed

❌ 未实现（按计划推迟）：

- **前端切换** —— 旧 [frontend/index.html](../../frontend/index.html) 仍打老接口；Step 012/013 做 Copilot UI 重构
- **risk 子路由** —— Step 014 PR-7
- **记忆系统 L1/L2** —— Step 015 原 PR-6 后半
- **老 API `Deprecation` 头** —— 等前端切完
- **日志结构化（JSON + request_id 贯穿到 use case 层）** —— PR-7 收尾
- **`container.close()` 钩子** —— 当前没有外部连接需要主动关；接 chromadb HTTP client 时再加 lifespan shutdown 阶段

## 7. 暂未实现 / TODO

- `cors_origins` 默认 `["*"]`，配 credentials=True 时浏览器会拒绝带 cookie 的跨域；生产必须改成显式白名单
  → 留给部署文档（Step 011 的范围只是把开关打开）
- `container` 是模块级单例，热重载（`uvicorn --reload`）时旧 sqlite 池会泄漏；开发期可接受
- `KnowledgeService`（老）与 `AppContainer.retriever`（新）目前是两套并存的 chroma 连接。前端切换后老 service 删除时一并清理
- 集成测试没覆盖 SSE 端点（`/api/v2/copilot/chat/stream`）走 main.app 的场景——但 [tests/api/test_copilot_sse.py](../../tests/api/test_copilot_sse.py) 已通过裸 app 覆盖，差异只在中间件，风险低

## 8. 测试与验证

```bash
pytest -q
# 380 passed, 16 warnings in 20.36s   (373 -> 380, +7)

ruff check main.py tests/api/test_main_integration.py api/v2
# All checks passed!

mypy main.py api/v2
# Success: no issues found（与 mypy 输出过滤一致）

# 手动冒烟
python -c "import main; print(sorted({r.path for r in main.app.routes if hasattr(r,'path') and r.path.startswith('/api/v2')}))"
# ['/api/v2/auth/anonymous', '/api/v2/auth/github/callback', '/api/v2/auth/github/login',
#  '/api/v2/auth/logout', '/api/v2/auth/me', '/api/v2/copilot/chat',
#  '/api/v2/copilot/chat/stream', '/api/v2/health', '/api/v2/health/ready',
#  '/api/v2/tasks', '/api/v2/tasks/{task_id}']
```

### 集成测试覆盖

| 用例 | 验证点 |
|---|---|
| `test_main_app_has_container_in_state` | `app.state.container` 已存且 4 工具齐 |
| `test_v2_health_works` | `GET /api/v2/health` 返回 v2 标识 |
| `test_v2_ready_lists_all_tools` | `GET /api/v2/health/ready` 端口装配 + 工具列表 |
| `test_v2_anonymous_login_returns_cookie` | 走真 AuthService（不是 FakeAuth）签发 anon: + Set-Cookie |
| `test_v2_require_owner_blocks_unauthed` | 401 `AUTH_REQUIRED` 结构化 body |
| `test_legacy_root_still_served` | `GET /` 老入口仍挂着 |
| `test_legacy_api_health_still_served` | 老 `/health` 路由不被守门员影响（关键 Strangler Fig 验证） |
