# ADR-009: Closure Router + Container DI（路由与容器的绑定方式）

- 状态: accepted
- 日期: 2026-06-05（追溯 Step 008 / 010 落地决策）
- 关联：[ADR-006: 4 层架构](ADR-006-4-layer-architecture.md)（被本 ADR 增强）

## 背景

Step 008 引入 `AppContainer`（13 Port + 6 use case 中央配电盘）后，FastAPI 路由如何拿到 container 是开放问题。常见做法：

1. **全局单例**：`from app.container import container`，路由模块 import 时绑死 → 测试时无法替换 fake，启动时 import 顺序敏感
2. **FastAPI Depends 工厂**：每个端点用 `container_dep = Depends(get_container)` → 全部端点都要写一遍，且 `get_container` 自己仍需要全局单例
3. **请求 state 注入**：`request.state.container` → 类型不友好（`Any`），中间件耦合

## 决策

**采用 closure router 模式**：每个子路由模块导出一个工厂函数 `build_*_routes(container) -> APIRouter`，container 被闭包绑定到端点处理函数。

```python
# api/v2/auth.py
def build_auth_routes(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])
    require_owner = make_require_owner(container)

    @router.post("/anonymous")
    def login_anonymous(...) -> LoginResponse:
        user = container.auth_login.login_anonymous()  # 闭包捕获
        ...
    return router

# api/v2/router.py
def build_v2_router(container: AppContainer) -> APIRouter:
    root = APIRouter()
    root.include_router(build_auth_routes(container))
    root.include_router(build_task_routes(container))
    root.include_router(build_documents_routes(container))
    root.include_router(build_audit_routes(container))
    ...
    return root
```

测试 fixture 一次性注入全 fake container，前端正常运行时注入真实 container：

```python
# tests/api/conftest.py
@pytest.fixture
def container(test_settings, ...) -> AppContainer:
    return AppContainer(test_settings, user_repo=InMemoryUserRepo(), ...)

@pytest.fixture
def app(container) -> FastAPI:
    fastapi_app = FastAPI()
    fastapi_app.include_router(build_v2_router(container), prefix="/api/v2")
    ...
```

## 后果

**正面**：
- **零全局状态**：container 唯一来源是 `main.py`（生产）或 `conftest.py`（测试）
- **测试用例不需要 monkeypatch**：直接传 fake container 进去；TestClient + 200 个 API 用例无任何 `mock` 调用
- **类型友好**：路由函数闭包内 container 类型是 `AppContainer`，IDE 跳转 / 重构均工作
- **多 container 共存可能**：未来如需 staging / canary 双 container 并存只需多构造一次，端口不冲突

**负面**：
- 路由模块不能"裸 import 即可挂端点"；必须显式调用 `build_*_routes(container)`
- 不熟悉 closure 的人初次阅读需要理解"为什么端点函数能拿到 container"
- `make_require_owner(container) / make_require_admin(container)` 需要类似的 closure 工厂模式（已落地）

## 备选方案

| 方案 | 否决理由 |
|---|---|
| 全局单例 `from app.container import container` | 测试隔离差，import 顺序敏感，多 container 不可能 |
| 纯 `Depends(get_container)` | 仍需全局单例；每个端点签名多一行噪音 |
| 中间件注入 `request.state.container` | 类型 `Any`，要么 cast 要么 `# type: ignore` |
| FastAPI lifespan + state | 与 closure 等价但样板更多，且路由函数仍要 `request.app.state.container` |

## 实证

- 全部 v2 路由（auth / tasks / documents / audit / copilot / health）统一走该模式（Step 010-021）
- 200+ API 测试零 `monkeypatch.setattr` 调用
- `tests/api/conftest.py` 单 fixture 链 `container → app → client` 复用率 100%

## 关联

- [ADR-006: 4 层架构](ADR-006-4-layer-architecture.md)
- [ADR-010: Strangler Fig v1/v2 共存](ADR-010-strangler-fig-v1-v2.md)
- 实现：`app/container.py`、`api/v2/router.py`、`api/v2/deps.py`
- 过程：[Step 008](../process/step_008_pr5_app_layer.md)、[Step 010](../process/step_010_pr6_api_layer.md)
