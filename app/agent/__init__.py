"""Agent 子层出口。

ReAct 风格 ComplianceCopilotAgent + ToolSpec 注册表 + AgentEvent 事件流。
所有 LLM 决策都走 JSON 协议：Agent 让 LLM 输出 {"thought":..., "action":...} 形式，
解析失败优雅降级，不抛崩 Agent 主循环。
"""

from app.agent.copilot import ComplianceCopilotAgent
from app.agent.decision import (
    AgentDecision,
    AgentDecisionParseError,
    parse_decision,
)
from app.agent.events import AgentEvent, AgentEventType
from app.agent.tools import ToolSpec, register_default_tools

__all__ = [
    "AgentDecision",
    "AgentDecisionParseError",
    "AgentEvent",
    "AgentEventType",
    "ComplianceCopilotAgent",
    "ToolSpec",
    "parse_decision",
    "register_default_tools",
]
