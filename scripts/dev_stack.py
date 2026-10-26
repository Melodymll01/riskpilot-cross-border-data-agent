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
STATE = ROOT / "data/dev-stack.json"
LOG = ROOT / "logs/dev-stack.log"


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


def run(python: str, go: str) -> None:
    env = environment()
    go_port = int(env.get("GATEWAY_PORT", "8765"))
    python_port = int(env.get("PYTHON_SERVICE_PORT", "8766"))
    if go_port == python_port:
        raise RuntimeError("Go and Python require separate ports")
    for port in [go_port, python_port]:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    binary = ROOT / "data/bin/riskpilot-api"
    binary.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [go, "build", "-o", str(binary), "./cmd/riskpilot"],
        cwd=ROOT / "services/api-go",
        env=go_environment(env),
        check=True,
    )
    env.update(
        {
            "GATEWAY_LISTEN": f"127.0.0.1:{go_port}",
            "PYTHON_SERVICE_URL": f"http://127.0.0.1:{python_port}",
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
            private_env = {
                **env,
                "INTERNAL_SERVICE_REQUIRED": "true",
                "RATE_LIMIT_ENABLED": "false",
            }
            python_process = subprocess.Popen(
                [
                    python,
                    "-m",
                    "uvicorn",
                    "main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(python_port),
                ],
                cwd=ROOT,
                env=private_env,
                stdout=output,
                stderr=output,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            children.append(python_process)
            wait_ready(
                f"http://127.0.0.1:{python_port}/api/v2/health/ready",
                python_process,
                time.monotonic() + 120,
            )
            gateway = subprocess.Popen(
                [str(binary)],
                cwd=ROOT,
                env=env,
                stdout=output,
                stderr=output,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            children.append(gateway)
            wait_ready(
                f"http://127.0.0.1:{go_port}/api/v2/health/ready", gateway, time.monotonic() + 15
            )
            STATE.write_text(
                json.dumps(
                    {
                        "manager": os.getpid(),
                        "python": python_process.pid,
                        "gateway": gateway.pid,
                        "go_port": go_port,
                        "python_port": python_port,
                    }
                )
            )
            print(
                f"Go API: http://127.0.0.1:{go_port}; private Python: {python_port}; log: {LOG}",
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
