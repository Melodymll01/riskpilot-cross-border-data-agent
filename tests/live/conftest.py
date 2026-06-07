r"""``tests/live`` 共享 fixtures —— 命中**真实**外部服务的端到端测试。

与 ``tests/api`` 的 Fake 注入相反：这里直接用生产装配的 ``main.app``（真适配器，
真调智谱 embedding-3 + 百炼 GLM-5），用于验证整条 RAG 链路真能跑通。

双重保险，默认不跑（保护普通 ``pytest`` 全量轮次不产生网络调用 / 不花钱）：
1. 必须显式开启：环境变量 ``RUN_LIVE=1``
2. ``.env`` 里必须是真 key（非占位符 ``sk-placeholder``）

任一不满足 → ``pytest.skip``。本地跑法::

    $env:RUN_LIVE = "1"; .\.venv\Scripts\python.exe -m pytest -m live -q

注意：root ``tests/conftest.py`` 在收集期会把 ``CHROMA_PERSIST_DIR`` / ``UPLOAD_DIR``
重定向到临时目录，因此 live 测试连的是**空的临时 chroma**——测试自带文档上传，
既覆盖"真 embedding 写入"路径，又**不污染**真实 ``data/chroma_db``。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from config import settings

if TYPE_CHECKING:
    from fastapi import FastAPI

_PLACEHOLDER = "sk-placeholder"


def _has_real_keys() -> bool:
    """embedding 与 chat 的有效 key 均非占位符时返回 True。"""
    embed_key = settings.openai_api_key
    embed_ok = bool(embed_key) and embed_key != _PLACEHOLDER
    chat_key = settings.chat_api_key or settings.openai_api_key
    chat_ok = bool(chat_key) and chat_key != _PLACEHOLDER
    return embed_ok and chat_ok


@pytest.fixture(autouse=True)
def _guard_live() -> None:
    """每个 live 用例前的双重门禁：未开启或无真 key 直接 skip。"""
    if os.environ.get("RUN_LIVE") != "1":
        pytest.skip("live 测试默认关闭；设 RUN_LIVE=1 开启")
    if not _has_real_keys():
        pytest.skip("缺少真实 LLM/embedding key（.env 仍是占位符），跳过 live 测试")


@pytest.fixture(scope="session")
def live_app() -> FastAPI:
    """生产装配的 FastAPI 应用（真容器、真适配器）。

    延迟到 fixture 内 import，确保非 live 轮次不会触发 ``main`` 的真实容器构建。
    """
    from main import app

    return app
