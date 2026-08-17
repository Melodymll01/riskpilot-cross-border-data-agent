"""Pydantic Typed Tool Registry。"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from app.agent_tools.policy import AgentToolPolicy
from domain.agent_workflow import (
    AgentRuntimeContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolSideEffectLevel,
)


@dataclass(frozen=True)
class RegisteredTool[InputModel: BaseModel, OutputModel: BaseModel]:
    name: str
    description: str
    input_model: type[InputModel]
    output_model: type[OutputModel]
    executor: Callable[[InputModel, AgentRuntimeContext], OutputModel]
    timeout_seconds: float
    max_retries: int
    required_roles: frozenset[str]
    allowed_stages: frozenset[str]
    side_effect_level: ToolSideEffectLevel

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema_name=self.input_model.__name__,
            output_schema_name=self.output_model.__name__,
            timeout_seconds=self.timeout_seconds,
            max_retries=self.max_retries,
            required_roles=sorted(self.required_roles),
            allowed_stages=sorted(self.allowed_stages),
            side_effect_level=self.side_effect_level,
        )


class TypedToolRegistry:
    def __init__(self, *, policy: AgentToolPolicy | None = None) -> None:
        self._tools: dict[str, RegisteredTool[Any, Any]] = {}
        self._policy = policy or AgentToolPolicy()

    def register(self, tool: RegisteredTool[Any, Any]) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具 {tool.name!r} 已注册")
        if not tool.required_roles or not tool.allowed_stages:
            raise ValueError("工具必须声明 required_roles 和 allowed_stages")
        self._policy.validate_registration(tool)
        self._tools[tool.name] = tool

    def definitions(self) -> list[ToolDefinition]:
        return [self._tools[name].definition() for name in sorted(self._tools)]

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        context: AgentRuntimeContext,
    ) -> ToolExecutionResult:
        tool = self._tools.get(tool_name)
        if tool is None:
            raise ValueError(f"工具 {tool_name!r} 未注册")
        self._policy.authorize(tool, context)
        self._policy.validate_arguments(arguments)
        try:
            validated = tool.input_model.model_validate(arguments)
        except ValidationError as exc:
            raise ValueError(f"工具 {tool_name!r} 参数结构非法") from exc
        started = time.perf_counter()
        retry_count = 0
        while True:
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(tool.executor, validated, context)
            try:
                output = future.result(timeout=tool.timeout_seconds)
                break
            except FutureTimeoutError as exc:
                future.cancel()
                error: Exception = TimeoutError(
                    f"工具 {tool.name!r} 超过 {tool.timeout_seconds} 秒"
                )
                error.__cause__ = exc
            except Exception as exc:
                error = exc
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
            if retry_count >= tool.max_retries:
                raise error
            retry_count += 1
        duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        try:
            validated_output = tool.output_model.model_validate(output)
        except ValidationError as exc:
            raise ValueError(f"工具 {tool_name!r} 返回结构非法") from exc
        payload = validated_output.model_dump(mode="json")
        token_usage_value = getattr(validated_output, "token_usage", 0)
        token_usage = (
            token_usage_value
            if isinstance(token_usage_value, int) and token_usage_value >= 0
            else 0
        )
        self._policy.validate_output(payload)
        return ToolExecutionResult(
            tool_name=tool.name,
            arguments=_sanitize_arguments(validated.model_dump(mode="json")),
            output=payload,
            result_summary=_summary(payload),
            duration_ms=duration_ms,
            retry_count=retry_count,
            token_usage=token_usage,
        )


def _summary(payload: dict[str, Any]) -> str:
    keys = ", ".join(sorted(payload))
    return f"返回字段: {keys}" if keys else "工具执行完成"


def _sanitize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in arguments.items():
        if key == "query" and isinstance(value, str):
            sanitized["query"] = "[redacted]"
            sanitized["query_length"] = len(value)
        elif isinstance(value, str) and (len(value) > 200 or "\n" in value):
            sanitized[key] = "[redacted]"
        else:
            sanitized[key] = value
    return sanitized
