"""CLI transport tests use an isolated process; live GPT is a separate verification."""

import json
import os
import sys
import time
from pathlib import Path

import pytest
from langchain_core.tools import tool

from infra.chat.traex_model import TraexChatModel, _decode_events


def _cli(tmp_path: Path, body: str) -> str:
    path = tmp_path / "traex"
    path.write_text(f"#!{sys.executable}\n" + body, encoding="utf-8")
    path.chmod(0o700)
    return str(path)


def _events(content: str, calls=None) -> str:
    return "\n".join(
        json.dumps(event)
        for event in [
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": json.dumps({"content": content, "tool_calls": calls or []}),
                },
            },
            {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 3}},
        ]
    )


def test_text_transport_is_ephemeral_and_reports_real_identity(tmp_path):
    path = _cli(
        tmp_path,
        "import sys, json, os\n"
        "assert '--ephemeral' in sys.argv and '--disallowed-tool' in sys.argv\n"
        "assert os.getcwd().split('/')[-1].startswith('riskpilot-inference-')\n"
        "request=json.load(sys.stdin)\n"
        "assert request['messages'][0]['content'] == '你好'\n"
        f"print({_events('已接通')!r})\n",
    )
    model = TraexChatModel(cli_path=path)
    answer = model.invoke("你好")
    assert answer.content == "已接通"
    assert answer.response_metadata["provider"] == "trae"
    assert answer.response_metadata["cli"] == "traex"
    assert answer.usage_metadata["total_tokens"] == 13
    assert model.supports_incremental_text is False


def test_tool_decision_is_converted_and_arguments_are_validated(tmp_path):
    @tool
    def search_law(query: str) -> str:
        """Look up a law."""
        raise AssertionError("transport must never execute application tools")

    output = _events("", [{"name": "search_law", "arguments_json": '{"query":"数据出境"}'}])
    path = _cli(tmp_path, f"import sys\nsys.stdin.read()\nprint({output!r})\n")
    answer = TraexChatModel(cli_path=path).bind_tools([search_law]).invoke("查法规")
    assert answer.tool_calls[0]["name"] == "search_law"
    assert answer.tool_calls[0]["args"] == {"query": "数据出境"}


def test_internal_tools_and_missing_terminal_are_rejected():
    with pytest.raises(RuntimeError, match="forbidden"):
        _decode_events(
            json.dumps({"type": "item.completed", "item": {"type": "command_execution"}})
        )
    with pytest.raises(RuntimeError, match="completed"):
        _decode_events('{"type":"turn.started"}')


def test_timeout_terminates_child_process(tmp_path, monkeypatch):
    import subprocess

    actual_popen = subprocess.Popen
    processes = []

    def tracked_popen(*args, **kwargs):
        process = actual_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", tracked_popen)
    path = _cli(
        tmp_path,
        "import os, sys, time\nsys.stdin.read()\ntime.sleep(10)\n",
    )
    with pytest.raises(TimeoutError):
        TraexChatModel(cli_path=path, timeout_seconds=0.25).invoke("test")
    assert processes
    pid = processes[0].pid
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_runtime_cancellation_stops_cli_process(tmp_path):
    class Cancelled(Exception):
        pass

    class Control:
        def __init__(self):
            self.calls = 0

        def check(self):
            self.calls += 1
            if self.calls > 4:
                raise Cancelled()

    path = _cli(tmp_path, "import sys,time\nsys.stdin.read()\ntime.sleep(10)\n")
    started = time.monotonic()
    with pytest.raises(Cancelled):
        TraexChatModel(cli_path=path).invoke("test", runtime_control=Control())
    assert time.monotonic() - started < 2
