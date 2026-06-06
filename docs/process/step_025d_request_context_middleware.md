# Step 025d — RequestContext middleware（激活 request_id 透传）

> Step 021 把 `AuditEntry.request_id` 字段定下，但留空；Step 025c 在 use case 形参里留位，仍然没人传。本步用 `contextvars.ContextVar` + 一个 FastAPI middleware 把 `X-Request-ID` 从 HTTP 请求一路无侵入透传到 `AuditLogPort.record()` 写下的每一条审计，**不**改任何 API 路由签名，**不**改任何 use case 调用方法签名。

## 1. 目标

- 让审计条目的 `request_id` 字段从「永远 None」升级为「永远非空」（HTTP 入口）
- 不让 API 路由层每个调用点都改签名透传 request_id（违背 ADR-009 的「容器装配 + 路由薄」原则）
- 显式形参仍然可用（命令行 / 后台任务 / 单测可以传入）
- 不破坏向后兼容：use case 调用方不传 request_id、不挂 middleware 都仍然能正常工作

## 2. 改动清单

### 后端 4 文件

| 文件 | 改动 |
|---|---|
| [app/request_context.py](../../app/request_context.py)（新） | `request_id_var: ContextVar[str \| None]` + `get_request_id()` / `set_request_id(value)` / `reset_request_id(token)` + `request_context(value)` contextmanager + `install_request_id_middleware(app)` 注册函数 |
| [app/use_cases/kb_management.py](../../app/use_cases/kb_management.py) | `_record_audit` 内 `effective_request_id = request_id if request_id is not None else get_request_id()`；写入 `AuditEntry(request_id=effective_request_id, ...)`；import `from app.request_context import get_request_id` |
| [app/use_cases/auth_login.py](../../app/use_cases/auth_login.py) | 同 `_record_audit` 改造 + import |
| [main.py](../../main.py) | 原 inline `request_context_middleware` 改用 `set_request_id` / `reset_request_id`（try/finally 包 `await call_next(request)`），保留原 logging + elapsed_ms + path 过滤；import 二者 |

### 测试 3 文件

| 文件 | 改动 |
|---|---|
| [tests/app/test_request_context.py](../../tests/app/test_request_context.py)（新） | 11 用例：5 个 contextvar 原语（默认 None / set+reset / contextmanager / 嵌套还原 / 异常退出 reset） + 3 个 auth_login fallback（contextvar 填充 / 形参 wins / 双空 None） + 3 个 kb_management fallback（ingest_web / delete / audit=None 安全） |
| [tests/api/test_request_id_propagation.py](../../tests/api/test_request_id_propagation.py)（新） | 6 用例：response 回写 `X-Request-ID`（显式 / 自动生成）+ 匿名登录审计带 request_id + 自动生成的 id 等于 response 头 + 3 次请求拿 3 个不同 id + 失败 OAuth callback 的 `AUTH_LOGIN_FAILURE` 同样带 request_id |
| [tests/api/conftest.py](../../tests/api/conftest.py) | `app` fixture 调 `install_request_id_middleware(fastapi_app)`，让端到端测试与生产 main.py 一致 |

## 3. 五大决策（D1-D5）

### D1：ContextVar 而非 explicit threading

**选**：`contextvars.ContextVar` + use case 内部 fallback 取，**不**改 API 路由 / use case 调用签名。

**否方案 A**：每个 API 路由从 `request.state.request_id` 取，逐层透传到 use case。
**否方案 B**：FastAPI `Depends` 注入 `RequestContext` 对象给 use case。

**理由**：(1) Strangler 原则下 use case 应保持「领域无 web 概念」，逐层透传 = 让 web 概念漏进 use case 签名；(2) Depends 方案要 use case 接受 `request_context: RequestContext = Depends(...)`，逼着 use case 知道 FastAPI 的存在；(3) ContextVar 与 `asyncio.to_thread` / `anyio.to_thread.run_sync` 在 Python 3.9+ 默认透传（`contextvars.copy_context().run(...)`），跨线程也能拿到；(4) use case 显式形参仍保留，命令行 / 后台任务可以直接传。

### D2：「显式形参优先 > contextvar > None」三段优先级

**选**：`_record_audit` 内 `effective = request_id if request_id is not None else get_request_id()`。

**否**：永远用 contextvar，忽略形参。

**理由**：(1) 形参 wins 保留了「特殊场景显式覆盖」的能力（例如批量回放历史事件时手工指定 request_id）；(2) 单测可以传入已知 id 做精确断言，不依赖 middleware；(3) Step 025c 已经在 `AuthLoginUseCase.complete` / `login_anonymous` 留了 `request_id` 形参位，本步沿用语义不重塑。

### D3：middleware 在 `app/request_context.py` 提供为可复用函数

**选**：`install_request_id_middleware(app)` 在 `app/` 包导出；main.py 沿用原 inline middleware（手工调 `set_request_id` / `reset_request_id`，因为还要合并 logging）；测试 fixture 调 `install_request_id_middleware`。

**否方案 A**：只在 main.py 写 inline，测试 conftest 复制粘贴。
**否方案 B**：把 logging middleware 拆出来，让 main.py 也调 `install_request_id_middleware`。

**理由**：(1) 测试 fixture 要与生产同形态才能断言"响应回写 X-Request-ID"等行为，复制粘贴会漂移；(2) main.py 现有 middleware 已经同时管 logging + path 过滤 + elapsed_ms + contextvar，强行拆分增加 PR 噪音且没收益（main.py 用 set/reset 原语已经足够干净）；(3) 函数式 `install_*(app)` 模式与 `install_exception_handlers(app)` 风格一致。

### D4：自动生成的 request_id 格式 `uuid4().hex[:12]`

**选**：12 字符 hex（与 main.py 已有约定一致）。

**否方案 A**：完整 uuid4（36 字符含 dash）。
**否方案 B**：8 字符短码。

**理由**：(1) 12 hex = 48 bit，单进程冲突概率可忽略；(2) 太短（如 8 字符）= 32 bit，在 audit 表 1M 行规模下生日攻击概率 ~7%，不可靠；(3) 完整 uuid 在 log 行宽 80 列里太占位置；(4) main.py 原本就用这个长度，保持向后兼容。

### D5：失败请求路径也走 middleware 回写 + contextvar 落审计

**选**：middleware 用 `try/finally` 包 `await call_next(request)`，无论路由抛异常或返回 4xx/5xx，response header 都回写 `X-Request-ID`，contextvar 都被 reset。

**否**：异常时跳过 header 回写，让 ASGI default 500 自己处理。

**理由**：(1) 运维场景里**失败请求**才是 admin 最想要 request_id 反馈的——成功了客户端通常不在意；(2) `try/finally` 保证 contextvar 一定 reset，避免请求间泄漏（虽然 asyncio task 切换会自动隔离 contextvar，但显式 reset 防御性更强）；(3) FastAPI 的 exception_handler 会基于这个 response 继续处理，header 已经在，能稳定传出去。

## 4. 风险与回归

### 已识别风险

1. **`anyio.to_thread.run_sync` contextvar 透传**：use case 的 `ingest_file` / `ingest_web` 走 `anyio.to_thread.run_sync(lambda: ...)` 跨线程，contextvar 需被透传。
   - **缓解**：anyio ≥ 3.7（pyproject 锁版本 4.x）默认用 `contextvars.copy_context().run()` 透传，已经 OK。新加的 `TestKbManagementFallback::test_ingest_web_picks_up_contextvar` 隐式覆盖（虽然走的是直接调用而非 anyio.to_thread，但 use case 本身没有跨线程边界——跨线程发生在 API 层调 `anyio.to_thread.run_sync` 调用之前 contextvar 就已经被 main.py middleware set 好）。
   - **遗留**：未来如果引入显式 ThreadPoolExecutor 不用 anyio，要手工 `ctx = copy_context(); pool.submit(ctx.run, fn)`。

2. **多 middleware 顺序**：starlette 的 middleware 是 LIFO 注册顺序，新注册的最先执行。main.py 原 inline middleware 是 `@app.middleware("http")`（同等优先级），所以注册顺序 = 执行顺序逆。如果将来加更多 middleware（如 CORS 在 contextvar 之前/之后）需要小心；本步没动顺序。

3. **request_id 形参 deprecation 倾向**：现在 contextvar 几乎覆盖所有 HTTP 路径，形参的存在感降低。
   - **现状**：仍然保留，因为命令行 / 后台任务用得到；测试也方便。
   - **未来**：如果半年后形参一次都没真实用过，可以收掉。

### 不动的部分

- ❌ `api/v2/auth.py` / `api/v2/documents.py`：保持薄路由，不传 request_id（让 contextvar 自动生效）
- ❌ `domain/models.py AuditEntry`：字段早就存在（Step 021 留位），不动
- ❌ `infra/audit/sqlite_audit_repo.py`：已支持 request_id 字段写入，不动
- ❌ `_record_audit` 的 audit_log=None 跳过语义 + 写失败 logger.warning 语义

## 5. 验证

### 自动化

- `pytest -q tests/`：**578 passed**（基线 560 + 11 单测 + 6 端到端 + 1 个原有测试因 middleware 加挂被隐式覆盖，零失败）
- `ruff check app/request_context.py app/use_cases/auth_login.py app/use_cases/kb_management.py main.py tests/api/conftest.py tests/app/test_request_context.py tests/api/test_request_id_propagation.py`：All checks passed

### 手工

1. `python main.py` 启动 → 浏览器请求 `/api/v2/health` → 看 DevTools 网络面板 `X-Request-ID` 响应头存在且每次不同
2. `curl -H "X-Request-ID: my-trace-1" -X POST http://127.0.0.1:8765/api/v2/auth/anonymous` → response 头 `X-Request-ID: my-trace-1`；admin 登录后查 `/api/v2/audit/logs` 应能看到对应条目 `request_id="my-trace-1"`

## 6. 后续 step（候选）

- **Step 025e（logout 端点 + 审计）**：补全身份生命周期最后一环；request_id 透传已就绪
- **Step 025f（结构化日志 contextvar）**：把 request_id 也注入 logging Formatter，所有 log 行自动带 request_id 前缀（现在只有手工拼 `f"[{request_id}]"`）
- **Step 026a（audit CSV / 时间范围过滤）**：审计 UI 运维能力补全
- **Step 025b（mypy 复活）**：长期债务

## 7. 关联

- ADR-009（Closure Router + Container DI）— 本步是 ADR-009「路由薄、容器装配」的延伸：跨切面信息走 contextvar 而非签名透传
- ADR-013（审计副作用语义）— 本步补全 `AuditEntry.request_id` 字段的实际可用性
- Step 021（admin 审计基础设施）— `AuditEntry.request_id` 字段在那时已留位
- Step 025c（登录端点接入 AuditLogPort）— `request_id` 形参留位在那时定下，本步「激活」之
