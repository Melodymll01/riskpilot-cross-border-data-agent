"""Docker Compose production profile 静态契约；不需要 Docker daemon。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PROFILE_ENV = {
    "AGENT_PLANNER_BACKEND": "deterministic",
    "EMBED_PROVIDER": "deterministic",
    "FACT_PROPOSAL_BACKEND": "safe_empty",
    "LLM_PROVIDER": "local",
}


def _compose_config(*global_args: str) -> str:
    return subprocess.run(
        [
            str(ROOT / "scripts" / "compose.sh"),
            "--env-file",
            "/dev/null",
            *global_args,
            "config",
        ],
        cwd=ROOT,
        env={**os.environ, **_DEFAULT_PROFILE_ENV},
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_default_compose_uses_shared_production_backends() -> None:
    config = yaml.safe_load(_compose_config())
    services = config["services"]
    api_environment = services["app"]["environment"]
    worker_environment = services["worker"]["environment"]

    assert set(services) == {
        "app",
        "migrate",
        "minio",
        "minio-init",
        "postgres",
        "redis",
        "worker",
    }
    for environment in (api_environment, worker_environment):
        assert environment["STORAGE_BACKEND"] == "postgres"
        assert environment["VECTOR_BACKEND"] == "pgvector"
        assert environment["TASK_BACKEND"] == "celery"
        assert environment["OBJECT_STORE_BACKEND"] == "s3"
        assert environment["DATABASE_URL"] == api_environment["DATABASE_URL"]
        assert environment["CELERY_BROKER_URL"] == api_environment["CELERY_BROKER_URL"]
        assert environment["S3_ENDPOINT_URL"] == api_environment["S3_ENDPOINT_URL"]
    assert api_environment["AGENT_PLANNER_BACKEND"] == "deterministic"
    assert api_environment["FACT_PROPOSAL_BACKEND"] == "safe_empty"
    assert api_environment["EMBED_PROVIDER"] == "deterministic"
    assert "OPENAI_API_KEY" not in api_environment


def test_compose_has_migration_health_restart_resource_and_named_volume_contracts() -> None:
    config = yaml.safe_load(_compose_config())
    services = config["services"]

    assert services["app"]["depends_on"]["migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["worker"]["depends_on"]["migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["migrate"]["command"] == ["alembic", "upgrade", "head"]
    for name in ("app", "worker", "postgres", "redis", "minio"):
        service = services[name]
        assert service["restart"] == "unless-stopped"
        assert "healthcheck" in service
        assert int(service["mem_limit"]) > 0
        assert float(service["cpus"]) > 0
    assert services["app"]["init"] is True
    assert services["worker"]["init"] is True
    assert services["app"]["image"] == services["worker"]["image"]
    assert services["app"]["stop_grace_period"] == "30s"
    assert services["worker"]["stop_grace_period"] == "30s"
    assert all(volume["type"] == "volume" for volume in services["app"]["volumes"])
    assert all(volume["type"] == "volume" for volume in services["worker"]["volumes"])
    assert set(config["volumes"]) >= {
        "app-data",
        "app-logs",
        "postgres-data",
        "redis-data",
        "minio-data",
    }


def test_tools_and_observability_profiles_are_explicit() -> None:
    profiles = set(
        subprocess.run(
            [
                str(ROOT / "scripts" / "compose.sh"),
                "--env-file",
                "/dev/null",
                "config",
                "--profiles",
            ],
            cwd=ROOT,
            env={**os.environ, **_DEFAULT_PROFILE_ENV},
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    )
    services = set(
        subprocess.run(
            [
                str(ROOT / "scripts" / "compose.sh"),
                "--env-file",
                "/dev/null",
                "config",
                "--services",
            ],
            cwd=ROOT,
            env={**os.environ, **_DEFAULT_PROFILE_ENV},
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    )
    observability = yaml.safe_load(_compose_config("--profile", "observability"))
    tools = yaml.safe_load(_compose_config("--profile", "tools"))

    assert profiles == {"observability", "tools"}
    assert "seed" not in services
    assert "prometheus" not in services
    assert "prometheus" in observability["services"]
    assert "seed" in tools["services"]
    assert tools["services"]["seed"]["command"] == ["python", "-m", "scripts.seed_demo"]
    assert observability["services"]["prometheus"]["ports"][0]["target"] == 9090
