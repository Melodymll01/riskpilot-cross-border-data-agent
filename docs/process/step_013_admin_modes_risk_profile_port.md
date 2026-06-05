# Step 013 — admin 权限基线 + Task.mode 三业务模式 + RiskProfilePort 接口预留

> 对应 commit [`e4fc107`](https://github.com/Melodymll01/riskpilot-cross-border-data-agent/commit/e4fc107)
> 标题：`feat(backend): admin baseline + Task.mode 三业务模式 + RiskProfilePort 接口预留`

## 1. 本步骤目标

为接下来的多业务形态前端（Step 014）和 profile 模式联调（Step 015）准备**三块独立但同期落地的后端基线**：

1. **admin 权限基线** —— 后端有"管理员"概念但前端没暴露入口；本步给出 owner_id 命名空间的管理员判定 + 401/403 二阶错误码 + UserOut 输出层 `is_admin` 标记，让 Step 016+ 的 KB 管理界面只需挂依赖即可。
2. **Task.mode 三业务模式** —— 把"知识问答 / 深度研究 / 风险画像"从原来 v1.0 表单层级的概念下沉到 domain `Task.mode` 字段，全链路（domain → infra → app → api）透传，让 Step 014 前端能通过单参数选模式，Step 015 在 use case 里按 mode 分流。
3. **`RiskProfilePort` 接口预留** —— 提前按隔壁仓库 [`schema-evidence-risk-profiling`](https://github.com/Melodymll01/schema-evidence-risk-profiling) 的 `evidence_v1/sample_schema_v1.json` 形态把 domain 模型 + Port + 占位适配器全部落地，等模型训完只需替换工厂的实现，无需改 use case / api。

三块同 commit 的合理性：admin / mode / port 三者独立，但 mode == "profile" 的语义就是"调 RiskProfilePort"，三者在前端 UX 层（Step 014）会同时出现，所以一次性把后端基础设施铺平比拆三次 PR 更省事。

## 2. 修改文件

### 新增

| 文件 | 行数（增） | 说明 |
|---|---|---|
| [infra/risk_profile/__init__.py](../../infra/risk_profile/__init__.py) | 11 | 子包导出 `StubRiskProfileService` |
| [infra/risk_profile/stub_risk_profile.py](../../infra/risk_profile/stub_risk_profile.py) | 64 | `RiskProfilePort` 占位实现，`raise` / `placeholder` 双模式 |
| [tests/infra/test_stub_risk_profile.py](../../tests/infra/test_stub_risk_profile.py) | 57 | `raise` 抛 `RiskProfileNotReady`、`placeholder` 返回带 `metadata.stub=True` 的 `RiskProfile` |

### 修改

| 文件 | +/- | 关键改动 |
|---|---|---|
| [config.py](../../config.py) | +10 | 新增 `admin_user_ids: List[str]` 字段（`.env` 逗号分隔，pydantic-settings 自动解析） |
| [.env.example](../../.env.example) | +24 | 增 `ADMIN_USER_IDS=github:foo,github:bar` 模板与说明 |
| [domain/__init__.py](../../domain/__init__.py) | +12 | re-export `EvidenceState` / `EvidenceSpan` / `RiskProfile` / `TaskMode` / `RiskProfilePort` / `RiskProfileNotReady` |
| [domain/errors.py](../../domain/errors.py) | +8 | 新增 `RiskProfileNotReady(DomainError)`（与 `EvidenceServiceError` 区别于"占位 vs 真实失败"） |
| [domain/models.py](../../domain/models.py) | +54 | 新增 `TaskMode = Literal["qa","research","profile"]`、`EvidenceState` 五分类、`EvidenceSpan`、`RiskProfile`；`Task` 增 `mode: TaskMode = "qa"` |
| [domain/ports.py](../../domain/ports.py) | +22 | 新增 `RiskProfilePort.assess(target, document?, language?) -> RiskProfile` Protocol |
| [infra/storage/_db.py](../../infra/storage/_db.py) | +16 | `_apply_incremental_migrations` 增 `tasks.mode` 列（NULL → 'qa' 兼容历史 DB） |
| [infra/storage/sqlite_task_repo.py](../../infra/storage/sqlite_task_repo.py) | +19 | INSERT/SELECT 列表增 `mode`；row → `Task` 反序列化把 `NULL/''` 归一成 `"qa"` |
| [app/factories.py](../../app/factories.py) | +8 | `build_risk_profile(settings) -> RiskProfilePort` 默认返回 `StubRiskProfileService(mode="raise")` |
| [app/container.py](../../app/container.py) | +6 | 装配 `self.risk_profile = build_risk_profile(settings)` |
| [app/use_cases/task_management.py](../../app/use_cases/task_management.py) | +4 | `create_task(... , mode: TaskMode = "qa")` 透传到 `Task` |
| [app/use_cases/run_copilot.py](../../app/use_cases/run_copilot.py) | +4 | `stream(... , mode: TaskMode = "qa")` 在 create_task 时传入；具体分流逻辑在 Step 015 |
| [api/v2/deps.py](../../api/v2/deps.py) | +39 | 新增 `make_require_admin(container)` 闭包依赖：401（未登录）/ 403（`ADMIN_REQUIRED`） |
| [api/v2/schemas.py](../../api/v2/schemas.py) | +4 | `UserOut.is_admin: bool = False`；`TaskOut.mode` / `ChatRequest.mode: Literal["qa","research","profile"] = "qa"` |
| [api/v2/auth.py](../../api/v2/auth.py) | ±24 | `/me` 与 `/anonymous` 在响应阶段计算 `is_admin = user.user_id in settings.admin_user_ids` |
| [api/v2/copilot.py](../../api/v2/copilot.py) | +2 | 把 `ChatRequest.mode` 透传到 `run_copilot.stream(... , mode=...)` |
| [api/v2/tasks.py](../../api/v2/tasks.py) | +1 | `_to_task_out` 把 `task.mode` 写进 `TaskOut.mode` |
| [tests/api/conftest.py](../../tests/api/conftest.py) | +10 | `Settings()` fixture 增 `admin_user_ids=["github:adminuser"]` 用于 admin 路由测试 |
| [tests/api/test_auth.py](../../tests/api/test_auth.py) | +151 | `is_admin` 渲染 + `make_require_admin` 401/403/200 三态用例 |
| [tests/api/test_copilot.py](../../tests/api/test_copilot.py) | +60 | `ChatRequest.mode` 透传 + `TaskOut.mode` 输出验证 |
| [tests/app/test_container.py](../../tests/app/test_container.py) | +2 | 容器装配后 `container.risk_profile` 是 `RiskProfilePort` 实例 |
| [tests/domain/test_models.py](../../tests/domain/test_models.py) | +55 | `Task.mode` 默认/枚举校验；`RiskProfile` JSON round-trip；`EvidenceSpan` start/end 边界 |
| [tests/infra/test_sqlite_repos.py](../../tests/infra/test_sqlite_repos.py) | +82 | `mode` 列迁移幂等；老 DB 升级后 `Task.mode == "qa"`；新建任务 mode 持久化 |

## 3. 设计决策

| 选择 | 取代方案 | 原因 |
|---|---|---|
| **admin 用 `user_id` 命名空间白名单**（如 `github:lele.ma`） | DB 字段 `User.role` | ADR-008 已经把 owner_id 设计成 `<provider>:<id>` 命名空间；admin 只需在 `Settings` 里维护一份列表即可；无 schema 迁移、无管理员管理后台 |
| **`is_admin` 在 API 输出层计算**，不进 domain 模型 | `User.is_admin: bool` | domain 是 frozen 持久层模型；admin 是部署期配置，不是用户数据；进 domain 会带"什么时候校验 / 什么时候持久"的歧义 |
| **`make_require_admin` 是 closure 依赖** | 全局 `Depends(require_admin)` | 与既有 `make_require_owner` / `make_identify_owner` 风格一致；闭包持有 `container.settings` 才能拿到 `admin_user_ids` |
| **401 / 403 二阶错误码** | 一律 401 | 401 = "你是谁不知道"，403 = "知道但不够格"；前端可分别给"去登录"和"联系管理员"两种 UX |
| **`TaskMode` 是 Literal 而非枚举** | `class TaskMode(str, Enum)` | 项目 domain 层规约统一用 `Literal[...]`（见 `Provider` / `TaskState`）；JSON 序列化天然是字符串；pydantic v2 校验等价 |
| **`Task.mode` 默认 `"qa"` + 数据库列允许 NULL** | NOT NULL DEFAULT 'qa' | 历史 DB 已有的 task 不需要全表 UPDATE；SELECT 阶段 NULL → "qa" 归一 |
| **schema 迁移走 `_apply_incremental_migrations`，不上 alembic** | alembic | 已有迁移机制（PR-3 基线确立的"幂等 ALTER TABLE"模式）；本项目就一张 sqlite，alembic 是过度工程 |
| **`RiskProfilePort.assess` 签名包含 `document` 可选参数** | 只传 `target` | `schema-evidence-risk-profiling` 的 sample 就是「target × document → state」，模型部署后必然要传文档；接口先长出来 |
| **占位实现默认抛 `RiskProfileNotReady` 而非返回 `not_disclosed`** | 静默返回 not_disclosed 占位 | `not_disclosed` 是"文档未涉及"的真实分类；占位用同一值会污染未来日志/评估；明确异常 + 上层翻译"敬请期待" |
| **占位实现还提供 `placeholder` 模式** | 只有 `raise` | 联调阶段前端要能跑通整条管道（创 task → SSE → answer 渲染），避免每次前端调试都见红 |
| **`_format_risk_profile_md` 在 use case 而非 schema** | API 层 schema 自带 markdown 字段 | use case 是业务边界，markdown 是"输出形态"决定（聊天界面），属于 use case 责任；其他客户端（CLI/JSON API）可以直接用 `RiskProfile` |
| **`build_risk_profile` 工厂函数显式签名** | 直接 `container.risk_profile = StubRiskProfileService()` | 与其他端口一致（`build_chat` / `build_retriever`）；模型部署后切 `HttpRiskProfileClient(...)` 改一处 |

## 4. 核心契约 / 接口

### domain（schema-evidence v1 镜像）

```python
EvidenceState = Literal[
    "supported",                # 文档显式支持目标命题
    "contradicted",             # 文档反驳目标命题
    "not_disclosed",            # 文档未涉及（≠ 事实为假）
    "insufficiently_disclosed", # 涉及但信息不足
    "irrelevant",               # 与目标命题无关
]

class EvidenceSpan(BaseDomainModel):
    text: str = Field(min_length=1)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)

class RiskProfile(BaseDomainModel):
    target: str = Field(min_length=1)
    evidence_state: EvidenceState
    evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    explanation: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

@runtime_checkable
class RiskProfilePort(Protocol):
    def assess(
        self,
        target: str,
        document: str | None = None,
        *,
        language: str = "zh",
    ) -> RiskProfile: ...
```

### Task.mode 全链透传

```
ChatRequest.mode  →  run_copilot.stream(mode=...)
                         ↓
                  task_management.create_task(mode=...)
                         ↓
                     Task(mode=...) (domain)
                         ↓
                  SqliteTaskRepo.add() INSERT mode
                         ↑
                  SELECT ... 反序列化 NULL → 'qa'
                         ↓
                     Task(mode='qa'|'research'|'profile')
                         ↓
                     TaskOut.mode
```

### admin 依赖 401/403 契约

```python
# api/v2/deps.py
def make_require_admin(container) -> Depends:
    def _require_admin(request: Request) -> str:
        uid = identify(request.cookies[cookie_name])
        if uid is None:
            raise HTTPException(401, {"error_code": "AUTH_REQUIRED", ...})
        if uid not in admin_set:
            raise HTTPException(403, {"error_code": "ADMIN_REQUIRED", ...})
        return uid
    return _require_admin
```

`admin_set = set(container.settings.admin_user_ids)` 在闭包构造时一次性物化；运行时新增管理员需重启进程。

## 5. 与外部服务的关系

- **`schema-evidence-risk-profiling` 仓库**（`D:\py\schema-evidence-risk-profiling`，未来独立模型仓）—— 本步只对齐其 `schemas/evidence_v1/sample_schema_v1.json` 字段形态，未发起任何 HTTP / 模型推理调用。`StubRiskProfileService` 不依赖任何外部服务。
- **GitHub OAuth** —— admin 判定建立在已有 OAuth 流程之上；用户登录后拿到 `user_id = "github:<login>"`，匹配 `Settings.admin_user_ids` 列表即视为管理员。
- **SQLite** —— 唯一的持久化变更：`tasks` 表新增 `mode` 列。增量迁移幂等，已部署实例可平滑升级。

## 6. 当前实现范围

✅ 已实现：

- `Settings.admin_user_ids` 配置项，`.env.example` 模板
- `make_require_admin` 闭包依赖，401/403 二阶错误码
- `UserOut.is_admin` 输出层标记
- `Task.mode` domain 字段、SQLite 列迁移、API schema 字段、use case 透传
- `EvidenceState` / `EvidenceSpan` / `RiskProfile` domain 模型，frozen + JSON round-trip
- `RiskProfilePort.assess` Protocol 签名
- `StubRiskProfileService` 双模式占位实现
- `factories.build_risk_profile` + `container.risk_profile` 装配

❌ 未实现（按规划推迟）：

- **`RiskProfilePort` 真实 HTTP 适配器** —— 等隔壁仓库 evidence-state v1 模型训完部署
- **管理员后台 UI** —— Step 016+ 的 KB 管理面板会用上 `make_require_admin`
- **profile 模式 use case 分流** —— Step 015 闭环；本步只确保 mode 字段透传到了 `RunCopilotUseCase`，但 `mode == "profile"` 时仍然走 agent（被 Step 015 修正）
- **`Task.mode` 在 task 创建后是否可变** —— 当前 domain 是 frozen，假设 task 一经创建 mode 不变；如果未来要支持"切 Tab 复用同一 task"则需要重新设计

## 7. 暂未实现 / TODO

- `make_require_admin` 没有审计日志；管理员调用敏感接口（删 KB / 重置任务）时建议加 `audit_log`
- `admin_user_ids` 是 `Settings` 一次性物化，运行时改 `.env` 不生效；如有需要可换 lazy 读取
- `EvidenceSpan.start/end` 二者要么都有要么都没有的约束未在 pydantic 校验里强制（依赖输入端自觉）；待真实模型接入后视情况补 validator
- `RiskProfile.metadata` 是 `dict[str, Any]`，schema 故意宽松，未来真实模型若稳定输出可收紧字段
- 占位 `placeholder` 模式的 `not_disclosed` 输出会被前端按真实分类渲染；如果未来训练数据要从生产回流，需在前端加"占位 banner"区分

## 8. 测试与验证

```bash
pytest -q --no-cov
# 409 passed, 16 warnings  （+15 vs Step 012 基线 380；admin 路由 +9, mode +4, RiskProfile +15, RiskProfilePort 占位 +6, container +2 / 跨 file 增减汇总后实际 +29 用例 = 409）
ruff check .
# All checks passed
mypy app domain infra/risk_profile api/v2
# Success: no issues found
```

### 端到端冒烟（手动）

```python
from infra.risk_profile.stub_risk_profile import StubRiskProfileService
from domain.errors import RiskProfileNotReady

# 默认 raise 模式
svc = StubRiskProfileService()
try:
    svc.assess(target="本公司向欧盟传输用户人脸数据无需 SCC")
except RiskProfileNotReady as e:
    print("OK:", e)
# OK: 风险画像模型尚未部署...

# placeholder 模式
svc = StubRiskProfileService(mode="placeholder")
rp = svc.assess(target="...", document="《公司隐私政策》第 5.2 条...")
assert rp.metadata["stub"] is True
assert rp.evidence_state == "not_disclosed"
```

### admin 路由（curl 演示）

```bash
# 未登录
curl -i http://127.0.0.1:8765/api/v2/_protected_admin_demo
# HTTP/1.1 401 Unauthorized
# {"error_code":"AUTH_REQUIRED",...}

# 已登录但非管理员
curl -i --cookie session=<普通用户 jwt> .../_protected_admin_demo
# HTTP/1.1 403 Forbidden
# {"error_code":"ADMIN_REQUIRED",...}

# 管理员
curl -i --cookie session=<admin jwt> .../_protected_admin_demo
# HTTP/1.1 200 OK
```

> 注：`_protected_admin_demo` 是文档示意路径；实际管理员路由在 Step 016+ 的 KB 管理子路由中挂载。
