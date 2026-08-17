"""Dockerfile 的非 root、graceful shutdown 和 health 契约。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_image_is_non_root_and_signal_safe() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12-slim-bookworm AS builder" in dockerfile
    assert "FROM python:3.12-slim-bookworm AS runtime" in dockerfile
    assert "COPY --chown=app:app . ." in dockerfile
    assert "\nUSER app\n" in dockerfile
    assert "STOPSIGNAL SIGTERM" in dockerfile
    assert "EXPOSE 8001 9101" in dockerfile
    assert "/api/v2/health/ready" in dockerfile
    runtime_stage = dockerfile.split("FROM python:3.12-slim-bookworm AS runtime", maxsplit=1)[1]
    assert "build-essential" not in runtime_stage
    assert "gcc" not in runtime_stage
    assert "libgl1" in runtime_stage
    assert "libglib2.0-0" in runtime_stage
    assert "libx11-xcb1" in runtime_stage
