"""Run and stop the local Go entry and the private Python Agent as one stack."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from dotenv import dotenv_values
from go_tool import go_environment

ROOT = Path(__file__).resolve().parents[1]
AGENT_RUNTIME = ROOT / "services/agent-runtime"
CONTROL_PLANE = ROOT / "services/control-plane"
STATE = ROOT / "data/dev-stack.json"
LOG = ROOT / "logs/dev-stack.log"

CONTROL_PLANE_KEYS = {
    "RISKPILOT_ROOT",
    "INTERNAL_SERVICE_KEY_FILE",
    "CONTROL_PLANE_LISTEN",
    "CONTROL_PLANE_DB_PATH",
    "CONTROL_PLANE_MAX_ACTIVE_REQUESTS",
    "CONTROL_PLANE_RUNTIME_TIMEOUT_SECONDS",
    "CONTROL_PLANE_RATE_PER_MINUTE",
    "AGENT_RUNTIME_URL",
    "JWT_SECRET",
    "JWT_TTL_SECONDS",
    "GITHUB_CLIENT_ID",
    "GITHUB_CLIENT_SECRET",
    "GITHUB_REDIRECT_URI",
    "ADMIN_USER_IDS",
    "COOKIE_NAME",
    "COOKIE_SECURE",
    "COOKIE_SAMESITE",
    "MAX_UPLOAD_MB",
    "RATE_LIMIT_ENABLED",
    "RUN_MAX_DURATION_SECONDS",
    "RUN_MAX_MODEL_CALLS",
    "RUN_MAX_TOOL_CALLS",
    "SSE_KEEPALIVE_SECONDS",
}

AGENT_RUNTIME_KEYS = {
    "AGENT_RUNTIME_ARTIFACT_DB_PATH",
    "AGENT_RUNTIME_EMBED_CACHE_DIR",
    "AGENT_RUNTIME_LANGGRAPH_CHECKPOINT_DB_PATH",
    "AGENT_RUNTIME_INTERNAL_SERVICE_KEY_FILE",
    "LLM_PROVIDER",
    "EMBED_PROVIDER",
    "LOCAL_SENTENCE_EMBEDDING_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "CHAT_MODEL",
    "CHAT_API_KEY",
    "CHAT_API_BASE",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIMENSIONS",
    "CHAT_MAX_TOKENS",
    "OLLAMA_API_BASE",
    "LOCAL_CHAT_MODEL",
    "LOCAL_EMBEDDING_MODEL",
    "CHROMA_PERSIST_DIR",
    "ENABLE_QUERY_REWRITE",
    "ENABLE_RERANKER",
    "RERANKER_MODEL",
    "RERANKER_DEVICE",
    "CHUNK_SIZE",
    "CHUNK_OVERLAP",
    "EMBEDDING_MAX_INPUT_TOKENS",
    "INDEX_SCHEMA_VERSION",
    "EMBEDDING_INPUT_STRATEGY",
    "CONVERSATION_MODEL_CALL_TIMEOUT_SECONDS",
    "CONVERSATION_TOOL_CALL_TIMEOUT_SECONDS",
    "CONVERSATION_CONTROL_POLL_SECONDS",
    "JSON_LOGS_ENABLED",
    "PROMETHEUS_ENABLED",
    "OBSERVABILITY_HASH_SALT",
    "RISK_PILOT_LANGSMITH_ENABLED",
    "LANGSMITH_API_KEY",
    "LANGSMITH_ENDPOINT",
    "LANGSMITH_PROJECT",
    "LANGSMITH_HASH_SALT",
    "OTEL_ENABLED",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
}


def environment() -> dict[str, str]:
    values = {
        key: value for key, value in dotenv_values(ROOT / ".env").items() if value is not None
    }
    env = {**values, **os.environ}
    key = Path(env.get("INTERNAL_SERVICE_KEY_FILE", ROOT / "data/internal-service.key"))
    if not key.is_absolute():
        key = ROOT / key
    key.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if len(key.read_text().strip()) < 32:
            raise RuntimeError("Existing internal service key is invalid") from None
    else:
        with os.fdopen(descriptor, "w") as output:
            output.write(secrets.token_hex(32))
    env.update({"RISKPILOT_ROOT": str(ROOT), "INTERNAL_SERVICE_KEY_FILE": str(key)})
    if not env.get("JWT_SECRET") or len(env["JWT_SECRET"]) < 32:
        raise RuntimeError("Set JWT_SECRET with at least 32 characters in .env")
    return env


def service_environment(source: dict[str, str], allowed: set[str]) -> dict[str, str]:
    """Pass only one service's configuration on top of the process environment."""
    return {
        **os.environ,
        **{key: source[key] for key in allowed if key in source},
    }


def stop_group(pid: int) -> None:
    try:
        # Only process-group leaders recorded by this launcher may be stopped.
        if os.getpgid(pid) != pid:
            raise RuntimeError("Recorded PID is not a managed process group")
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return


def wait_ready(url: str, process: subprocess.Popen, deadline: float) -> None:
    opener = build_opener(ProxyHandler({}))
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Service exited with {process.returncode}; see {LOG}")
        try:
            with opener.open(url, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"Service startup timed out; see {LOG}")


def wait_port(host: str, port: int, process: subprocess.Popen, deadline: float) -> None:
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Service exited with {process.returncode}; see {LOG}")
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"Service startup timed out; see {LOG}")


def run(python: str, go: str) -> None:
    env = environment()
    control_plane_port = int(env.get("CONTROL_PLANE_PORT", "8765"))
    agent_runtime_port = int(env.get("AGENT_RUNTIME_PORT", "8766"))
    if control_plane_port == agent_runtime_port:
        raise RuntimeError("Go and Python require separate ports")
    for port in [control_plane_port, agent_runtime_port]:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    binary = ROOT / "data/bin/riskpilot-control-plane"
    binary.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [go, "build", "-o", str(binary), "./cmd/riskpilot"],
        cwd=CONTROL_PLANE,
        env=go_environment(service_environment(env, {"GO_SDKROOT"})),
        check=True,
    )
    env.update(
        {
            "CONTROL_PLANE_LISTEN": f"127.0.0.1:{control_plane_port}",
            "AGENT_RUNTIME_URL": f"http://127.0.0.1:{agent_runtime_port}",
            "CONTROL_PLANE_DB_PATH": str(ROOT / "data/control-plane.sqlite3"),
            "AGENT_RUNTIME_ARTIFACT_DB_PATH": str(ROOT / "data/agent-runtime-artifacts.sqlite3"),
            "AGENT_RUNTIME_LANGGRAPH_CHECKPOINT_DB_PATH": str(
                ROOT / "data/conversation-checkpoints.sqlite3"
            ),
        }
    )
    control_env = service_environment(env, CONTROL_PLANE_KEYS)
    runtime_env = service_environment(env, AGENT_RUNTIME_KEYS)
    runtime_env.update(
        {
            "PYTHONPATH": str(AGENT_RUNTIME),
            "PYTHONSAFEPATH": "1",
            "AGENT_RUNTIME_INTERNAL_SERVICE_KEY_FILE": env["INTERNAL_SERVICE_KEY_FILE"],
        }
    )
    LOG.parent.mkdir(exist_ok=True)
    children: list[subprocess.Popen] = []
    interrupted = False

    def on_signal(signum: int, frame: object) -> None:
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    try:
        with LOG.open("ab") as output:
            python_process = subprocess.Popen(
                [
                    python,
                    "-m",
                    "uvicorn",
                    "main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(agent_runtime_port),
                ],
                cwd=ROOT,
                env=runtime_env,
                stdout=output,
                stderr=output,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            children.append(python_process)
            wait_port(
                "127.0.0.1",
                agent_runtime_port,
                python_process,
                time.monotonic() + 120,
            )
            control_plane = subprocess.Popen(
                [str(binary)],
                cwd=ROOT,
                env=control_env,
                stdout=output,
                stderr=output,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            children.append(control_plane)
            wait_ready(
                f"http://127.0.0.1:{control_plane_port}/api/v2/health/ready",
                control_plane,
                time.monotonic() + 15,
            )
            STATE.write_text(
                json.dumps(
                    {
                        "manager": os.getpid(),
                        "agent_runtime": python_process.pid,
                        "control_plane": control_plane.pid,
                        "control_plane_port": control_plane_port,
                        "agent_runtime_port": agent_runtime_port,
                    }
                )
            )
            print(
                "Control plane: "
                f"http://127.0.0.1:{control_plane_port}; "
                f"private agent runtime: {agent_runtime_port}; log: {LOG}",
                flush=True,
            )
            while not interrupted:
                for process in children:
                    if process.poll() is not None:
                        raise RuntimeError(f"Stack service exited; see {LOG}")
                time.sleep(0.5)
    finally:
        for process in reversed(children):
            if process.poll() is None:
                stop_group(process.pid)
        for process in children:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        STATE.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "status", "prepare"])
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--go", default="go")
    parser.add_argument("--detach", action="store_true")
    args = parser.parse_args()
    if args.action == "prepare":
        env = environment()
        print(f"Prepared local service key: {env['INTERNAL_SERVICE_KEY_FILE']}")
    elif args.action == "status":
        print(STATE.read_text() if STATE.exists() else "Managed stack is not running")
    elif args.action == "stop":
        if STATE.exists():
            state = json.loads(STATE.read_text())
            try:
                command = subprocess.check_output(
                    ["ps", "-p", str(state["manager"]), "-o", "command="], text=True
                )
            except subprocess.CalledProcessError:
                raise RuntimeError(
                    "Stack manager no longer exists; check listeners before cleanup"
                ) from None
            if "dev_stack.py start" not in command:
                raise RuntimeError("Recorded manager does not match dev_stack")
            os.kill(state["manager"], signal.SIGTERM)
            print("Requested managed stack shutdown")
    elif args.detach:
        if STATE.exists():
            raise RuntimeError("A managed stack is already recorded; use status/stop")
        environment()
        LOG.parent.mkdir(exist_ok=True)
        with LOG.open("ab") as output:
            process = subprocess.Popen(
                [
                    args.python,
                    str(Path(__file__).resolve()),
                    "start",
                    "--python",
                    args.python,
                    "--go",
                    args.go,
                ],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                start_new_session=True,
            )
        print(f"Starting stack manager PID {process.pid}; log: {LOG}")
    else:
        run(args.python, args.go)


if __name__ == "__main__":
    main()
