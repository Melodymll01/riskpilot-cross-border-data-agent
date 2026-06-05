# Step 007 — PR-4 Auth 层（GitHub OAuth + JWT + 匿名）

## 1. 本步骤目标

承接 Step 006 完成的 Infra 层，落地 spec §4.2 / ADR-008 中的认证子系统：

- **为什么存在**：上层 use-case（PR-5 onwards）需要一个稳定的 `AuthPort`，把"OAuth 授权码流程"、"JWT 颁发与校验"、"匿名 owner 发放"统一在一个进程内服务后面。前端拿到 JWT 后，所有受保护接口都通过解码 token 拿 `user_id` 做归属。
- **服务于哪层**：infra 层。本身不动 api 路由，只为后续 PR-5 (`AuthService` 注入到 use-case) / PR-6 (FastAPI dep) 提供具体实现。
- **为后续提供什么**：
  - `AuthService` —— 唯一对外 facade，实现 `AuthPort`
  - `JwtIssuer` —— HS256 短 TTL token 签发与校验，独立单测
  - `GitHubOAuthProvider` —— Authorization Code Flow，依赖 `requests`，可用 `responses` mock
  - `AnonymousProvider` —— 发放 `anon:{uuid4().hex[:16]}`
  - `FakeAuth` —— 上层测试用的 `AuthPort` 内存替身
  - `FakeOAuthProvider` —— 单测 `AuthService` 时替换网络层

## 2. 修改文件

| 路径 | 说明 |
|---|---|
| `infra/auth/__init__.py` | 公共出口：`AuthService` / `JwtIssuer` / `GitHubOAuthProvider` / `AnonymousProvider` |
| `infra/auth/jwt_issuer.py` | HS256 + 注入式 clock；`verify` 自管 `exp`（绕开 PyJWT 实时时钟） |
| `infra/auth/anonymous.py` | uuid4().hex[:16] → User，`provider="anonymous"` |
| `infra/auth/github_oauth.py` | `begin()`+`exchange(code,state)`，`requests.Session` 可注入 |
| `infra/auth/auth_service.py` | 组合三件套；`_consume_state(provider,state)` 防 CSRF + replay |
| `tests/fakes/fake_auth.py` | `FakeOAuthProvider`（实现 `_OAuthProviderLike`）+ `FakeAuth`（实现 `AuthPort`） |
| `tests/fakes/__init__.py` | 导出 `FakeOAuthProvider`、`FakeAuth` |
| `tests/infra/test_jwt_issuer.py` | 8 case：构造校验 / round-trip / payload 形状 / 过期 / 篡改 / 错密钥 / 空 token / 空 user_id |
| `tests/infra/test_anonymous.py` | 6 case：命名空间 / provider 字段 / email/avatar=None / 时间戳 / 唯一性 / clock 注入 |
| `tests/infra/test_github_oauth.py` | 9 case：URL 拼装 / state 唯一 / 成功 / name 回退 / 空 code / token 5xx / token error 字段 / user 4xx / 缺 login |
| `tests/infra/test_auth_service.py` | 13 case：契约 / begin / complete happy / unknown provider / state replay / unknown state / empty state / provider mismatch / state expired / created_at 保留 / JWT round-trip / JWT 垃圾输入 / 匿名持久化 |
| `tests/infra/test_fake_auth.py` | 9 case：FakeAuth 契约 + 行为 + FakeOAuthProvider 行为 |

## 3. 设计决策

### 3.1 AuthService 用鸭子接口而非具体类型

```python
class _OAuthProviderLike(Protocol):
    def begin(self) -> tuple[str, str]: ...
    def exchange(self, code: str, state: str) -> User: ...
```

`AuthService.providers` 是 `dict[str, _OAuthProviderLike]`。这样 `AuthService` 单测只需 `FakeOAuthProvider`，不用真启 HTTP；扩展 GoogleOAuth 也只是再加一个键，无需改 AuthService。

### 3.2 state 在 AuthService 集中管，不下沉到 Provider

OAuth state 是 CSRF 防御 + replay 防御 + provider 路由三个责任的聚合点。Provider 只负责生成与回传 state，校验全部在 AuthService.`_consume_state(provider, state)`：

- pop 出 record（一次性消费 → 防 replay）
- 校对 provider 名（防"用 google 颁发的 state 走 github 回调"）
- 校对 issued_at + ttl（默认 10 分钟，防过期 state 被滥用）

### 3.3 JwtIssuer.verify 用注入 clock 自管 exp

PyJWT 的 `decode` 总是用 `time.time()` 校 exp，注入 clock 没用。为了让"过期 token 测试"和"假时钟集成测试"都能工作，verify 内部 `options={"verify_exp": False}` + 自己用 `self._clock()` 比较 exp。这点在 jwt_issuer.py 的 docstring 里写明。

### 3.4 GitHubOAuthProvider 默认带 `requests.Session()`，但可注入

测试里直接 `@responses.activate` 拦真实 URL；生产里默认 `requests.Session()` 自动连接池。为单元 + 集成两套场景同时友好。

### 3.5 owner_id 命名空间严格遵守 ADR-008

- 匿名：`anon:{uuid4().hex[:16]}`
- GitHub：`github:{login}`
- （未来）Google：`google:{sub}`
- （未来）Email：`email:{sha256(addr).hexdigest()}`

`AnonymousProvider` 与 `GitHubOAuthProvider._to_domain_user` 都按这个前缀写死，不接受外部覆盖，避免上层伪造 owner_id 串号。

### 3.6 已存在用户的 `created_at` 保留

`AuthService.complete_oauth` 在 upsert 前 `user_repo.get(user.user_id)`：若已存在，用 `model_copy(update={"created_at": existing.created_at})` 把新 user 的 created_at 替换为旧的，再 upsert。这样：

- 第一次登录 → created_at 真实写入
- 第 N 次登录 → created_at 不变，其它字段（display_name / avatar / email）跟随上游更新

## 4. 核心契约 / 接口

```python
class AuthService:
    def begin_oauth(provider: str) -> tuple[str, str]
    def complete_oauth(provider: str, code: str, state: str) -> User
    def issue_jwt(user_id: str) -> str
    def verify_jwt(token: str) -> str | None
    def create_anonymous() -> User
```

满足 `domain.ports.AuthPort`（在 `tests/infra/test_auth_service.py::TestProtocolConformance` 里 `isinstance` 校验）。`FakeAuth` 同样满足。

错误模型：

- 任何 OAuth 流程错误（state 无效 / code 失败 / 网络错 / payload 缺字段 / unknown provider）→ `OAuthFlowError`
- `issue_jwt("")` → `InvalidToken`
- `verify_jwt(...)` 失败时 → 返回 `None`，**不抛异常**（按 spec §4.2）

## 5. 与外部服务的关系

| 服务 | 实现 | 测试隔离 |
|---|---|---|
| GitHub OAuth API | `requests.Session` POST/GET | `@responses.activate` 拦截 |
| PyJWT HS256 | jwt.encode/decode | 注入 clock，自校 exp |
| user_repo | `_UserRepoLike` Protocol | `InMemoryUserRepo`（已在 Step 006 落地） |

GitHub OAuth client_id / client_secret / redirect_uri 在构造时透传，不读环境变量；env → config 的桥在后续 PR 接入。

## 6. 当前实现范围

**已实现**：
- 完整的 `begin → state 缓存 → exchange → upsert` 主循环
- state 一次性消费 + provider 匹配 + TTL
- JWT 注入 clock + 自校 exp + 短密钥 reject + 篡改检测
- GitHub OAuth：authorize URL 拼装 / token endpoint POST / user endpoint GET / payload 校验
- 匿名 owner 发放 + 时间戳一致性
- FakeOAuthProvider（满足 `_OAuthProviderLike`）+ FakeAuth（满足 AuthPort）
- 全部 45 个新增测试通过，与原 175 共 224 全绿

**按设计未实现**：
- Google OAuth / Email OTP（spec 多 provider 路线，留作后续 PR）
- JWT 续签 / refresh token（spec 短 TTL + 前端续签策略，本步骤不引入）
- state 的 Redis 后端（10 分钟内存级足够，多副本部署再迁）
- merge_owner（匿名 → 登录的资源迁移）—— 按 spec 应在 use-case 层显式调用 `UserRepoPort.merge_owner`，不进 AuthPort 接口

## 7. 暂未实现 / TODO

- [ ] PR-5 把 AuthService DI 进 use-case
- [ ] PR-6 FastAPI `Depends(get_current_user)` 解 token → owner_id
- [ ] env / config 读 client_id / secret / jwt_secret
- [ ] 真实 GitHub 联调（在 dev 环境跑一次 `begin → 浏览器 → callback`）

## 8. 测试与验证

```powershell
# 单元 + 集成
pytest -q --no-cov
# 224 passed, 16 warnings in 15.39s

# 类型
mypy infra/auth
# Success: no issues found in 5 source files

# Lint
ruff check infra/auth tests/infra tests/fakes/fake_auth.py
# All checks passed!
```

新增测试覆盖：

- `tests/infra/test_jwt_issuer.py`：8 case
- `tests/infra/test_anonymous.py`：6 case
- `tests/infra/test_github_oauth.py`：9 case（`responses.activate` 全程隔离）
- `tests/infra/test_auth_service.py`：13 case
- `tests/infra/test_fake_auth.py`：9 case

合计 **+45 case**，仓库累计 **224 passed**（Step 006 是 175）。
