# ─── Stage 1: builder ────────────────────────────────────────────────────────
# 单独的构建阶段：安装依赖、编译 wheel。最终镜像不带构建工具，体积更小。
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120

# 编译某些 Python 轮子需要的系统库
RUN apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=30 update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU 版 PyTorch 单独成层：普通 requirements 变化时不重复下载 155MB wheel。
RUN pip install --upgrade pip \
    && pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ─── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # HuggingFace 模型缓存目录（数据卷挂载，避免每次启动重新下载）
    HF_HOME=/app/.cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/app/.cache/huggingface \
    # 国内 HuggingFace 镜像（如在境外部署可改回默认 https://huggingface.co）
    HF_ENDPOINT=https://hf-mirror.com

# OpenCV/RapidOCR 的最小动态库；不安装编译工具、桌面环境或 X Server。
RUN apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=30 update \
    && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libx11-xcb1 \
    && rm -rf /var/lib/apt/lists/*

# 非 root 用户运行（安全最佳实践）
RUN groupadd --system app && useradd --system --gid app --home /app app
WORKDIR /app

# 从 builder 拷贝已安装好的 Python 包
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 拷贝项目源码（.dockerignore 会过滤 venv / data / .env 等）
COPY --chown=app:app . .

# 确保运行时目录存在并归属 app 用户
RUN mkdir -p data/chroma_db data/uploads data/embed_cache logs .cache/huggingface \
    && chown -R app:app /app

USER app

EXPOSE 8001 9101
STOPSIGNAL SIGTERM

# 默认镜像命令是 API，因此镜像级 healthcheck 使用 readiness。
# Worker service 在 Compose 中覆盖为 Celery inspect ping。
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/api/v2/health/ready', timeout=3)"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
