# ADR-012: Admin RBAC：白名单 settings + 401/403 二段守门

- 状态: accepted
- 日期: 2026-06-05（追溯 Step 013 / 018 / 019 落地决策）
- 关联：[ADR-007: GitHub OAuth + 匿名](ADR-007-github-oauth-with-anonymous.md)、[ADR-008: owner_id 统一身份键](ADR-008-owner-id-tenancy.md)

## 背景

Step 013 起项目出现"admin only"端点：KB 管理（上传 / 删除）、Step 021 审计日志查询。需要决定：

1. **谁是 admin**？基于 user 字段（数据库 `is_admin` 列）还是配置白名单？
2. **怎么守门**？路由层 Depends 还是中间件？
3. **未登录 vs 非 admin 区分**：都返回 403 还是分 401 / 403？
4. **失败响应契约**：FastAPI 默认 `{"detail": ...}` 还是项目 `ErrorResponse`？

## 决策

### D1：admin 由 `Settings.admin_user_ids: list[str]` 白名单声明

```python
# config.py
class Settings(BaseSettings):
    admin_user_ids: Annotated[list[str], NoDecode] = []

    @field_validator("admin_user_ids", mode="before")
    @classmethod
    def _split_admin_ids(cls, v):
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v
```

- `.env` 既可写 `ADMIN_USER_IDS=github:Melodymll01,github:alice`（CSV）
- 也可写 `ADMIN_USER_IDS=["github:Melodymll01","github:alice"]`（JSON）

数据库**不**加 `is_admin` 列。理由：
- admin 列表变更频率极低，重启应用即可生效
- 避免"自助提权"路径（数据库写入 = 提权）
- pydantic-settings 校验在启动期触发，配置错（拼写错 user_id）立刻发现

### D2：路由层 Depends，闭包工厂注入 container

```python
# api/v2/deps.py
def make_require_admin(container: AppContainer) -> Callable[[Request], str]:
    def require_admin(request: Request) -> str:
        token = request.cookies.get(container.settings.session_cookie_name)
        if not token:
            raise HTTPException(401, {"error_code": "AUTH_REQUIRED", ...})
        payload = container.auth.verify_jwt(token)
        if not payload:
            raise HTTPException(401, {"error_code": "AUTH_REQUIRED", ...})
        user_id = payload["sub"]
        if user_id not in container.settings.admin_user_ids:
            raise HTTPException(403, {"error_code": "ADMIN_REQUIRED", ...})
        return user_id
    return require_admin
```

不用 FastAPI 中间件因为：
- 中间件无法在端点签名层显式声明权限要求
- Depends 显式 = 阅读路由就能看出权限层级
- 单元测试可独立测 `require_admin(request) → raise/return`

### D3：401 vs 403 严格分

| 场景 | 状态码 | error_code |
|---|---|---|
| 无 cookie / cookie 失效 | **401** | `AUTH_REQUIRED` |
| 已登录但 user_id 不在白名单 | **403** | `ADMIN_REQUIRED` |

理由：401 提示客户端"去登录"，403 提示"换账号"。混用会让前端不知道该弹登录框还是弹"无权限"。

### D4：响应走项目 `ErrorResponse` 而非 FastAPI 默认

通过 `install_exception_handlers(app, path_prefix="/api/v2")`（见 ADR-010）：

```json
// v2 端点
{"error_code": "ADMIN_REQUIRED", "message": "admin only", "details": null}

// v1 端点（不受影响）
{"detail": "..."}
```

## 后果

**正面**：
- **零数据库改动**：admin 变更只需改 `.env` + 重启
- **启动期可发现配置错**：pydantic-settings 校验 `admin_user_ids` 格式
- **测试可控**：`tests/api/conftest.py` 用 `admin_user_ids` fixture override，每个测试类可指定不同 admin 集
- **401/403 区分清晰**：前端可针对性引导（登录跳转 vs 友好提示）
- **API 契约统一**：v2 全部走 `ErrorResponse{error_code, message}`，前端只写一套错误处理

**负面**：
- admin 调整需重启服务（可接受：低频运维操作）
- 多人协作时 `.env` 不入库，admin 列表靠口头同步（已在 README 注明）
- 未来若做"自助申请 admin"流程，要把白名单挪进数据库（届时立 ADR-XXX）

## 权限矩阵（Step 019 落地）

| 端点类 | 未登录 | 登录非 admin | admin |
|---|---|---|---|
| 老 v1 全部 | 200（无认证） | 200 | 200 |
| v2 `/auth/*` | （含登录端点本身） | — | — |
| v2 `/tasks/*` | 401 | 200（owner 隔离） | 200 |
| v2 KB 读 | 401 | 200 | 200 |
| v2 KB 写（POST/DELETE） | 401 | **403** | 200 |
| v2 `/audit/*` | 401 | **403** | 200 |
| v2 `/copilot/*` | 401 | 200 | 200 |
| v2 `/health/*` | 200（探活） | 200 | 200 |

## 演化记录

- **Step 013**：`admin_user_ids` settings 首次落地 + `make_require_admin` + `UserOut.is_admin` 输出层
- **Step 018**：`config.py` 用 `Annotated[list[str], NoDecode]` + `field_validator(mode="before")` 修复 pydantic-settings v2 默认 JSON-only 的 .env 解析坑（同时兼容 CSV / JSON 两种格式）
- **Step 019**：KB 端点权限拆分（读 `require_owner` / 写 `require_admin`），前端双层 gate
- **Step 021**：审计端点完全 admin-only

## 备选方案

| 方案 | 否决理由 |
|---|---|
| 数据库 `users.is_admin` 列 | 提权路径多一道，且没有自助申请场景，过度设计 |
| 中间件统一拦截 | 路由签名看不出权限层级；测试不便 |
| 401 + 403 合并为单一 403 | 前端无法区分"未登录"和"无权限" |
| Casbin / 完整 RBAC | 当前只有 2 个角色（user / admin），不需要规则引擎 |

## 关联

- [ADR-007: GitHub OAuth + 匿名](ADR-007-github-oauth-with-anonymous.md)
- [ADR-008: owner_id 统一身份键](ADR-008-owner-id-tenancy.md)
- [ADR-009: Closure Router + Container DI](ADR-009-closure-router-container-di.md)
- [ADR-013: 审计端口副作用语义](ADR-013-audit-side-effect-semantics.md)
- 实现：`api/v2/deps.py`、`config.py`、`api/v2/documents.py`、`api/v2/audit.py`
- 过程：[Step 013](../process/step_013_admin_modes_risk_profile_port.md)、[Step 018](../process/step_018_login_bugfix_port_admin.md)、[Step 019](../process/step_019_kb_permission_split.md)、[Step 021](../process/step_021_admin_audit_log.md)
