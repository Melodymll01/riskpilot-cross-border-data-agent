# Step 025c — 登录端点接入 AuditLogPort（审计闭环）

> Step 021 把 `AuditLogPort` 端口 + `SqliteAuditLogRepo` 实现 + `AuditAction` 常量集落地，但当时只串接了 `KbManagementUseCase` 的三个写动作。本步把"谁在什么时候登录成功 / 失败"也并入同一条审计管道，让 admin 在 `/api/v2/audit/logs` 看到的"系统活动"真的覆盖所有身份相关写动作，把 login 路径从"侧信道日志"升级为"一等公民审计事件"。

## 1. 目标

- 让登录三动作（OAuth 完成 / OAuth 失败 / 匿名创建）写入与 KB 操作同一份 `audit_log` 表
- 用 use case 层挂 hook（而非 API 层），与 `KbManagementUseCase` 的现成 pattern 对齐——`api/v2/auth.py` 保持薄路由零改动
- 失败路径同样落审计（`success=False` + `error` + `actor_id="system:unknown"`），让 admin 能识别"短时间内大量失败回调"这类异常迹象
- 在不修改 `AuditEntry` 形状、不引入新 port 的前提下完成；为将来接 RequestContext middleware 预留 `request_id` 形参位

## 2. 改动清单

### 后端 3 文件

| 文件 | 改动 |
|---|---|
| [domain/models.py](../../domain/models.py) | `AuditAction` 追加 3 常量：`AUTH_LOGIN_SUCCESS = "auth.login_success"` / `AUTH_LOGIN_FAILURE = "auth.login_failure"` / `AUTH_ANONYMOUS_CREATE = "auth.anonymous_create"` |
| [app/use_cases/auth_login.py](../../app/use_cases/auth_login.py) | `__init__` 加 kw-only `audit_log: AuditLogPort \| None = None`；`complete` 用 try/except 包 `complete_oauth`，成功落 `AUTH_LOGIN_SUCCESS`、`OAuthFlowError` 落 `AUTH_LOGIN_FAILURE` 后 re-raise；`login_anonymous` 成功落 `AUTH_ANONYMOUS_CREATE`；`complete` / `login_anonymous` 加 kw-only `request_id: str \| None = None` 形参位（API 层暂未透传，保留契约）；新增 `_record_audit` 私有 helper（语义与 `KbManagementUseCase._record_audit` 一致：audit=None 跳过，写失败仅 logger.warning）；`identify` / `require` 只读路径不落审计；`begin` 只颁发 state url，不落审计 |
| [app/container.py](../../app/container.py) | 第 122 行 `AuthLoginUseCase(self.auth)` → `AuthLoginUseCase(self.auth, audit_log=self.audit_log)` |

### 测试 2 文件

| 文件 | 改动 |
|---|---|
| [tests/app/test_auth_login.py](../../tests/app/test_auth_login.py) | 新增 `TestAuditHooks` 类（6 用例）：`audit_log=None` 旧调用兼容 / `complete` 成功 → `AUTH_LOGIN_SUCCESS` + `actor_id=github:alice` + `extra={"provider":"github"}` / `complete` `OAuthFlowError` → `AUTH_LOGIN_FAILURE` + `success=False` + `actor_id="system:unknown"` + `extra` 含 `reason` / `login_anonymous` → `AUTH_ANONYMOUS_CREATE` + `actor_id=anon:...` / `request_id` 透传 / `identify` + `require` 只读不落审计 |
| [tests/api/test_audit.py](../../tests/api/test_audit.py) | `admin_client` fixture 在 `_login_as_admin` 后 `container.audit_log.entries.clear()`——清除登录本身产生的 `AUTH_LOGIN_SUCCESS` 条目，让 `TestList` / `TestFilters` / `TestPagination` 只断言 `_seed` 出来的数据；其他逻辑零改动 |

## 3. 五大决策（D1-D5）

### D1：在 use case 层挂 hook，API 层零改动

**选**：审计逻辑全部落在 `AuthLoginUseCase`，`api/v2/auth.py` 不动。

**否**：API 层装饰器或路由内手动 `container.audit_log.record(...)`。

**理由**：(1) 与 `KbManagementUseCase` 现成 pattern 对齐——审计是业务事实而非传输事实，应该在 use case 边界发生；(2) API 层装饰器会逼着每个路由都包一层，复用差；(3) use case 层挂 hook 让"调命令行脚本直接调 use case"也能产生审计，不依赖 HTTP 入口。

### D2：失败用 `actor_id="system:unknown"` 占位

**选**：`complete` 抛 `OAuthFlowError` 时 `actor_id = "system:unknown"`，配合 `success=False` + `error=str(exc)` + `extra.reason = type(exc).__name__`。

**否方案 A**：跳过审计（只成功才写）。
**否方案 B**：用调用方提供的"猜测 user_id"（比如从 state 反查）。

**理由**：(1) 失败本身就是 admin 最关心的信号（短时间内大量回调失败 = 攻击迹象），跳过等于把可观测性砍掉；(2) 失败时通常没有可信 user_id（OAuth 还没完成 callback），任何"猜测"都不可靠；(3) `system:unknown` 是 `AuditEntry.actor_id` 字段的默认值，复用现成语义。

### D3：`begin` 不落审计，只 `complete` / `login_anonymous` 落

**选**：`begin(provider)` 只颁发 `(authorize_url, state)`，不产生身份事件，不落审计。

**否**：每次 `begin` 也写一条 `AUTH_LOGIN_START`。

**理由**：(1) `begin` 是无副作用的 URL 生成，每次刷新前端登录页都会触发，写审计等于把审计表灌成噪音；(2) 攻击者无法通过单纯调 `begin` 造成可观测危害，没有 admin 关心的"威胁面"；(3) 真正能验证身份的是 `complete`，那时的 `actor_id` 才有意义。

### D4：`request_id` 形参先留位，本步不透传

**选**：`complete` / `login_anonymous` 签名加 `request_id: str | None = None`，但 API 层暂时不传，使审计条目的 `request_id` 始终为 `None`。

**否**：现在就在 API 层从 FastAPI `Request` 提取 `X-Request-ID` header 并透传。

**理由**：(1) 与 Step 021 决策 D3（"先留位待 RequestContext middleware"）一致；(2) 一次性补 RequestContext middleware 比"每个 use case 自己处理"更聚拢；(3) 形参留位让将来接入零回归——只在 API 层一行代码透传即可。

### D5：`resource` 字段用 `oauth:{provider}` / `"anonymous"`

**选**：登录成功 / 失败 → `resource = f"oauth:{provider}"`（如 `"oauth:github"`）；匿名 → `resource = "anonymous"`。

**否方案 A**：`resource = user.user_id`（与 actor_id 一致）。
**否方案 B**：`resource = "/api/v2/auth/github/callback"`（HTTP 路径）。

**理由**：(1) `resource` 字段语义是"被操作的对象"——登录是"对某个 provider 的认证流程"，不是"对 user 的操作"；(2) 与 KB 的 `resource = source_name` 形成对照：动作对象是 provider/anonymous，actor 是结果用户；(3) HTTP 路径会泄露传输细节到审计语义，且未来 SSO/WebAuthn 接入会破坏一致性。

## 4. 风险与回归

### 已识别风险

1. **测试污染**：`tests/api/test_audit.py` 的 admin fixture 通过登录拿 admin token，登录本身现在会写一条审计 → 之前断言 `entries == []` 的用例全部红。
   - **缓解**：fixture 内 `audit_log.entries.clear()`，影响范围只在测试 setup。一次性、显式、就近，比"测试断言全部 +1"或"过滤 auth.*"更干净。

2. **静默失败被吞**：审计写失败只 logger.warning 不抛——登录本身仍然成功。
   - **现状**：这是 `KbManagementUseCase` 既定语义（"业务主路径优先于可观测性")，本步沿用。
   - **可观测性**：失败会进 Python logging，运维侧 ELK 仍能捕获。

3. **匿名用户 actor_id 不稳定**：`anon:xxx` 是每次 `create_anonymous` 新生成的 8 字符 hex，admin 翻审计时同一物理人可能呈现多条不同 actor_id。
   - **决策**：这是匿名设计的本性（无身份 = 无连续性），不在本步处理。如果将来想关联，应在 cookie 层引入设备指纹，与审计无关。

### 不动的部分

- ❌ `api/v2/auth.py`：保持薄路由，审计责任全在 use case 层
- ❌ `service.py`：v1 已删除（Step 016d），不应回潮
- ❌ cookie / session 处理：属于 API 层职责，与 ADR-006 一致
- ❌ `request_id` 透传：留待将来 RequestContext middleware 一次性补

## 5. 验证

### 自动化

- `pytest tests/ -q` → **560 passed**（基线 555 + 新增 6 `TestAuditHooks` + 调整 1 fixture = 净增 5；`test_jwt_issuer::test_tampered_token_returns_none` 偶发 flaky 单跑通过，与本步无关）
- `ruff check domain/models.py app/use_cases/auth_login.py app/container.py tests/app/test_auth_login.py tests/api/test_audit.py` → All checks passed

### 手工

- 启动 `python main.py` → 登录 GitHub → 进 admin UI 看审计页 → 应能看到一条 `auth.login_success` + `actor_id=github:<your_login>` + `resource=oauth:github`
- 在 GitHub OAuth 回调过程中故意改 state 参数 → 应能看到一条 `auth.login_failure` + `actor_id=system:unknown` + `error` 非空

## 6. 后续 step（候选）

- **Step 025d（RequestContext middleware）**：FastAPI middleware 把 `request_id`（X-Request-ID header 或 uuid4）放到 contextvar；API 层调用 use case 时透传 → 本步形参位生效，审计条目第一次有真实 `request_id`
- **Step 025e（登出端点 + 审计）**：当前没有 `/api/v2/auth/logout`，前端 cookie 在过期前一直有效；加 logout 端点同步落 `auth.logout` 审计
- **Step 025f（per-actor 速率限制）**：基于审计表的 `auth.login_failure` 计数做 fail2ban 风格的源 IP / actor 临时封禁

## 7. 关联

- ADR-008（owner_id 多租户）— 本步是 actor 维度的可观测性，与 ADR-008 的数据维度互补
- ADR-013（审计副作用语义）— 本步是 ADR-013 决策"audit 写失败不影响主路径"的一次应用
- Step 021（admin 审计基础设施落地）— 直接前置
- Step 023（admin 审计 UI）— 本步事件登场之处
- Step 025a（owner_id 多租户）— 本步同期的另一块"身份可见性"
