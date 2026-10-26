"""Validate the actual Compose model without starting a daemon."""

import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_compose_exposes_go_and_keeps_python_private():
    result = subprocess.run(
        [
            str(ROOT / "scripts/compose.sh"),
            "--env-file",
            "/dev/null",
            "config",
            "--no-env-resolution",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    config = yaml.safe_load(result.stdout)
    assert set(config["services"]) == {"app", "gateway"}
    app = config["services"]["app"]
    assert app["init"] is True
    assert app["restart"] == "unless-stopped"
    assert {mount["target"] for mount in app["volumes"]} == {"/app/data", "/app/logs"}
    assert app["environment"]["SQLITE_DB_PATH"] == "/app/data/rag.sqlite3"
    assert app["environment"]["CHROMA_PERSIST_DIR"] == "/app/data/chroma_db"
    assert "depends_on" not in app
    assert "profiles" not in app
    assert "ports" not in app
    assert app["environment"]["INTERNAL_SERVICE_REQUIRED"] == "true"
    assert app["environment"]["INTERNAL_SERVICE_KEY_FILE"] == "/app/data/internal-service.key"
    gateway = config["services"]["gateway"]
    assert gateway["environment"]["PYTHON_SERVICE_URL"] == "http://app:8001"
    assert gateway["depends_on"]["app"]["condition"] == "service_healthy"
    assert gateway["build"]["dockerfile"] == "services/api-go/Dockerfile"
    assert gateway["ports"]
