# ─── Stage 1: builder ────────────────────────────────────────────────────────
# 单独的构建阶段：安装依赖、编译 wheel。最终镜像不带构建工具，体积更小。
FROM python:3.11-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 编译某些 Python 轮子需要的系统库
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装 CPU 版 PyTorch（sentence-transformers 依赖；CUDA 版镜像会超大）
# 然后再装项目其余依赖
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

# ─── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # HuggingFace 模型缓存目录（数据卷挂载，避免每次启动重新下载）
    HF_HOME=/app/.cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/app/.cache/huggingface \
    # 国内 HuggingFace 镜像（如在境外部署可改回默认 https://huggingface.co）
    HF_ENDPOINT=https://hf-mirror.com

# 运行时所需的最小系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

# 非 root 用户运行（安全最佳实践）
RUN groupadd --system app && useradd --system --gid app --home /app app
WORKDIR /app

# 从 builder 拷贝已安装好的 Python 包
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 拷贝项目源码（.dockerignore 会过滤 venv / data / .env 等）
COPY --chown=app:app . .

# 确保运行时目录存在并归属 app 用户
RUN mkdir -p data/chroma_db data/uploads data/embed_cache logs .cache/huggingface \
    && chown -R app:app /app

USER app

EXPOSE 8001

# 容器健康检查：访问根路径
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8001/ >/dev/null || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
