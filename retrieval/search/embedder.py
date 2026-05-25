"""Embedding 模块：调用 OpenAI 兼容接口生成文本向量。

特性:
- 异步批量嵌入（async embed_texts_async），10 万条文档入库不阻塞主线程
- 磁盘持久化 LRU 缓存（diskcache），进程重启后首批用户仍有缓存命中
- 线程安全 + asyncio 安全的内存缓存（threading.Lock）
- 优雅降级：重试耗尽后返回零向量而非抛异常，保证服务可用性


-风险：维度问题我看这里是1024，有的是2048，如果程序启动有问题，注意审查
"""

import asyncio
import hashlib
import logging
import random
import time
import threading
from collections import OrderedDict
from pathlib import Path
from typing import List, Optional

from openai import (
    OpenAI, AsyncOpenAI,
    APITimeoutError, APIConnectionError, RateLimitError,
)

from config import settings

logger = logging.getLogger(__name__)

# 每批最多发送的文本数量，避免单次请求过大导致 API 超时或超限
EMBED_BATCH_SIZE = 64
# API 调用最大重试次数
MAX_RETRIES = 3
# 内存查询向量缓存容量
_QUERY_CACHE_SIZE = 512
# 单条文本最大 token 数（智谱 embedding-3 限制 3072）
MAX_TOKENS_PER_TEXT = 3072
# 粗略估算：1 个中文字符 ≈ 1.5 token，取保守值，按字符数预检
MAX_CHARS_PER_TEXT = int(MAX_TOKENS_PER_TEXT / 1.5)
# 异步批量嵌入的并发上限，防止同时打出过多请求
_ASYNC_CONCURRENCY = 8
# 磁盘缓存目录（位于 data/ 下，与 chroma_db 同级）
_DISK_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "embed_cache"
# 磁盘缓存最大条目数
_DISK_CACHE_SIZE_LIMIT = 50_000


class Embedder:
    """封装 OpenAI 兼容的 Embedding API 调用。

    - 同步方法保持向后兼容，现有调用方无需改动
    - 新增 async 方法供高并发批量场景使用
    - 查询级双层缓存：内存 LRU + 磁盘持久化
    - 全部重试失败后降级返回零向量，不抛异常
    """

    def __init__(self):
        # 同步客户端（向后兼容）
        self.client = OpenAI(
            api_key=settings.effective_embed_api_key,
            base_url=settings.effective_embed_base_url,
        )
        # 异步客户端（批量入库用）
        self.async_client = AsyncOpenAI(
            api_key=settings.effective_embed_api_key,
            base_url=settings.effective_embed_base_url,
        )
        self.model = settings.effective_embed_model
        self.dimensions = settings.embedding_dimensions

        # ── 双层缓存 ──────────────────────────────────────────────────────
        # L1: 内存 LRU（快，进程内有效）
        self._query_cache: OrderedDict[str, List[float]] = OrderedDict()
        self._cache_lock = threading.Lock()

        # L2: 磁盘持久缓存（慢，跨进程 / 重启有效）
        self._disk_cache = self._init_disk_cache()

        # 零向量维度（首次实际调用后确定）
        self._zero_dim: Optional[int] = self.dimensions

    # ── 磁盘缓存初始化 ────────────────────────────────────────────────────

    @staticmethod
    def _init_disk_cache():
        """初始化 diskcache，失败时降级为 None（仅用内存缓存）。"""
        try:
            import diskcache
            _DISK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            return diskcache.Cache(
                str(_DISK_CACHE_DIR),
                size_limit=_DISK_CACHE_SIZE_LIMIT * 8 * 1024,  # 粗估每条 8KB
                eviction_policy="least-recently-used",
            )
        except ImportError:
            logger.warning("diskcache 未安装，查询缓存仅使用内存（pip install diskcache）")
            return None
        except Exception as e:
            logger.warning(f"磁盘缓存初始化失败，降级为内存缓存: {e}")
            return None

    # ── 缓存操作（线程安全）─────────────────────────────────────────────

    def _cache_get(self, key: str) -> Optional[List[float]]:
        """L1 → L2 查找缓存。"""
        with self._cache_lock:
            if key in self._query_cache:
                self._query_cache.move_to_end(key)
                return self._query_cache[key]

        # L2 磁盘查找
        if self._disk_cache is not None:
            val = self._disk_cache.get(key)
            if val is not None:
                # 回填 L1
                with self._cache_lock:
                    self._put_memory_cache(key, val)
                return val
        return None

    def _cache_put(self, key: str, embedding: List[float]):
        """写入 L1 + L2。"""
        with self._cache_lock:
            self._put_memory_cache(key, embedding)
        if self._disk_cache is not None:
            try:
                self._disk_cache.set(key, embedding)
            except Exception as e:
                logger.debug(f"磁盘缓存写入失败: {e}")

    def _put_memory_cache(self, key: str, embedding: List[float]):
        """写入内存 LRU（调用者需持有 _cache_lock）。"""
        if len(self._query_cache) >= _QUERY_CACHE_SIZE:
            self._query_cache.popitem(last=False)
        self._query_cache[key] = embedding

    # ── 零向量降级 ────────────────────────────────────────────────────────

    def _zero_vector(self) -> List[float]:
        """返回零向量，用于 API 全部失败时的优雅降级。"""
        dim = self._zero_dim or 1024
        return [0.0] * dim

    # ── 同步方法（向后兼容）─────────────────────────────────────────────

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """批量计算文本向量（同步），自动分批防止单次请求过大。"""
        if not texts:
            return []

        texts = [t[:MAX_CHARS_PER_TEXT] if len(t) > MAX_CHARS_PER_TEXT else t for t in texts]
        logger.info(f"正在计算 {len(texts)} 条文本的 embedding...")

        all_embeddings: List[List[float]] = []
        for i in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[i: i + EMBED_BATCH_SIZE]
            batch_embeddings = self._embed_with_retry(batch)
            all_embeddings.extend(batch_embeddings)

        if all_embeddings and all_embeddings[0]:
            self._zero_dim = len(all_embeddings[0])
        logger.info(f"Embedding 计算完成，共 {len(all_embeddings)} 条，维度: {self._zero_dim}")
        return all_embeddings

    def _embed_with_retry(self, texts: List[str]) -> List[List[float]]:
        """调用 embedding API，失败时自动重试；全部失败时降级返回零向量。"""
        last_error: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                kwargs = {"model": self.model, "input": texts}
                if self.dimensions is not None:
                    kwargs["dimensions"] = self.dimensions
                response = self.client.embeddings.create(**kwargs)
                return [
                    item.embedding for item in sorted(response.data, key=lambda x: x.index)
                ]
            except (APITimeoutError, APIConnectionError, RateLimitError) as e:
                last_error = e
                if attempt == MAX_RETRIES:
                    break
                wait = 2 ** attempt + random.uniform(0, 1)
                logger.warning(f"Embedding API 调用失败 (第{attempt}次)，{wait:.1f}s 后重试: {e}")
                time.sleep(wait)

        # ── fallback: 全部重试失败，返回零向量 ──
        logger.error(f"Embedding API {MAX_RETRIES} 次重试全部失败，降级返回零向量: {last_error}")
        return [self._zero_vector() for _ in texts]

    def embed_query(self, query: str) -> List[float]:
        """计算单条查询文本的向量（带双层 LRU 缓存，线程安全）。"""
        cache_key = hashlib.md5(query.encode()).hexdigest()

        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        result = self.embed_texts([query])
        embedding = result[0]

        self._cache_put(cache_key, embedding)
        return embedding

    # ── 异步方法（批量入库高性能场景）──────────────────────────────────

    async def embed_texts_async(self, texts: List[str]) -> List[List[float]]:
        """异步批量计算文本向量，利用信号量控制并发，不阻塞事件循环。

        10 万条文档入库时使用此方法，比同步版本快数倍。
        """
        if not texts:
            return []

        texts = [t[:MAX_CHARS_PER_TEXT] if len(t) > MAX_CHARS_PER_TEXT else t for t in texts]
        logger.info(f"[async] 正在计算 {len(texts)} 条文本的 embedding...")

        semaphore = asyncio.Semaphore(_ASYNC_CONCURRENCY)
        batches = [texts[i: i + EMBED_BATCH_SIZE] for i in range(0, len(texts), EMBED_BATCH_SIZE)]

        async def _process_batch(batch: List[str]) -> List[List[float]]:
            async with semaphore:
                return await self._async_embed_with_retry(batch)

        results = await asyncio.gather(*[_process_batch(b) for b in batches])

        all_embeddings = [emb for batch_result in results for emb in batch_result]
        if all_embeddings and all_embeddings[0]:
            self._zero_dim = len(all_embeddings[0])
        logger.info(f"[async] Embedding 计算完成，共 {len(all_embeddings)} 条，维度: {self._zero_dim}")
        return all_embeddings

    async def _async_embed_with_retry(self, texts: List[str]) -> List[List[float]]:
        """异步调用 embedding API，失败时重试；全部失败时降级返回零向量。"""
        last_error: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                kwargs = {"model": self.model, "input": texts}
                if self.dimensions is not None:
                    kwargs["dimensions"] = self.dimensions
                response = await self.async_client.embeddings.create(**kwargs)
                return [
                    item.embedding for item in sorted(response.data, key=lambda x: x.index)
                ]
            except (APITimeoutError, APIConnectionError, RateLimitError) as e:
                last_error = e
                if attempt == MAX_RETRIES:
                    break
                wait = 2 ** attempt + random.uniform(0, 1)
                logger.warning(f"[async] Embedding API 调用失败 (第{attempt}次)，{wait:.1f}s 后重试: {e}")
                await asyncio.sleep(wait)

        logger.error(f"[async] Embedding API {MAX_RETRIES} 次重试全部失败，降级返回零向量: {last_error}")
        return [self._zero_vector() for _ in texts]

    async def embed_query_async(self, query: str) -> List[float]:
        """异步计算单条查询向量（带双层缓存）。"""
        cache_key = hashlib.md5(query.encode()).hexdigest()

        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        result = await self.embed_texts_async([query])
        embedding = result[0]

        self._cache_put(cache_key, embedding)
        return embedding
