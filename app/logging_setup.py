"""logging 配置：把 ``request_id`` contextvar 注入每条 LogRecord（Step 025f）。

设计：
- 一个 ``logging.Filter`` 子类——``RequestIdLogFilter``——在 ``filter()`` 里
  从 ``request_id_var`` 取值，写到 ``record.request_id``；缺省 ``"-"``
  （Apache log 风格短哨兵，扫眼即识别）
- ``configure_logging(*, level, log_file)`` 替代旧的裸 ``logging.basicConfig``：
  - format 加 ``[%(request_id)s]`` 段
  - filter 添加到 root logger 的 *handler* 上（不是 logger 本身——`logger.addFilter`
    不级联给 child logger 的 handler；只有 handler 级 filter 才能覆盖所有 log）
  - 幂等：重复调用先清掉自己之前装的 handler，避免测试 setup 多次后日志翻倍
- 不影响 evaluations/ 里独立的 ``logging.basicConfig(level=WARNING)`` 脚本，
  本函数仅在 main.py 启动路径调用

为什么 filter 而不是 Formatter 子类：
- Formatter 子类要 override ``format()``，与 ``%`` 风格 / ``{`` 风格混淆且
  影响其它字段；filter 模式只往 record 注入属性，对 format 字符串透明
- filter 模式与 stdlib `logging` 文档示例一致，长期维护友好
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from app.request_context import get_request_id

DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] [%(request_id)s] %(name)s: %(message)s"
"""默认 logging 格式串：在 levelname 与 logger name 之间插入 ``[request_id]``。"""

_FILTER_MARKER = "_step025f_request_id_filter"
"""handler 上挂的属性名，用于幂等检测（同一 handler 不重复加 filter）。"""


class RequestIdLogFilter(logging.Filter):
    """从 ``request_id_var`` contextvar 拉值写入 ``LogRecord.request_id``。

    永远返回 ``True``——这是注入而非过滤。
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - stdlib API
        rid = get_request_id()
        # logging 在 Formatter 拼字符串时如果属性缺失会抛 KeyError；
        # 这里保证一定有值，缺省 "-" 与 Apache combined log 约定一致
        record.request_id = rid or "-"
        return True


def _attach_filter_to_handler(handler: logging.Handler) -> None:
    """在 handler 上挂 ``RequestIdLogFilter``；幂等。"""
    if getattr(handler, _FILTER_MARKER, False):
        return
    handler.addFilter(RequestIdLogFilter())
    setattr(handler, _FILTER_MARKER, True)


def configure_logging(
    *,
    level: int = logging.INFO,
    log_file: str | None = "logs/app.log",
    fmt: str = DEFAULT_FORMAT,
    extra_handlers: Iterable[logging.Handler] | None = None,
) -> None:
    """安装带 ``request_id`` 字段的 logging 配置；幂等。

    参数：
    - ``level``：root logger 等级，默认 ``INFO``。
    - ``log_file``：文件 handler 路径；``None`` 表示只打 stderr。
    - ``fmt``：format 串；默认带 ``[%(request_id)s]`` 段。
    - ``extra_handlers``：测试可注入额外 handler（如 ``MemoryHandler``）。

    行为：
    1. 把 root logger 的 level 调到 ``level``。
    2. 添加 stream handler（StreamHandler 默认输出 stderr）+ 可选 file handler
       + ``extra_handlers``，每个都装好 ``RequestIdLogFilter`` 与 formatter。
    3. 不清空 root logger 已有 handler（uvicorn / pytest 可能已挂自己的）；
       但每个新加的 handler 都打了 ``_FILTER_MARKER`` 防止重复装 filter。
    """
    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(fmt)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    if extra_handlers:
        handlers.extend(extra_handlers)

    for h in handlers:
        h.setLevel(level)
        h.setFormatter(formatter)
        _attach_filter_to_handler(h)
        # 幂等：同一类型 handler 已存在就不重复加（按类比对避免每次重启日志翻倍）
        if not any(
            isinstance(existing, type(h))
            and getattr(existing, "_step025f_owned", False)
            for existing in root.handlers
        ):
            h._step025f_owned = True  # type: ignore[attr-defined]
            root.addHandler(h)
