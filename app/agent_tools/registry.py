"""Pydantic Typed Tool Registry。"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

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
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool[Any, Any]] = {}

    def register(self, tool: RegisteredTool[Any, Any]) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具 {tool.name!r} 已注册")
        if not tool.required_roles or not tool.allowed_stages:
            raise ValueError("工具必须声明 required_roles 和 allowed_stages")
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
        if context.actor_role not in tool.required_roles:
            raise PermissionError(f"角色 {context.actor_role!r} 无权调用工具 {tool_name!r}")
        if context.workflow_stage not in tool.allowed_stages:
            raise PermissionError(
                f"工具 {tool_name!r} 不允许在阶段 {context.workflow_stage!r} 调用"
            )
        validated = tool.input_model.model_validate(arguments)
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
        validated_output = tool.output_model.model_validate(output)
        payload = validated_output.model_dump(mode="json")
        token_usage_value = getattr(validated_output, "token_usage", 0)
        token_usage = (
            token_usage_value
            if isinstance(token_usage_value, int) and token_usage_value >= 0
            else 0
        )
        return ToolExecutionResult(
            tool_name=tool.name,
            arguments=validated.model_dump(mode="json"),
            output=payload,
            result_summary=_summary(payload),
            duration_ms=duration_ms,
            retry_count=retry_count,
            token_usage=token_usage,
        )


def _summary(payload: dict[str, Any]) -> str:
    keys = ", ".join(sorted(payload))
    return f"返回字段: {keys}" if keys else "工具执行完成"
