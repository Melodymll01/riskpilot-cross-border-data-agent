"""Case Assessment Agent 的统一工具安全策略。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agent_tools.registry import RegisteredTool
    from domain.agent_workflow import AgentRuntimeContext

_RESERVED_SCOPE_KEYS = {
    "actor_id",
    "actor_role",
    "case_id",
    "run_id",
    "workspace_id",
}
_FORBIDDEN_OUTPUT_KEYS = {
    "access_token",
    "answer",
    "api_key",
    "authorization",
    "chain_of_thought",
    "content",
    "cookie",
    "credential",
    "document",
    "document_text",
    "password",
    "prompt",
    "quote",
    "raw_completion",
    "raw_prompt",
    "reasoning",
    "refresh_token",
    "secret",
    "text",
    "thought",
}


@dataclass(frozen=True)
class AgentToolPolicy:
    """默认拒绝高权限工具，只显式允许有限可逆写工具。"""

    allowed_reversible_tools: frozenset[str] = field(
        default_factory=lambda: frozenset({"extract_fact_candidates"})
    )

    def validate_registration(self, tool: RegisteredTool) -> None:
        if tool.side_effect_level == "forbidden_for_agent":
            raise ValueError(f"工具 {tool.name!r} 被标记为 forbidden_for_agent，不能注册")
        if tool.side_effect_level == "privileged_write":
            raise ValueError(f"工具 {tool.name!r} 是 privileged_write，不能暴露给 Agent")
        if tool.side_effect_level == "reversible_write":
            if tool.name not in self.allowed_reversible_tools:
                raise ValueError(f"可逆写工具 {tool.name!r} 未进入 Agent allowlist")
            if tool.max_retries != 0:
                raise ValueError(f"可逆写工具 {tool.name!r} 禁止自动重试")

    def authorize(
        self,
        tool: RegisteredTool,
        context: AgentRuntimeContext,
    ) -> None:
        self.validate_registration(tool)
        if context.actor_role not in tool.required_roles:
            raise PermissionError(f"角色 {context.actor_role!r} 无权调用工具 {tool.name!r}")
        if context.workflow_stage not in tool.allowed_stages:
            raise PermissionError(
                f"工具 {tool.name!r} 不允许在阶段 {context.workflow_stage!r} 调用"
            )

    def validate_arguments(self, arguments: dict[str, Any]) -> None:
        forbidden = _find_keys(arguments, _RESERVED_SCOPE_KEYS)
        if forbidden:
            raise PermissionError("Agent 工具参数不得声明运行时 scope")

    def validate_output(self, payload: dict[str, Any]) -> None:
        forbidden = _find_keys(payload, _FORBIDDEN_OUTPUT_KEYS)
        if forbidden:
            raise ValueError("Agent 工具输出包含禁止进入状态的敏感字段")
        if _contains_binary_or_oversized_text(payload):
            raise ValueError("Agent 工具输出包含不可审计的大文本或二进制内容")


def _find_keys(value: Any, forbidden: set[str]) -> set[str]:
    if isinstance(value, dict):
        found = {str(key).lower() for key in value if str(key).lower() in forbidden}
        for child in value.values():
            found.update(_find_keys(child, forbidden))
        return found
    if isinstance(value, list):
        list_found: set[str] = set()
        for child in value:
            list_found.update(_find_keys(child, forbidden))
        return list_found
    return set()


def _contains_binary_or_oversized_text(value: Any) -> bool:
    if isinstance(value, bytes):
        return True
    if isinstance(value, str):
        return len(value) > 500 or "\x00" in value
    if isinstance(value, dict):
        return any(_contains_binary_or_oversized_text(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_binary_or_oversized_text(child) for child in value)
    return False
