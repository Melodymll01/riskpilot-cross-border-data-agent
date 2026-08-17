"""
基于 RAG 的数据出境知识库问答系统 - 主入口

启动方式:
    uvicorn main:app --host 127.0.0.1 --port 8765 --reload

端口 8765 与 config.settings.github_redirect_uri 默认值保持一致，
OAuth 回调才能落到本地服务。
"""

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api.v2 import build_v2_router
from api.v2.errors import install_exception_handlers
from api.v2.ratelimit import build_limiter
from api.v3 import build_v3_router
from app.container import AppContainer
from app.logging_setup import configure_logging
from app.request_context import (
    reset_request_id,
    reset_user_id,
    set_request_id,
    set_user_id,
)
from config import settings

# ===================== 日志配置 =====================

# Step 025f：用 ``configure_logging`` 替代裸 basicConfig，让所有 LogRecord
# 自动带 ``[request_id]`` 字段（contextvar 缺省时显示 ``[-]``）
configure_logging(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    log_file="logs/app.log",
)

logger = logging.getLogger(__name__)


# ===================== Lifespan（替代已弃用的 on_event） =====================


def _warn_oauth_redirect_port_mismatch(server_port: int | None) -> None:
    """启动时检查：若 server 监听端口与 github_redirect_uri 里的端口不一致，发出警告。

    端口不一致会让 GitHub OAuth 回调跳到一个空端口，前端看起来像「登录不进去」。
    服务端无法可靠探测 uvicorn 实际监听端口（可能由命令行 --port 指定），
    所以只在通过 ``python main.py`` 直起场景或 ``UVICORN_PORT`` 已知时校验。
    """
    from urllib.parse import urlparse

    if server_port is None:
        return
    try:
        parsed = urlparse(settings.github_redirect_uri)
        cb_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except Exception:  # pragma: no cover - 配置极端损坏
        return
    if cb_port != server_port:
        logger.warning(
            "OAuth redirect URI 端口 (%s) 与服务监听端口 (%s) 不一致，"
            "GitHub 登录回调会跳到死端口；请同步修改 .env 中 GITHUB_REDIRECT_URI 或 server 端口。",
            cb_port,
            server_port,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动时初始化服务，关闭时释放资源。"""
    settings.validate_runtime_configuration()
    logger.info("数据出境知识库问答系统正在启动...")
    container_ref = getattr(app.state, "container", None)
    if container_ref is not None:
        workflow_runtime = getattr(container_ref, "workflow_runtime", None)
        initialize_workflow = getattr(workflow_runtime, "initialize", None)
        if initialize_workflow is not None:
            await asyncio.to_thread(initialize_workflow)
    # Deep Research 图预热，失败不影响服务启动。
    if container_ref is not None and settings.warmup_research_on_startup:
        app.state.warmup_task = asyncio.create_task(_warmup_research(container_ref))
    yield
    logger.info("数据出境知识库问答系统正在关闭...")
    # 优雅关闭 L2 记忆调度线程池（best-effort，不等待挂起作业）
    if container_ref is not None:
        scheduler = getattr(container_ref, "memory_scheduler", None)
        if scheduler is not None:
            try:
                scheduler.shutdown(wait=False)
            except Exception:  # noqa: BLE001 — 关闭期异常仅记录
                logger.warning("记忆调度器关闭异常（已吞掉）", exc_info=True)
        workflow_runtime = getattr(container_ref, "workflow_runtime", None)
        close_workflow = getattr(workflow_runtime, "close", None)
        if close_workflow is not None:
            try:
                close_workflow()
            except Exception:  # noqa: BLE001 — 关闭期异常仅记录
                logger.warning("LangGraph checkpoint 连接关闭异常（已吞掉）", exc_info=True)
        storage_database = getattr(container_ref, "storage_database", None)
        if storage_database is not None:
            try:
                storage_database.dispose()
            except Exception:  # noqa: BLE001 — 关闭期异常仅记录
                logger.warning("数据库 Engine 关闭异常（已吞掉）", exc_info=True)


async def _warmup_research(container_ref) -> None:
    """后台预热深度研究引擎（best-effort）。失败只记日志，不影响服务可用性。"""
    warmup = getattr(getattr(container_ref, "research", None), "warmup", None)
    if warmup is None:
        return
    started = time.time()
    logger.info("深度研究引擎预热开始（后台加载 CrossEncoder 等模型）...")
    try:
        await asyncio.to_thread(warmup)
        logger.info("深度研究引擎预热完成，耗时 %.1fs", time.time() - started)
    except Exception:  # noqa: BLE001 — 预热失败首个 research 仍可懒加载
        logger.warning("深度研究引擎预热失败（已吞掉，将在首次请求时懒加载）", exc_info=True)


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
    """为每个请求注入 request_id + user_id，记录请求耗时。

    Step 025d：把 request_id 写入 ``app.request_context.request_id_var``
    contextvar，让 use case（如 ``_record_audit``）不需要 API 层逐层透传
    就能拿到当前请求 id，落到 ``AuditEntry.request_id`` 字段。

    Step 025g：在解析 request_id 之后再尝试从 cookie 解出 user_id 一并写到
    ``user_id_var``。这样 access log 行（行末 logger.info）和后续路由内的
    任何 logger 调用都会自动带 ``[uid:xxx]`` 段。注意：
    - identify 失败 / 无 cookie / 路径非 ``/api/`` 都会让 user_id 保持 ``None``
      （filter 出 ``[uid:-]``），不抛
    - 在 lifespan 之前的请求（理论上不存在，FastAPI 启动后才接请求）拿不到
      ``app.state.container``，``getattr`` 兜底为 ``None``
    """
    request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
    request.state.request_id = request_id
    rid_token = set_request_id(request_id)

    # Step 025g：尝试从 cookie 解析 user_id；只对 /api/ 路径解（静态文件、
    # /docs 等无认证语义的请求不必每次解 JWT）
    user_id: str | None = None
    if request.url.path.startswith("/api/"):
        container_ref = getattr(request.app.state, "container", None)
        if container_ref is not None:
            cookie_name = container_ref.settings.cookie_name
            token_str = request.cookies.get(cookie_name)
            if token_str:
                try:
                    user_id = container_ref.auth_login.identify(token_str)
                except Exception:
                    # identify 内部已吞 jwt 异常返回 None；这里多一层保险，
                    # 避免任何意外异常把整个请求带崩
                    user_id = None
    request.state.user_id = user_id
    uid_token = set_user_id(user_id)

    start = time.perf_counter()
    try:
        response = await call_next(request)

        # Step 025g bug fix：access log + header 必须在 reset 之前调用，
        # 否则 RequestIdLogFilter 取 contextvar 时拿到的是 None（reset 后
        # 回到 default），log 行就只会出 [-] [uid:-] 而非真实值
        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = request_id
        if request.url.path.startswith("/api/"):
            # Step 025f / 025g：request_id 与 user_id 由 RequestIdLogFilter 自动
            # 拼到 [%(request_id)s] [uid:%(user_id)s] 段，不再手工拼接
            logger.info(
                "%s %s → %s (%.0fms)",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
            )
        return response
    finally:
        reset_user_id(uid_token)
        reset_request_id(rid_token)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常兜底：防止未捕获异常泄露堆栈信息到客户端。"""
    request_id = getattr(request.state, "request_id", "unknown")
    # Step 025f：request_id 仍作为 JSON body 返回以供客户端反馈；log 行由
    # formatter 自动拼上，不再手工 f"[{request_id}]"
    logger.exception("未捕获异常: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请稍后重试", "request_id": request_id},
    )


# ── Step 011：装配 api/v2（六边形架构层）——v1 已于 Step 029 退役────────────────
# 容器在模块级构造（构造本身只装组件、不连外部服务），存到 app.state 方便测试取用。
# 不要放进 lifespan：include_router 在 app 创建时就要拿到 router。
container = AppContainer(settings)
app.state.container = container

# Step（v2 限流）：构造限流器并注入 v2 router；limiter 为 None（rate_limit_enabled=False）
# 时所有限流依赖退化为无操作。限流以 FastAPI 依赖实现，超额时直接抛 HTTPException(429)，
# 由 FastAPI 默认处理器返回，无需额外异常处理器。测试 app 不挂 limiter 故不受影响。
limiter = build_limiter(settings)

app.include_router(build_v2_router(container, limiter=limiter), prefix="/api/v2")
app.include_router(build_v3_router(container), prefix="/api/v3")
install_exception_handlers(app)
logger.info(
    "api/v2 + api/v3 routes mounted (tools=%s, rate_limit=%s)",
    container.copilot_agent.tool_names,
    "on" if limiter is not None else "off",
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

    # 端口默认跟 settings.github_redirect_uri 保持一致，避免 OAuth 回调跟服务不同端口造成「登录不进去」。
    # 如需改端口，请同步修改 .env 中 GITHUB_REDIRECT_URI 及 GitHub OAuth App 里的 callback。
    SERVER_PORT = 8765
    _warn_oauth_redirect_port_mismatch(SERVER_PORT)
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=SERVER_PORT,
        reload=False,
    )
