"""
基于 RAG 的数据出境知识库问答系统 - 主入口

启动方式:
    uvicorn main:app --host 127.0.0.1 --port 8004 --reload
"""

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from api.routes import limiter, router, start_service_init
from api.v2 import build_v2_router
from api.v2.errors import install_exception_handlers
from app.container import AppContainer
from config import settings

# ===================== 日志配置 =====================

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/app.log", encoding="utf-8"),
    ],
)

logger = logging.getLogger(__name__)


# ===================== Lifespan（替代已弃用的 on_event） =====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动时初始化服务，关闭时释放资源。"""
    logger.info("数据出境知识库问答系统正在启动...")
    start_service_init()
    yield
    logger.info("数据出境知识库问答系统正在关闭...")


# ===================== FastAPI 应用 =====================

app = FastAPI(
    title="数据出境知识库问答系统",
    description="基于 RAG 的数据出境法规、政策、指南知识库问答 API",
    version="1.0.0",
    lifespan=lifespan,
)


# ===================== 中间件 =====================

@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    """为每个请求注入 request_id，记录请求耗时。"""
    request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
    request.state.request_id = request_id
    start = time.perf_counter()

    response = await call_next(request)

    elapsed_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Request-ID"] = request_id
    if request.url.path.startswith("/api/"):
        logger.info(
            f"[{request_id}] {request.method} {request.url.path} "
            f"→ {response.status_code} ({elapsed_ms:.0f}ms)"
        )
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常兜底：防止未捕获异常泄露堆栈信息到客户端。"""
    request_id = getattr(request.state, "request_id", "unknown")
    logger.exception(f"[{request_id}] 未捕获异常: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请稍后重试", "request_id": request_id},
    )


# 注册 API 限流
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

# 注册 API 路由
app.include_router(router)

# ── Step 011：装配 api/v2（新六边形架构层）─────────────────────────────────
# 容器在模块级构造（构造本身只装组件、不连外部服务），存到 app.state 方便测试取用。
# 不要放进 lifespan：include_router 在 app 创建时就要拿到 router。
container = AppContainer(settings)
app.state.container = container
app.include_router(build_v2_router(container), prefix="/api/v2")
install_exception_handlers(app)
logger.info(
    "api/v2 routes mounted (tools=%s)",
    sorted(container.tool_registry.keys()),
)

# CORS 允许前端跨域请求（origins 可在 .env 中通过 CORS_ORIGINS 配置）
# 注意：cookie session 需要 allow_credentials=True；浏览器规定 credentials 模式下
# 禁用 origin 通配符 "*"，生产环境必须把 CORS_ORIGINS 配成显式白名单。
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

# 静态文件服务：前端页面 & 资源（CSS/JS）
FRONTEND_DIR = Path(__file__).parent / "frontend"

# /static/* 提供 CSS、JS 等静态资源，必须在 @app.get("/") 之前挂载
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
async def serve_frontend():
    """访问根路径返回前端页面。"""
    return FileResponse(FRONTEND_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8004,
        reload=False,
    )
