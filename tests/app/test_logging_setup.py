"""``app.logging_setup`` 单测（Step 025f）。

测点：
- ``RequestIdLogFilter`` 在 ``request_context`` 内/外注入正确的 ``record.request_id``
- ``configure_logging`` 让 root logger 上 handler 都带上 filter（缺省 ``"-"``）
- 缺省占位符与 contextvar 值在 format 输出里实际可见
- 幂等：重复 ``configure_logging`` 不会让同一类型 handler 翻倍

策略：
- 用 ``MemoryHandler`` 收集 record 做精确断言，不污染 stderr
- 配合 ``request_context`` contextmanager 切换 contextvar
"""

from __future__ import annotations

import logging
from logging.handlers import MemoryHandler

import pytest

from app.logging_setup import (
    DEFAULT_FORMAT,
    RequestIdLogFilter,
    configure_logging,
)
from app.request_context import request_context


@pytest.fixture
def isolated_root_logger() -> logging.Logger:
    """给本测试模块一份隔离的 root logger handler 列表，结束后还原。"""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    # 清空，让 configure_logging 从空开始
    for h in list(root.handlers):
        root.removeHandler(h)
    yield root
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)


def _make_record(msg: str = "hello") -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=None,
        exc_info=None,
    )


class TestRequestIdLogFilter:
    def test_default_uses_dash_when_no_context(self) -> None:
        rec = _make_record()
        assert RequestIdLogFilter().filter(rec) is True
        assert rec.request_id == "-"

    def test_picks_up_contextvar(self) -> None:
        rec = _make_record()
        with request_context("req-log-1"):
            assert RequestIdLogFilter().filter(rec) is True
            assert rec.request_id == "req-log-1"

    def test_returns_true_always(self) -> None:
        # filter 是注入而非过滤；无论有没有 contextvar 都不丢 record
        f = RequestIdLogFilter()
        rec1 = _make_record()
        rec2 = _make_record()
        assert f.filter(rec1) is True
        with request_context("rid"):
            assert f.filter(rec2) is True


class TestConfigureLogging:
    def test_attaches_filter_to_handlers(
        self, isolated_root_logger: logging.Logger
    ) -> None:
        mem = MemoryHandler(capacity=10)
        configure_logging(level=logging.INFO, log_file=None, extra_handlers=[mem])

        # mem handler 已被加到 root，带 filter
        assert mem in isolated_root_logger.handlers
        assert any(isinstance(f, RequestIdLogFilter) for f in mem.filters)

    def test_format_includes_request_id(
        self, isolated_root_logger: logging.Logger
    ) -> None:
        mem = MemoryHandler(capacity=100)
        configure_logging(level=logging.INFO, log_file=None, extra_handlers=[mem])

        with request_context("req-fmt-1"):
            isolated_root_logger.info("hello world")

        assert mem.buffer, "应至少收到一条 record"
        rec = mem.buffer[-1]
        # filter 注入了 request_id 属性
        assert rec.request_id == "req-fmt-1"
        # formatter 拼出来含 request_id 段
        formatted = logging.Formatter(DEFAULT_FORMAT).format(rec)
        assert "[req-fmt-1]" in formatted
        assert "hello world" in formatted

    def test_dash_appears_when_outside_context(
        self, isolated_root_logger: logging.Logger
    ) -> None:
        mem = MemoryHandler(capacity=100)
        configure_logging(level=logging.INFO, log_file=None, extra_handlers=[mem])

        isolated_root_logger.info("no context")

        rec = mem.buffer[-1]
        assert rec.request_id == "-"
        formatted = logging.Formatter(DEFAULT_FORMAT).format(rec)
        assert "[-]" in formatted

    def test_idempotent_does_not_duplicate_handlers(
        self, isolated_root_logger: logging.Logger
    ) -> None:
        # 调两次：自家装的 StreamHandler 不应被加两遍（按 ``_step025f_owned``
        # 标记去重）。注：pytest LogCaptureHandler 是 StreamHandler 子类但
        # 没有 _step025f_owned 标记，过滤掉
        configure_logging(level=logging.INFO, log_file=None)
        first_count = sum(
            1
            for h in isolated_root_logger.handlers
            if getattr(h, "_step025f_owned", False)
        )
        configure_logging(level=logging.INFO, log_file=None)
        second_count = sum(
            1
            for h in isolated_root_logger.handlers
            if getattr(h, "_step025f_owned", False)
        )
        assert first_count == 1
        assert second_count == 1

    def test_nested_context_in_log_record(
        self, isolated_root_logger: logging.Logger
    ) -> None:
        mem = MemoryHandler(capacity=100)
        configure_logging(level=logging.INFO, log_file=None, extra_handlers=[mem])

        with request_context("outer"):
            isolated_root_logger.info("a")
            with request_context("inner"):
                isolated_root_logger.info("b")
            isolated_root_logger.info("c")

        assert [r.request_id for r in mem.buffer] == ["outer", "inner", "outer"]
