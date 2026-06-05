# Step 010 — PR-6 API v2 路由层（FastAPI + SSE）

## 1. 本步骤目标

> 给六边形架构的最外层（HTTP 入口）落地，把 Step 008 的 4 个 use case 与 Step 009 的 `RunCopilotUseCase` 对外暴露成
> RESTful API + SSE 流式接口。**严格 Strangler Fig：不动 `service.py` / `main.py` / `api/routes.py`**，
> 全部新代码挂在 `/api/v2` 路径下，老接口继续服务老前端。

为后续工作提供：

- 前端可以直接 fetch `/api/v2/copilot/chat/stream` 实现"对话式合规副驾"流式 UI（不需要老的 query 端点）
- Step 011（PR-6 续：前端改造）只需对接 `/api/v2/*`，旧前端不动
- Step 012（PR-7：合规风险点提取）会作为 `/api/v2/risk/*` 子路由直接挂上来，不再触碰旧 `service.py`

## 2. 修改文件

新增（全部）：

- `api/v2/__init__.py` — 只导出 `build_v2_router`，外层只看见这一个符号
- `api/v2/schemas.py` — 全部 Pydantic 请求/响应模型（generic / auth / tasks / copilot 四组）
- `api/v2/deps.py` — closure 依赖：`make_identify_owner` / `make_require_owner` + cookie 工具
- `api/v2/errors.py` — `install_exception_handlers`：DomainError MRO → 状态码 + error_code 映射
- `api/v2/sse.py` — `event_to_sse` / `sse_keepalive` / `sse_error` / `stream_with_keepalive`
- `api/v2/auth.py` — `build_auth_routes`：`/auth/{anonymous,github/login,github/callback,me,logout}`
- `api/v2/tasks.py` — `build_task_routes`：`/tasks` CRUD（list/get/patch/delete）
- `api/v2/copilot.py` — `build_copilot_routes`：`/copilot/chat`（同步聚合）+ `/copilot/chat/stream`（SSE）
- `api/v2/health.py` — `build_health_routes`：`/health` + `/health/ready`（Port + 工具列表自检）
- `api/v2/router.py` — `build_v2_router`：把 4 个子路由聚合成单一 `APIRouter`

修改：

- `config.py`（+4 字段，Step 008/009 之后、`model_config` 之前）：
  - `cookie_name="copilot_session"` / `cookie_secure=False` / `cookie_samesite="lax"` / `sse_keepalive_seconds=15`

测试新增：

- `tests/api/__init__.py` + `tests/api/conftest.py`（container/app/client/authed_client fixtures）
- `tests/api/test_auth.py` — 11 个用例
- `tests/api/test_tasks.py` — 11 个用例（list/get/patch/delete + owner 隔离 + 404）
- `tests/api/test_copilot.py` — 7 个用例（含 ask_user 终止 + tool→answer 回路 + 校验/401）
- `tests/api/test_copilot_sse.py` — 4 个用例（解析 `text/event-stream` 帧 + 工具调用流 + 401）
- `tests/api/test_health_and_sse.py` — 3 个用例（health/ready + SSE 工具函数单测）

## 3. 设计决策

| 选择 | 取代方案 | 原因 |
|---|---|---|
| **Closure 路由构建器** `build_*_routes(container) -> APIRouter` | 模块级全局变量 / FastAPI `Depends` 注入 container | 测试时直接传一个全 Fake container 即可拼装独立 app，**零 monkeypatch**；多实例并存（同进程跑两套 container）也不冲突 |
| `/api/v2` 前缀挂在 `app.include_router` 那一层 | 在每个子路由里写死前缀 | 老 `/api/*` 完全不动；将来要再起 v3 只需换前缀 |
| **Cookie session** （httponly + samesite=lax + max-age=jwt_ttl_seconds） | Authorization header / localStorage | 浏览器自动带、抗 XSS；前端不用碰 token |
| **Closure dependency factory** `make_require_owner(container)` | 类的 `__call__` / 子类化 `SecurityBase` | 写法短一半；类型推断更顺；不引入 FastAPI 内部抽象 |
| **DomainError MRO 映射表** | 每个 except 分支单独 `@app.exception_handler` | 新增 14 种异常只要往字典加一行，不用改 import 不用改 handler；查找走 `type(exc).__mro__` 找最具体的祖先 |
| **HTTPException structured detail** = `{"error_code", "message"}` | `detail` 用字符串、状态码当 contract | 401/403/404 的 body 跟其他错误**长得一样**，前端单一 error 处理路径 |
| **SSE 帧手工拼装**（不用 sse-starlette） | `EventSourceResponse` | 项目体量小、依赖越少越好；keepalive/error 帧需要自定义；`StreamingResponse` 完全够用 |
| **`asyncio.wait_for` 包同步迭代器** | 用 `aiter`/原生 async 生成器 | `RunCopilotUseCase.stream()` 是同步生成器（agent 主循环是同步的）；用 `loop.run_in_executor` 桥接到 async，超时即发 keepalive |
| **`tool_args` `None` 单独处理** | `tool_args or {}` | 防御 LLM 真的传 `[]` / `0` 等 falsy 但合法的值——只把 `None` 当缺省 |
| `extra="ignore"` on `ChatRequest` | `extra="forbid"` | 前端调试期允许带 trace_id / debug 字段而不被 422 拒绝 |
| **`/health/ready` 暴露工具名列表** | 只检查端口装配 | `tool_registry` 是 agent 的运行时契约，前端可用来做 feature flag |

### SSE 帧格式

```
event: thought
data: {"text": "..."}

: keepalive

event: tool_call
data: {"tool_name":"search_law","tool_args":{...}}

event: error
data: {"error_code":"AGENT_EXCEPTION","message":"..."}
```

`data:` 单行 JSON；如果 JSON 里有真换行（中文文本时常见），`event_to_sse` 会把 `\n` 替换成空格，防止破帧。
关键说明：JSON 编码本来就会把字符串里的换行转成 `\n` 字面量，所以"裸换行"实际只出现在 `dump` 前的 Python 字符串里。
保险起见仍做一次 `.replace`。

### 异常 → HTTP 映射表

| Domain Error | Status | error_code |
|---|---|---|
| `InvalidToken` | 401 | INVALID_TOKEN |
| `OAuthFlowError` | 400 | OAUTH_ERROR |
| `AuthError` | 401 | AUTH_ERROR |
| `UserNotFound` / `TaskNotFound` | 404 | USER_NOT_FOUND / TASK_NOT_FOUND |
| `OwnerMismatch` | 403 | FORBIDDEN |
| `ToolNotFound` | 400 | TOOL_NOT_FOUND |
| `ToolExecutionError` | 502 | TOOL_EXECUTION_FAILED |
| `DomainError`（兜底） | 500 | DOMAIN_ERROR |

非 DomainError：

- `HTTPException(detail={"error_code", "message"})` → 透传 detail（来自 `require_owner` 的 401）
- `HTTPException(detail=str)` → 包成 ErrorResponse(`HTTP_ERROR`)
- `ValueError` → 400 / `BAD_REQUEST`
- `PermissionError` → 403 / `FORBIDDEN`

## 4. 核心契约

### Endpoint 列表

| Method | Path | Auth | 说明 |
|---|---|---|---|
| POST | `/api/v2/auth/anonymous` | — | 创建匿名 owner + Set-Cookie，**201** |
| GET | `/api/v2/auth/github/login` | — | 返回 `{authorize_url, state}`，前端 redirect |
| GET | `/api/v2/auth/github/callback` | — | code/state 换 JWT + Set-Cookie |
| GET | `/api/v2/auth/me` | optional | `{authenticated, user?}` |
| POST | `/api/v2/auth/logout` | — | 清 cookie |
| GET | `/api/v2/tasks?limit=N` | ✅ | 当前 owner 的 task 列表 |
| GET | `/api/v2/tasks/{id}` | ✅ | task + messages（含 citations） |
| PATCH | `/api/v2/tasks/{id}` | ✅ | 改 title / 合并 collected_facts |
| DELETE | `/api/v2/tasks/{id}` | ✅ | 软返回 `{ok:true}`；404 表示不存在或不归你 |
| POST | `/api/v2/copilot/chat` | ✅ | 一次性聚合返回 `{task_id, events:[…]}` |
| POST | `/api/v2/copilot/chat/stream` | ✅ | SSE 实时流（`text/event-stream`） |
| GET | `/api/v2/health` | — | `{status, version}` |
| GET | `/api/v2/health/ready` | — | `{ports_loaded:{...}, tools:[…]}` |

### 装配方式（main.py 假想代码，本步骤不动）

```python
from api.v2 import build_v2_router
from api.v2.errors import install_exception_handlers
from app.container import AppContainer

container = AppContainer.from_settings(settings)
app.include_router(build_v2_router(container), prefix="/api/v2")
install_exception_handlers(app)
```

## 5. 与外部服务的关系

- 仅依赖 `app.container.AppContainer`（已在 Step 008/009 落地，纯组合）
- 不直接 import `infra/*`，不直接 import 任何 SDK；Port 全由 container 注入
- SSE 端点写明 `Cache-Control: no-cache` 与 `X-Accel-Buffering: no`，兼容 nginx/cloudflare 反代

## 6. 当前实现范围

✅ 已实现：

- 10 个生产文件 + 4 个 config 字段 + 36 个 API 测试全绿
- DomainError MRO 映射 / HTTPException 结构化 detail 透传 / ValueError / PermissionError 兜底
- SSE 三种帧：event / keepalive / error
- Cookie 全配置项化（domain 暂未支持，留给部署期反向代理处理）

❌ 未实现（按设计推迟）：

- **未挂到 `main.py`** —— Step 010 只保证可被任意 `FastAPI()` 装配；正式接入老应用作为单独 step
- **risk 路由** —— Step 012 PR-7 一起做
- **WebSocket** —— 设计文档原本就只承诺 SSE
- **rate limiting / CORS** —— 应在 ASGI 中间件层做，不在路由层

## 7. 暂未实现 / TODO

- 当前 `stream_with_keepalive` 每次超时都会重启 executor task（cancel + 新建）。生产 QPS 高时这里可能产生大量短命 thread；
  Step 011 接入前要做一次压测，必要时改为 `loop.run_in_executor` 复用 thread + `asyncio.Queue`
- `/auth/github/login` 没有把 state 写到 cookie，依赖 `FakeAuth/GitHubOAuthProvider` 自己存。正式部署需考虑 state cookie 防 CSRF
- `chat` 同步端点会把所有 event 累积到内存再返回——当 agent 步数大或 facts 大时可能撑爆；调用方应优先用 stream
- 没有 OpenAPI 完整 example payload；FastAPI 自动生成的 schema 已够前端开工

## 8. 测试与验证

```bash
# 新增测试
pytest tests/api -q
# 36 passed

# 全量回归
pytest -q
# 373 passed, 16 warnings in 12.25s   (337 -> 373, +36)

# Lint
ruff check api/v2 tests/api
# All checks passed!

# Type check (新代码)
mypy api/v2
# Success: no issues found in api/v2 (与 mypy 输出过滤一致)
```

新代码 ruff 0、mypy 0。`config.py:134 settings = Settings()` 的 "Missing named argument" 仍是 Step 008 起就有的存量噪音
（pydantic-settings + mypy 对 `Field(...)` 默认值识别不全），与 Step 010 增量无关。

### 关键测试覆盖点

- `test_auth.py`：匿名→cookie / 两次匿名独立 / `/me` 三态（未登/有效/无效 cookie）/ logout 清 cookie /
  github login URL / callback 成功 / callback state 无效 → 400 / callback 缺参 → 422 / `require_owner` 无 cookie → 401
- `test_tasks.py`：空列 / 自己的 task 列表（过滤别人的）/ limit 夹紧 / get 自己 200 / get 别人 404 /
  get 不存在 404 / patch title / patch facts 合并 / patch 别人 404 / delete 自己 / delete 别人 404（且不实际删）
- `test_copilot.py`：final_answer 单跳 / 复用已有 task_id（无 task_created） / 401 / 空 message 422 /
  超额附件 422 / ask_user 终止（无 answer） / tool→answer 完整回路（6 个事件类型有序）
- `test_copilot_sse.py`：`text/event-stream` Content-Type / 按 `\n\n` 切帧并 JSON 解析 / 工具回路在 SSE 中可见 / 401
- `test_health_and_sse.py`：health 字面值 / ready 工具集合 = `{search_law, search_user_docs, web_search, evidence_judge}` /
  SSE 工具函数（`event_to_sse` 不破帧 / keepalive 字面 / error 帧）
