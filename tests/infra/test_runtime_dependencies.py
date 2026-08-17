"""生产镜像直接 import 的依赖必须声明在 requirements.txt。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_jwt_runtime_dependency_is_not_dev_only() -> None:
    runtime = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    development = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")

    assert "PyJWT" in runtime
    assert "PyJWT" not in development
