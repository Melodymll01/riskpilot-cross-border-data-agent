from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]


def test_compose_uses_final_service_and_storage_boundaries() -> None:
    config = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    assert set(config["services"]) == {"control-plane", "agent-runtime"}

    runtime = config["services"]["agent-runtime"]
    assert "env_file" not in runtime
    assert runtime["build"]["dockerfile"] == "services/agent-runtime/Dockerfile"
    assert runtime["environment"]["AGENT_RUNTIME_ARTIFACT_DB_PATH"].endswith(
        "agent-runtime-artifacts.sqlite3"
    )
    assert "SQLITE_DB_PATH" not in runtime["environment"]
    assert "JWT_SECRET" not in runtime["environment"]

    control = config["services"]["control-plane"]
    assert "env_file" not in control
    assert control["build"]["dockerfile"] == "services/control-plane/Dockerfile"
    assert control["environment"]["AGENT_RUNTIME_URL"] == "http://agent-runtime:8001"
    assert control["environment"]["CONTROL_PLANE_DB_PATH"].endswith("control-plane.sqlite3")
    assert "PYTHON_SERVICE_URL" not in control["environment"]
    assert "CHAT_API_KEY" not in control["environment"]
    assert "OPENAI_API_KEY" not in control["environment"]


def test_old_source_roots_and_proxy_transport_are_gone() -> None:
    old_paths = [
        "api",
        "app",
        "domain",
        "infra",
        "ingestion",
        "processing",
        "retrieval",
        "evaluations",
        "tests",
        "main.py",
        "config.py",
        "services/api-go",
    ]
    assert all(not (ROOT / path).exists() for path in old_paths)

    go_source = "\n".join(
        path.read_text() for path in (ROOT / "services/control-plane").rglob("*.go")
    )
    assert "httputil.ReverseProxy" not in go_source
    assert "services/api-go" not in go_source

    runtime_source = "\n".join(
        path.read_text()
        for path in (ROOT / "services/agent-runtime").rglob("*.py")
        if "tests" not in path.parts
    )
    assert 'APIRouter(prefix="/api/v2' not in runtime_source
    assert "ConversationRunRepoPort" not in runtime_source


def test_repository_markdown_is_centralized_under_docs() -> None:
    excluded = {".git", ".venv", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    outside: list[Path] = []
    for path in ROOT.rglob("*.md"):
        relative = path.relative_to(ROOT)
        if any(part in excluded for part in relative.parts):
            continue
        if relative.parts[0] != "docs":
            outside.append(relative)
    assert outside == []
