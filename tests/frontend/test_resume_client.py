"""Small Node-backed checks for resume stream terminal UI states."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _node(source: str) -> dict[str, object]:
    assert NODE is not None
    completed = subprocess.run(
        [NODE, "--input-type=module", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_resume_completion_states_cover_success_conflict_and_failure() -> None:
    chat_url = (ROOT / "frontend" / "chat.js").as_uri()
    result = _node(
        f"""
        const {{ authoritativeRunStatus, isCurrentStream, resumeCompletionState, runStateLabel, shouldDisplayAnswer }} = await import({json.dumps(chat_url)});
        const answerStates = ["completed", "failed", "cancelled", "timed_out", "budget_exhausted"]
          .map((status) => authoritativeRunStatus(status, "answer", {{ text: "display only" }}));
        const displayAnswer = ["completed", "failed", "cancelled", "timed_out", "budget_exhausted", null]
          .map((status) => shouldDisplayAnswer(status));
        console.log(JSON.stringify({{
          success: resumeCompletionState({{ receivedAnswer: true, runStatus: "completed" }}),
          answerWithoutStatus: resumeCompletionState({{ receivedAnswer: true }}),
          conflict: resumeCompletionState({{ errorStatus: 409 }}),
          failure: resumeCompletionState({{ errorStatus: 500 }}),
          incomplete: resumeCompletionState(),
          interrupted: resumeCompletionState({{ runStatus: "interrupted" }}),
          cancelled: resumeCompletionState({{ runStatus: "cancelled" }}),
          timedOut: resumeCompletionState({{ runStatus: "timed_out" }}),
          failedWithAnswer: resumeCompletionState({{ receivedAnswer: true, runStatus: "failed" }}),
          current: isCurrentStream(4, 4),
          stale: isCurrentStream(3, 4),
          answerStates,
          displayAnswer,
          states: ["agent", "generation", "verification", "cancel_requested", "completed", "cancelled", "timed_out", "failed"].map(runStateLabel),
        }}));
        """
    )

    assert result == {
        "success": {"buttonText": "已恢复", "outcome": "completed"},
        "answerWithoutStatus": {"buttonText": "恢复未完成", "outcome": "failed"},
        "conflict": {"buttonText": "已由其他请求处理", "outcome": "conflict"},
        "failure": {"buttonText": "恢复失败", "outcome": "failed"},
        "incomplete": {"buttonText": "恢复未完成", "outcome": "failed"},
        "interrupted": {"buttonText": "仍处于暂停", "outcome": "interrupted"},
        "cancelled": {"buttonText": "已取消", "outcome": "cancelled"},
        "timedOut": {"buttonText": "已超时", "outcome": "timed_out"},
        "failedWithAnswer": {"buttonText": "恢复失败", "outcome": "failed"},
        "current": True,
        "stale": False,
        "answerStates": [
            "completed",
            "failed",
            "cancelled",
            "timed_out",
            "budget_exhausted",
        ],
        "displayAnswer": [True, True, False, False, False, False],
        "states": [
            "正在执行",
            "正在生成待核验草稿",
            "正在核验",
            "正在停止",
            "已完成",
            "已取消",
            "已超时",
            "失败",
        ],
    }


def test_resume_http_conflict_reaches_error_callback_with_status() -> None:
    sse_url = (ROOT / "frontend" / "sse.js").as_uri()
    result = _node(
        f"""
        const {{ streamRunResume }} = await import({json.dumps(sse_url)});
        let request = null;
        globalThis.fetch = async (url, options) => {{
          request = {{ url, method: options.method }};
          return new Response(
            JSON.stringify({{ error_code: "CONVERSATION_RUN_CONFLICT", message: "conflict" }}),
            {{ status: 409, headers: {{ "Content-Type": "application/json" }} }},
          );
        }};
        const error = await new Promise((resolve, reject) => {{
          streamRunResume("run/a", {{
            onEvent: () => reject(new Error("unexpected event")),
            onError: (value) => resolve({{ message: value.message, status: value.status }}),
            onDone: () => reject(new Error("unexpected done")),
          }});
        }});
        console.log(JSON.stringify({{ request, error }}));
        """
    )

    assert result == {
        "request": {"url": "/api/v2/copilot/runs/run%2Fa/resume", "method": "POST"},
        "error": {"message": "conflict", "status": 409},
    }


def test_resume_success_delivers_answer_before_done() -> None:
    sse_url = (ROOT / "frontend" / "sse.js").as_uri()
    result = _node(
        f"""
        const {{ streamRunResume }} = await import({json.dumps(sse_url)});
        globalThis.fetch = async () => new Response(
          'event: run_resumed\\ndata: {{"run_id":"run-1"}}\\n\\n'
          + 'event: answer\\ndata: {{"text":"done"}}\\n\\n',
          {{ status: 200, headers: {{ "Content-Type": "text/event-stream" }} }},
        );
        const events = [];
        await new Promise((resolve, reject) => {{
          streamRunResume("run-1", {{
            onEvent: (event) => events.push(event.event),
            onError: reject,
            onDone: resolve,
          }});
        }});
        console.log(JSON.stringify({{ events }}));
        """
    )

    assert result == {"events": ["run_resumed", "answer"]}


def test_sse_parser_handles_utf8_boundaries_crlf_and_heartbeats() -> None:
    sse_url = (ROOT / "frontend" / "sse.js").as_uri()
    result = _node(
        f"""
        const {{ streamChat }} = await import({json.dumps(sse_url)});
        const encoded = new TextEncoder().encode(
          ': ping\\r\\n\\r\\nevent: draft_delta\\r\\ndata: {{"text":"中文"}}\\r\\n\\r\\n'
        );
        const chunks = [encoded.slice(0, 47), encoded.slice(47, 48), encoded.slice(48)];
        globalThis.fetch = async () => new Response(new ReadableStream({{
          start(controller) {{
            for (const chunk of chunks) controller.enqueue(chunk);
            controller.close();
          }}
        }}), {{ status: 200, headers: {{ "Content-Type": "text/event-stream" }} }});
        const events = [];
        await new Promise((resolve, reject) => {{
          streamChat({{ message: "q" }}, {{ onEvent: (event) => events.push(event), onError: reject, onDone: resolve }});
        }});
        console.log(JSON.stringify({{ events }}));
        """
    )

    assert result == {"events": [{"event": "draft_delta", "data": {"text": "中文"}}]}


def test_cancel_client_posts_to_owner_scoped_run_endpoint() -> None:
    api_url = (ROOT / "frontend" / "api.js").as_uri()
    result = _node(
        f"""
        const {{ copilot }} = await import({json.dumps(api_url)});
        let request = null;
        globalThis.fetch = async (url, options) => {{
          request = {{ url, method: options.method, credentials: options.credentials }};
          return new Response(JSON.stringify({{ status: "cancel_requested" }}), {{
            status: 200,
            headers: {{ "Content-Type": "application/json" }},
          }});
        }};
        const response = await copilot.cancelRun("run/a");
        console.log(JSON.stringify({{ request, response }}));
        """
    )

    assert result == {
        "request": {
            "url": "/api/v2/copilot/runs/run%2Fa/cancel",
            "method": "POST",
            "credentials": "include",
        },
        "response": {"status": "cancel_requested"},
    }
