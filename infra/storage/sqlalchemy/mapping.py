"""Domain Pydantic Model 与 ORM Row 的显式映射工具。"""

from __future__ import annotations

from datetime import UTC, datetime


def to_datetime(timestamp: float | None) -> datetime | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=UTC)


def require_datetime(timestamp: float) -> datetime:
    value = to_datetime(timestamp)
    assert value is not None
    return value


def to_timestamp(value: datetime | None) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


def require_timestamp(value: datetime) -> float:
    timestamp = to_timestamp(value)
    assert timestamp is not None
    return timestamp
