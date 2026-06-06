# Step 025e — logout 端点接入 AuditLogPort（身份生命周期闭环）

> Step 025c 把登录 / 失败 / 匿名创建三条链路并入审计；Step 025d 让审计条目自动带上 `request_id`。本步补上身份生命周期的最后一环——`POST /api/v2/auth/logout` 也落审计，让 admin 在 `/api/v2/audit/logs` 能完整看到「谁在什么时间用什么 request_id 登入、何时退出」。

## 1. 目标

- 让登录态下的 logout 在审计表留下 `auth.logout` 一条
- **不**让未登录状态的 logout 写审计（避免任意客户端 spam `POST /logout` 把表灌满 `system:unknown` 噪音）
- 复用 Step 025c 的 use case 层挂 hook 模式 + Step 025d 的 contextvar 透传，零路由签名扩张
- 不破坏前端既有 logout 体验（cookie 清理时机与响应体不变）

## 2. 改动清单

### 后端 3 文件

| 文件 | 改动 |
|---|---|
| [domain/models.py](../../domain/models.py) | `AuditAction` 追加 `AUTH_LOGOUT = "auth.logout"` 常量 |
| [app/use_cases/auth_login.py](../../app/use_cases/auth_login.py) | 新增 `logout(token, *, request_id=None) -> str \| None` 方法：调 `self.identify(token)` 解出 user_id；user_id 为 None → 直接返回 None **不**写审计；user_id 非 None → 写 `AUTH_LOGOUT` + actor=user_id + resource=`session` + extra={} 然后返回 user_id |
| [api/v2/auth.py](../../api/v2/auth.py) | logout 路由签名加 `request: Request`；从 cookie 取 `container.settings.cookie_name` → 调 `container.auth_login.logout(token)`（返回值丢弃）→ `clear_session_cookie` → 返回 `OkResponse`；response 形状与原契约一致 |

### 测试 2 文件

| 文件 | 改动 |
|---|---|
| [tests/app/test_auth_login.py](../../tests/app/test_auth_login.py) | 新增 `TestLogout` 类（7 用例）：valid token → 审计 / None token noop / empty token noop / invalid token noop / `audit_log=None` 兼容 / `request_id` 透传 / github 登录后 logout 行 actor=`github:alice` |
| [tests/api/test_auth.py](../../tests/api/test_auth.py) | `TestLogout` 补 2 用例：登录态 logout 写 `AUTH_LOGOUT` 审计（actor=user.user_id + resource=`session` + request_id 来自 `X-Request-ID` header，Step 025d 链路对端验证） + 未登录 logout silent（不增审计） |

## 3. 五大决策（D1-D5）

### D1：use case 层挂 hook，cookie 清理留在 API 层

**选**：`AuthLoginUseCase.logout` 只负责审计；`clear_session_cookie(response, settings)` 留在 `api/v2/auth.py` 路由内。

**否**：把 cookie 清理也搬进 use case（让 use case 接 `Response` 对象）。

**理由**：(1) cookie 是传输层关切，use case 应保持「领域无 web 概念」（与 Step 025c D1 / ADR-009 一致）；(2) 同样的 cookie 形状未来如果切换到 Authorization header 或 WebSocket，use case 不需要动；(3) 路由层一行 `clear_session_cookie(...)` 比 use case 内透传 Response 更易读。

### D2：未登录的 logout 不写审计（silent no-op）

**选**：`uc.logout(token)` 在 `identify(token) is None` 时直接返回 None，**不**写审计。

**否方案 A**：写一条 `AUTH_LOGOUT` + actor=`system:unknown` + success=False。
**否方案 B**：写一条 `auth.logout_attempt_unauthed` 单独 action。

**理由**：(1) `POST /auth/logout` 是无身份校验的公开端点，任何客户端都能调，写无 actor 的审计 = 给攻击者一个免费 DOS 灌表的渠道；(2) 与登录失败不对称是有意识的——登录失败有「攻击迹象」价值（短时大量 401 = 暴破），但 logout 失败没有等价信号；(3) `auth.logout_attempt` 新 action 没有读者会用，纯增加噪音。

### D3：resource = `"session"`，extra = `{}`

**选**：resource 字段用 `"session"`，与 Step 025c 的 `"oauth:{provider}"` / `"anonymous"` 形成 trio。

**否**：resource = `user_id`（与 actor 重复）或 resource = `"jwt"`。

**理由**：(1) logout 的「被操作对象」是会话本身（无论它最初是 oauth 还是 anonymous 颁发），用统一的 `session` 标签便于 admin 按 resource 聚合；(2) 后续如果引入"撤销其它设备会话" / "session expire on close" 等动作，同样属于 session 维度；(3) extra 空字典：logout 没有 provider 信息要带（识别凭证已在 token 解出的 user_id 里）。

### D4：返回 user_id 而非 bool / None

**选**：`logout(token) -> str | None`，成功返回 user_id，失败返回 None。

**否**：返回 bool（写没写审计的指示）。

**理由**：(1) user_id 信息含量更高——上游调用者（命令行 / 内部脚本）可能想日志记录"谁登出了"；(2) None 本身就承担 falsy 角色（`if uc.logout(token):` 仍可读）；(3) bool 丢失信息且无法复原。

### D5：路由签名加 `Request` 不加 `Depends(identify)`

**选**：`def logout(request: Request, response: Response)` 然后 `token = request.cookies.get(name)`，**不**用 `Depends(make_identify_owner)`。

**否**：`def logout(response: Response, owner_id: str | None = Depends(identify))` 然后 use case 接 user_id。

**理由**：(1) `Depends(identify)` 拿到的是已解码的 user_id，但 use case 内部还要再做一次 `identify(token)`——重复解码；(2) use case 自己接 token 字符串、自己解码，能保持「use case 是身份逻辑的唯一持有者」（API 层只负责传输层适配）；(3) `Depends` 注入会让单测 mock 路径多一层，直接传 token 字符串测试更直观。

## 4. 风险与回归

### 已识别风险

1. **过期 token 仍然落审计成功**：JWT 已过期但还能解码出 user_id 时（取决于 verify_jwt 实现），logout 会写 success=True 的审计。
   - **现状**：`AuthService.verify_jwt` 会在 exp 校验失败时返回 None（与 Step 007 实现一致），过期 token 走 noop 分支。FakeAuth 同语义。
   - **结论**：风险闭合。

2. **审计写失败被吞**：`_record_audit` 写库异常仅 logger.warning，logout 主路径仍然成功。
   - **决策**：沿用 Step 025c 既定语义。logout 是用户主动行为，业务连续性 > 审计完备性。

3. **前端 logout 行为变更**：响应体仍是 `{"ok": true}`、cookie 仍被清理；前端 `frontend/auth.js` 不需要改。
   - **验证**：`tests/api/test_auth.py::TestLogout::test_clears_cookie` 原用例保持绿色。

### 不动的部分

- ❌ `frontend/auth.js`：响应契约未变，无需改动
- ❌ `infra/audit/sqlite_audit_repo.py`：`AUTH_LOGOUT` 在 action 字段是普通字符串，repo 无需感知
- ❌ `api/v2/audit.py`：admin 查询端点已支持 `?action=auth.logout` 过滤（Step 023 已落）
- ❌ `app/container.py`：`AuthLoginUseCase` 注入链未变（Step 025c 已加 audit_log）

## 5. 验证

### 自动化

- `pytest -q tests/`：**587 passed**（基线 578 + 7 use case 单测 + 2 端到端 = 净增 9）
- `ruff check domain/models.py app/use_cases/auth_login.py api/v2/auth.py tests/app/test_auth_login.py tests/api/test_auth.py`：All checks passed

### 手工

1. 浏览器 `python main.py` 启动 → GitHub 登录 → 点退出 → 进 admin UI 审计页 → 应看到一条 `auth.logout` + actor=`github:<your-login>` + resource=`session`
2. 未登录直接 `curl -X POST http://127.0.0.1:8765/api/v2/auth/logout` → 200，但审计页无新增

## 6. 后续 step（候选）

- **Step 025f（结构化日志 contextvar）**：把 request_id 注入 logging Formatter，所有 log 行自动带前缀
- **Step 026a（audit CSV / 时间范围过滤）**：补 admin 审计 UI 运维能力（导出 + range filter）
- **Step 027（session expiration 审计）**：JWT exp 过期时审计写 `auth.session_expired`（被动事件）
- **Step 025b（mypy 复活）**：长期债务

## 7. 关联

- ADR-009（Closure Router + Container DI）— 本步沿用「路由薄、容器装配」
- ADR-013（审计副作用语义）— 「未登录 logout 不写审计」是 ADR-013 D5 决策"业务连续性 > 审计完备性"的扩展应用
- Step 021（admin 审计基础设施）— 直接前置
- Step 025c（登录端点接入 AuditLogPort）— 本步是其镜像（登入 vs 登出）
- Step 025d（request_id contextvar 透传）— 本步审计的 `request_id` 字段由其链路自动填充
