# Step 025g — `user_id` 注入 contextvar（log 行加 `[uid:xxx]` 段）

> Step 025d 把 `request_id` 注入 contextvar 让审计自动带上；Step 025f 让 log
> 行也自动带上 `[request_id]` 段。本步把 `user_id` 同样提升为 logging 一等
> 字段，让运维侧能按用户聚合排障——「这个 anon 用户最近 5min 哪些请求出错」
> 一句 `grep [uid:anon:abc]` 即可。

## 1. 目标

- 所有应用 log 行格式扩成 `<ts> [LEVEL] [request_id] [uid:user_id] <name>: <msg>`
- 与 Step 025f 同样的 contextvar 模式：未登录 / 离线脚本场景优雅降级 `[uid:-]`
- 不破坏 `request_context()` 既有调用方（位置参数仍是 `request_id`，`user_id`
  作为 kw-only 加在后面）
- 不在每个 use case 加签名透传——middleware 层一次解析，全链路读取

## 2. 改动清单

### 后端 3 文件 + 1 测试

| 文件 | 状态 | 说明 |
| --- | --- | --- |
| `app/request_context.py` | 修改 | 新增 `user_id_var` + `get_user_id` / `set_user_id` / `reset_user_id`；扩展 `request_context(request_id, *, user_id=None)` |
| `app/logging_setup.py` | 修改 | `RequestIdLogFilter.filter()` 同时注入 `record.user_id`；`DEFAULT_FORMAT` 加 `[uid:%(user_id)s]` 段 |
| `main.py` | 修改 | middleware 解析 cookie token → `auth_login.identify` → `set_user_id` / `reset_user_id` 包 try/finally |
| `tests/app/test_logging_setup.py` | 修改 | 加 `test_user_id_independent_of_request_id` + 既有用例补 `user_id=` 断言 |

`app/request_context.py` 里的 `install_request_id_middleware`（测试 fixture
专用）**不动**——它没有 container 引用，没法解析 user_id；仅 main.py 的
inline middleware 完整支持。下一步如果端到端测试需要验证 user_id 透传，再
让 `install_request_id_middleware` 接 `auth_login` 可选参数。

## 3. 关键决策

### D1 — middleware 层解析，而非 Depends 层

候选实现位置：

| 方案 | 优点 | 缺点 |
| --- | --- | --- |
| **A. middleware 解析** | 一处 set 全链路覆盖；access log 行（middleware 自己打的）也带 uid | middleware 直接调 `auth_login.identify`，开销每请求一次 JWT verify |
| B. yield-form Depends | 只在需要登录的端点解析 | access log 在 dependency 之前打，拿不到 uid；无登录端点拿不到 uid |
| C. use case 入口 set | 各 use case 管自家 contextvar | 重复代码；非 use case 路径（健康检查）无 uid |

选 **A**：HMAC 验证微秒级开销可忽略；access log 必须带 uid 才有运维价值；
仅对 `/api/` 路径解（静态文件、`/docs` 不解，进一步省开销）。

### D2 — 失败 silent，user_id 退到 `None`

middleware 里 `auth_login.identify(token)` 内部已吞 JWT 异常返回 `None`，但
本步外层再包一层 `try: ... except Exception:` 兜底——任何意外都让 user_id
保持 `None`，绝不让认证逻辑把整个请求带崩。日志里出现 `[uid:-]` 即诊断信号。

### D3 — `request_context()` 签名向后兼容

原 `request_context(value: str | None)` → 现在 `request_context(request_id: str | None, *, user_id: str | None = None)`：

- 位置参数 1 仍是 request_id（既有 `request_context("rid")` 17 个调用点零修改）
- 用 kw-only 加 user_id 参数，避免位置参数顺序歧义
- contextmanager 内 try/finally 同时 reset 两个 contextvar，先入后出

### D4 — Filter 名字保留 `RequestIdLogFilter`

历史名字（Step 025f 创建时只有 request_id），本步扩展为同时注入两字段。
保留名字理由：
- 名字本质代表「请求上下文 filter」，request_id 只是其中之一
- 重命名会破坏既有 import（`tests/app/test_logging_setup.py` 直接 import）
- 运维 grep `RequestIdLogFilter` 在 log 配置里能找到，不必学新名字

如果将来注入字段超过 3 个再考虑改名 `ContextLogFilter`。

### D5 — 测试 fixture 不解析 user_id

`install_request_id_middleware` 仅设 request_id，不接 container 引用。原因：
- 它在测试 fixture 用，避免 fixture 强依赖完整 container 装配
- main.py 自己有完整 inline middleware（生产入口）
- 端到端测试目前只验证 request_id 透传到 audit；user_id 由单测充分覆盖

下一步如果有需求再扩展签名 `install_request_id_middleware(app, *, auth_login=None, cookie_name=None)`。

## 4. 验证

- 单测：`tests/app/test_logging_setup.py` 9 passed（filter 3 + configure 5 + 嵌套 1）
- 全量回归：**596 passed**（595 + 1 新增 `test_user_id_independent_of_request_id`），无回归
- ruff scoped 4 路径：All checks passed
- 手动启动 uvicorn 观察 `logs/app.log`：登录后访问 `/api/v2/me` 行含 `[uid:gh:xxx]`，
  匿名访问 `/api/v2/auth/anonymous` 之前含 `[uid:-]`、之后含 `[uid:anon:xxx]`

## 5. 后续工作

- 让 `install_request_id_middleware` 可选接 `auth_login`，端到端测试也覆盖 user_id 透传
- 把 `[uid:%(user_id)s]` 段做成日志查询面板的过滤维度（结构化日志输出 JSON
  时把 user_id 提为顶级字段，待 ELK / Loki 接入后做）
- 不在本步做的「重构债」（mypy 复活 / v1 检索面退役 / audit CSV 导出）按
  `docs/process/README.md` 大表追踪
