"""LangChain model transport through the user's authenticated Trae CLI.

The CLI is an inference subprocess, never the executor of application tools.
Its JSONL protocol currently delivers complete messages, not token deltas.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
import uuid
from collections.abc import Iterator, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

from infra.chat.runtime import current_model_control

_DISABLED_FEATURES = (
    "shell_tool",
    "unified_exec",
    "code_mode_host",
    "apply_patch_freeform",
    "multi_agent",
    "multi_agent_v2",
    "tool_search",
    "apps",
    "plugins",
    "memories",
    "skill_search",
    "hooks",
    "image_generation",
    "browser_use",
    "computer_use",
)


class TraexProcessError(RuntimeError):
    """The CLI process failed; deliberately excludes prompt and stderr content."""


class TraexInternalToolError(RuntimeError):
    """The inference subprocess attempted a tool owned by the host CLI."""


class TraexTurnError(RuntimeError):
    """The routed model turn did not complete."""


class TraexChatModel(BaseChatModel):
    model_name: str = "gpt-5.4"
    cli_path: str = "/Users/bytedance/.local/bin/traex"
    timeout_seconds: float = Field(default=90.0, gt=0)
    temperature: float = 0.1
    max_tokens: int | None = Field(default=4096, gt=0)
    accepts_runtime_control: bool = True
    supports_incremental_text: bool = False

    @property
    def _llm_type(self) -> str:
        return "trae-traex"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"cli": "traex", "provider": "trae", "model": self.model_name}

    def _get_ls_params(self, stop=None, **kwargs):
        params = super()._get_ls_params(stop=stop, **kwargs)
        params.update(ls_provider="trae", ls_model_name=self.model_name)
        return params

    def bind_tools(self, tools: Sequence[Any], *, tool_choice: Any = None, **kwargs: Any) -> Any:
        return self.bind(
            traex_tools=[convert_to_openai_tool(tool) for tool in tools],
            tool_choice=tool_choice,
            **kwargs,
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        del run_manager
        if stop:
            raise ValueError("Traex exec does not expose stop-sequence control")
        tools = kwargs.get("traex_tools", [])
        requested_temperature = kwargs.get("temperature", self.temperature)
        requested_max_tokens = kwargs.get("max_completion_tokens", self.max_tokens)
        schema = _envelope_schema(tools)
        serialized = []
        for message in messages:
            item: dict[str, Any] = {"role": message.type, "content": message.content}
            if isinstance(message, AIMessage) and message.tool_calls:
                item["tool_calls"] = message.tool_calls
            if isinstance(message, ToolMessage):
                item["tool_call_id"] = message.tool_call_id
            serialized.append(item)
        payload = json.dumps(
            {
                "transport_instructions": (
                    "You are an inference backend. Do not use CLI tools, files, skills, memory, "
                    "or external services. Application tools listed below are declarations only: "
                    "propose tool_calls and the application will execute them. Follow the supplied "
                    "messages. Return the transport envelope with content and tool_calls. "
                    "For a final answer tool_calls must be empty. If messages request JSON, "
                    "content must contain that JSON encoded as a string. Never invent tool results."
                ),
                "messages": serialized,
                "available_application_tools": tools,
                "tool_choice": kwargs.get("tool_choice"),
                "response_format": kwargs.get("response_format"),
                "requested_output_token_limit": requested_max_tokens,
            },
            ensure_ascii=False,
        )
        if len(payload) > 240_000:
            raise ValueError("Traex request exceeds the configured transport input bound")
        envelope, usage = self._invoke_cli(payload, schema, kwargs.get("runtime_control"))
        Draft202012Validator(schema).validate(envelope)
        declarations = {tool["function"]["name"]: tool["function"] for tool in tools}
        calls = []
        for call in envelope["tool_calls"]:
            name = call["name"]
            if name not in declarations:
                raise ValueError("Traex returned an undeclared application tool")
            arguments = json.loads(call["arguments_json"])
            Draft202012Validator(declarations[name]["parameters"]).validate(arguments)
            calls.append({"id": "call_" + uuid.uuid4().hex, "name": name, "args": arguments})
        content = envelope["content"]
        if (
            (kwargs.get("response_format") or {}).get("type") == "json_object"
            and not calls
            and not isinstance(json.loads(content), dict)
        ):
            raise ValueError("Traex structured response must be a JSON object")
        message = AIMessage(
            content=content,
            tool_calls=calls,
            usage_metadata=usage,
            response_metadata={
                **self._identifying_params,
                "transport_mode": "complete_message_jsonl",
                "backend_call_count": None,
                "requested_model": self.model_name,
                "resolved_model": None,
                "requested_temperature": requested_temperature,
                "requested_output_token_limit": requested_max_tokens,
                "sampling_parameters_enforced": False,
            },
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        # One completion chunk is truthful; never synthesize a token stream.
        message = (
            self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            .generations[0]
            .message
        )
        assert isinstance(message, AIMessage)
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content=message.content,
                tool_calls=message.tool_calls,
                usage_metadata=message.usage_metadata,
                response_metadata=message.response_metadata,
            )
        )

    def _invoke_cli(
        self,
        payload: str,
        schema: dict[str, Any],
        runtime_control: Any = None,
    ) -> tuple[dict[str, Any], dict[str, int] | None]:
        control = runtime_control or current_model_control()
        with tempfile.TemporaryDirectory(prefix="riskpilot-inference-") as directory:
            schema_path = Path(directory) / "response.schema.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            command = [self.cli_path, "-a", "never"]
            for feature in _DISABLED_FEATURES:
                command.extend(["--disable", feature])
            command.extend(
                [
                    "exec",
                    "--skip-git-repo-check",
                    "--ephemeral",
                    "--json",
                    "--color",
                    "never",
                    "--sandbox",
                    "read-only",
                    "--disallowed-tool",
                    "*",
                    "-c",
                    'web_search="disabled"',
                    "--model",
                    self.model_name,
                    "--output-schema",
                    str(schema_path),
                    "-",
                ]
            )
            env = os.environ.copy()
            env.update(LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false")
            if control is not None:
                control.check()
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=directory,
                env=env,
                start_new_session=True,
            )
            deadline = time.monotonic() + self.timeout_seconds
            input_data: bytes | None = payload.encode("utf-8")
            try:
                while True:
                    if control is not None:
                        control.check()
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Traex inference timeout")
                    try:
                        stdout, _stderr = process.communicate(input=input_data, timeout=0.05)
                        break
                    except subprocess.TimeoutExpired:
                        input_data = None
                if control is not None:
                    control.check()
                if process.returncode != 0:
                    raise TraexProcessError(f"Traex inference failed (exit {process.returncode})")
                if len(stdout) > 4 * 1024 * 1024:
                    raise ValueError("Traex response exceeds transport output bound")
                return _decode_events(stdout.decode("utf-8"))
            finally:
                _stop_process(process)


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=2)


def _envelope_schema(tools: list[dict[str, Any]]) -> dict[str, Any]:
    calls: dict[str, Any] = {
        "type": "array",
        "maxItems": 4,
        "items": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "enum": [tool["function"]["name"] for tool in tools] or ["unused"],
                },
                "arguments_json": {"type": "string"},
            },
            "required": ["name", "arguments_json"],
            "additionalProperties": False,
        },
    }
    if not tools:
        calls["maxItems"] = 0
    return {
        "type": "object",
        "properties": {
            "content": {"type": "string"},
            "tool_calls": calls,
        },
        "required": ["content", "tool_calls"],
        "additionalProperties": False,
    }


def _decode_events(output: str) -> tuple[dict[str, Any], dict[str, int] | None]:
    final: str | None = None
    usage: dict[str, int] | None = None
    completed = False
    for line in output.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("type") == "turn.failed":
            raise TraexTurnError("Traex model turn failed")
        item = event.get("item", {})
        if item.get("type") in {"command_execution", "mcp_tool_call", "web_search", "tool_call"}:
            raise TraexInternalToolError("Traex attempted a forbidden internal tool")
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            final = item["text"]
        if event.get("type") == "turn.completed":
            completed = True
            raw = event.get("usage") or {}
            if all(
                isinstance(raw.get(key), int) and raw[key] >= 0
                for key in ("input_tokens", "output_tokens")
            ):
                usage = {
                    "input_tokens": raw["input_tokens"],
                    "output_tokens": raw["output_tokens"],
                    "total_tokens": raw["input_tokens"] + raw["output_tokens"],
                }
    if not completed or final is None:
        raise TraexTurnError("Traex did not produce a completed model response")
    result = json.loads(final)
    if not isinstance(result, dict):
        raise ValueError("Traex response envelope must be an object")
    return result, usage
