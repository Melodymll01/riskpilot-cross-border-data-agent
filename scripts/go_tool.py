"""Run Go with project-local build overrides, without changing global tool settings."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def go_environment(env: dict[str, str] | None = None) -> dict[str, str]:
    values = {
        key: value for key, value in dotenv_values(ROOT / ".env").items() if value is not None
    }
    result = {**values, **(os.environ if env is None else env)}
    if result.get("GO_SDKROOT"):
        result["SDKROOT"] = result["GO_SDKROOT"]
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: go_tool.py <Go arguments>")
    raise SystemExit(
        subprocess.run(
            [os.environ.get("RISKPILOT_GO", "go"), *sys.argv[1:]],
            cwd=ROOT / "services/control-plane",
            env=go_environment(),
        ).returncode
    )
