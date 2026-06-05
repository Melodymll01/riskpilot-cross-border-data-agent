"""``api/v2/``：基于 ``AppContainer`` 的新一代路由层。

设计目的：
- 完全独立于老 ``api/routes.py``（Strangler Fig：新老并存，逐步切换）
- 通过 ``build_v2_router(container)`` 工厂构造 FastAPI APIRouter；不引入全局变量
- 路由只做"HTTP → use case 调用 → 序列化"，业务编排全在 app 层
- SSE 流式由 ``api/v2/sse.py`` 把 ``AgentEvent`` 翻译成标准 SSE 帧

用法（生产）：
    from api.v2 import build_v2_router
    from app import AppContainer
    from config import settings

    container = AppContainer(settings)
    app.include_router(build_v2_router(container), prefix="/api/v2")

用法（测试）：
    container = AppContainer(settings, auth=FakeAuth(), task_repo=InMemoryTaskRepo(), ...)
    app = FastAPI()
    app.include_router(build_v2_router(container), prefix="/api/v2")
    client = TestClient(app)
"""

from api.v2.router import build_v2_router

__all__ = ["build_v2_router"]
