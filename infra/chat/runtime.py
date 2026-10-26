"""Propagate a run's cancellation control through LangGraph model calls."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_CONTROL: ContextVar[Any] = ContextVar("model_run_control", default=None)


def current_model_control() -> Any:
    return _CONTROL.get()


@contextmanager
def model_control_context(control: Any) -> Iterator[None]:
    token = _CONTROL.set(control)
    try:
        yield
    finally:
        _CONTROL.reset(token)
