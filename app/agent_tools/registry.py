"""Pydantic Typed Tool Registry。"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from app.agent_tools.policy import AgentToolPolicy
from domain.agent_workflow import (
    AgentRuntimeContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolSideEffectLevel,
)
from observability_context import observability_context

if TYPE_CHECKING:
    from domain.ports import MetricsPort, TracePort


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
    def __init__(
        self,
        *,
        policy: AgentToolPolicy | None = None,
        trace: TracePort | None = None,
        metrics: MetricsPort | None = None,
        model_name: str = "unconfigured",
        input_cost_per_1m_tokens: float = 0.0,
        output_cost_per_1m_tokens: float = 0.0,
    ) -> None:
        if input_cost_per_1m_tokens < 0 or output_cost_per_1m_tokens < 0:
            raise ValueError("LLM token price 不能小于 0")
        self._tools: dict[str, RegisteredTool[Any, Any]] = {}
        self._policy = policy or AgentToolPolicy()
        self._trace = trace
        self._metrics = metrics
        self._model_name = model_name
        self._input_cost_per_1m_tokens = input_cost_per_1m_tokens
        self._output_cost_per_1m_tokens = output_cost_per_1m_tokens

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
        span_manager = (
            self._trace.span(
                f"riskpilot.tool.{tool.name}",
                run_type="tool",
                metadata={
                    "run_id": context.run_id,
                    "workspace_id": context.workspace_id,
                    "case_id": context.case_id,
                    "stage": context.workflow_stage,
                    "tool": tool.name,
                },
            )
            if self._trace is not None
            else nullcontext(None)
        )
        try:
            with (
                observability_context(
                    run_id=context.run_id,
                    workspace_id=context.workspace_id,
                    case_id=context.case_id,
                    node=context.workflow_stage,
                    tool=tool.name,
                ),
                span_manager as span,
            ):
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
                if span is not None:
                    span.add_metadata(
                        {
                            "status": "success",
                            "duration_ms": (time.perf_counter() - started) * 1000,
                            "retry_count": retry_count,
                        }
                    )
        except Exception:
            if self._metrics is not None:
                self._metrics.observe_tool(
                    tool=tool.name,
                    status="failed",
                    duration_seconds=time.perf_counter() - started,
                    retry_count=retry_count,
                )
            raise
        duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        try:
            validated_output = tool.output_model.model_validate(output)
        except ValidationError as exc:
            raise ValueError(f"工具 {tool_name!r} 返回结构非法") from exc
        payload = validated_output.model_dump(mode="json")
        token_usage_value = getattr(validated_output, "token_usage", 0)
        input_tokens_value = getattr(validated_output, "input_tokens", 0)
        output_tokens_value = getattr(validated_output, "output_tokens", 0)
        token_usage = (
            token_usage_value
            if isinstance(token_usage_value, int) and token_usage_value >= 0
            else 0
        )
        input_tokens = (
            input_tokens_value
            if isinstance(input_tokens_value, int) and input_tokens_value >= 0
            else 0
        )
        output_tokens = (
            output_tokens_value
            if isinstance(output_tokens_value, int) and output_tokens_value >= 0
            else 0
        )
        self._policy.validate_output(payload)
        if self._metrics is not None:
            self._metrics.observe_tool(
                tool=tool.name,
                status="success",
                duration_seconds=duration_ms / 1000,
                retry_count=retry_count,
            )
            if token_usage:
                self._metrics.record_llm_usage(
                    operation=tool.name,
                    model=self._model_name,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost=_estimated_cost(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        input_cost_per_1m_tokens=self._input_cost_per_1m_tokens,
                        output_cost_per_1m_tokens=self._output_cost_per_1m_tokens,
                    ),
                )
        return ToolExecutionResult(
            tool_name=tool.name,
            arguments=_sanitize_arguments(validated.model_dump(mode="json")),
            output=payload,
            result_summary=_summary(payload),
            duration_ms=duration_ms,
            retry_count=retry_count,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
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


def _estimated_cost(
    *,
    input_tokens: int,
    output_tokens: int,
    input_cost_per_1m_tokens: float,
    output_cost_per_1m_tokens: float,
) -> float:
    return (
        input_tokens * input_cost_per_1m_tokens + output_tokens * output_cost_per_1m_tokens
    ) / 1_000_000
