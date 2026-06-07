"""LLM 决策的 JSON 协议与解析。

为了让 ChatPort 保持 ``chat(messages, ...) -> str`` 的纯文本签名（与所有现有
适配器/Fake 兼容），Agent 强制 LLM 以下面的 JSON 形式返回决策：

```json
{
  "thought": "...",
  "action": "tool" | "ask_user" | "final_answer",
  "tool_name": "search_law",          // action=tool 时
  "tool_args": {"query": "...", ...}, // action=tool 时
  "question": "...",                  // action=ask_user 时
  "missing_facts": ["a", "b"],        // 可选
  "answer": "...",                    // action=final_answer 时
  "citations": [{...}]                // 可选
}
```

解析失败抛 ``AgentDecisionParseError``，由 Agent 主循环捕获并产出
``AgentEvent.decision_parse_error``，**不会** crash 整个会话。

容错点：
- 支持 ``\u200b```json ... ``` `` 围栏；先抠最外层 ``{...}``
- ``action`` 拼写宽松：``"tools"`` / ``"answer"`` / ``"ask"`` 都映射到标准值
- 缺关键字段（如 action=tool 但无 tool_name）按规则降级或抛错
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

ActionType = Literal["tool", "ask_user", "final_answer"]


class AgentDecisionParseError(ValueError):
    """LLM 返回的字符串无法解析成有效决策。"""


_ACTION_ALIASES: dict[str, ActionType] = {
    "tool": "tool",
    "tools": "tool",
    "tool_call": "tool",
    "call_tool": "tool",
    "ask": "ask_user",
    "ask_user": "ask_user",
    "question": "ask_user",
    "answer": "final_answer",
    "final": "final_answer",
    "final_answer": "final_answer",
    "done": "final_answer",
}

# 抠最外层 {...}（包含跨行、嵌套对象）。非贪婪不够用，这里抠从第一个 { 到最后一个 }。
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class AgentDecision:
    """一次 LLM 决策的结构化结果。"""

    thought: str
    action: ActionType
    tool_name: str | None = None
    tool_args: dict[str, Any] = field(default_factory=dict)
    question: str | None = None
    missing_facts: list[str] = field(default_factory=list)
    final_text: str | None = None
    citations: list[dict[str, Any]] = field(default_factory=list)


def parse_decision(raw: str) -> AgentDecision:
    """从 LLM 文本响应中解析出 ``AgentDecision``。

    流程：剥代码围栏 → 抠最外层 JSON 对象 → ``json.loads`` → 标准化字段。
    任何环节失败抛 ``AgentDecisionParseError``。
    """
    if not raw or not raw.strip():
        msg = "empty LLM response"
        raise AgentDecisionParseError(msg)

    body = _strip_fences(raw)
    match = _JSON_OBJECT_RE.search(body)
    if match is None:
        msg = f"no JSON object found in response: {raw!r}"
        raise AgentDecisionParseError(msg)

    snippet = match.group(0)
    try:
        data = json.loads(snippet)
    except json.JSONDecodeError as exc:
        # Step 026d：真实 LLM（如 GLM-5）偶尔在字符串值内塞未转义的 ASCII 双引号
        # （例：``第三章"个人信息跨境提供的规则"的相关条款``），令标准 json.loads 失败。
        # 先做一次启发式修复再重解析；仍失败才抛错，保持降级不 crash。
        try:
            data = json.loads(_repair_unescaped_quotes(snippet))
        except json.JSONDecodeError:
            msg = f"invalid JSON: {exc}; raw={raw!r}"
            raise AgentDecisionParseError(msg) from exc

    # 正则 \{.*\} 已经保证抠出的是花括号片段，json.loads 解出来必是 dict。
    # 此处不再加 isinstance 防御层 —— 若未来正则改动，由测试兜底。

    thought = str(data.get("thought") or "").strip()
    action_raw = str(data.get("action") or "").strip().lower()
    action = _ACTION_ALIASES.get(action_raw)
    if action is None:
        msg = f"unknown action {action_raw!r}; expected one of tool/ask_user/final_answer"
        raise AgentDecisionParseError(msg)

    tool_name = data.get("tool_name")
    # 注意：不能用 ``data.get("tool_args") or {}``，否则 ``tool_args: []`` 会被静默替换
    raw_args = data.get("tool_args")
    tool_args: Any = {} if raw_args is None else raw_args
    if action == "tool":
        if not isinstance(tool_name, str) or not tool_name:
            msg = "action=tool requires non-empty tool_name"
            raise AgentDecisionParseError(msg)
        if not isinstance(tool_args, dict):
            msg = f"tool_args must be object, got {type(tool_args).__name__}"
            raise AgentDecisionParseError(msg)

    question = data.get("question")
    if action == "ask_user" and (not isinstance(question, str) or not question.strip()):
        msg = "action=ask_user requires non-empty question"
        raise AgentDecisionParseError(msg)

    final_text = data.get("answer") or data.get("final_text")
    if action == "final_answer" and (
        not isinstance(final_text, str) or not final_text.strip()
    ):
        msg = "action=final_answer requires non-empty answer"
        raise AgentDecisionParseError(msg)

    missing = data.get("missing_facts") or []
    if not isinstance(missing, list):
        missing = []

    citations = data.get("citations") or []
    if not isinstance(citations, list):
        citations = []

    return AgentDecision(
        thought=thought,
        action=action,
        tool_name=tool_name if action == "tool" else None,
        tool_args=dict(tool_args) if action == "tool" else {},
        question=question if action == "ask_user" else None,
        missing_facts=[str(x) for x in missing],
        final_text=final_text if action == "final_answer" else None,
        citations=[c for c in citations if isinstance(c, dict)],
    )


def _strip_fences(text: str) -> str:
    """剥掉 ```...``` 围栏，保留代码内容。"""
    match = _FENCE_RE.search(text)
    if match is not None:
        return match.group(1)
    return text


def _repair_unescaped_quotes(snippet: str) -> str:
    """修复 JSON 字符串值内部未转义的 ASCII 双引号（LLM 常见越界输出）。

    启发式状态机：逐字符扫描。处于字符串内部时遇到 ``"``，向后跳过空白看下一个
    非空白字符：若是结构分隔符 ``, : } ]`` 或已到结尾，则视为字符串真正的结束引号；
    否则视为字面引号并转义成 ``\\"``。转义序列 ``\\x`` 原样保留。

    已知局限：字符串文本内若出现 ``"`` 紧跟 ``,``（如 ``他说"好",``）会被误判为结束，
    但法规问答场景极少触发；仅作为 json.loads 失败后的兜底，不影响合法 JSON。
    """
    out: list[str] = []
    in_string = False
    i = 0
    n = len(snippet)
    while i < n:
        ch = snippet[i]
        if not in_string:
            out.append(ch)
            if ch == '"':
                in_string = True
            i += 1
            continue
        # —— 字符串内部 ——
        if ch == "\\":
            out.append(ch)
            if i + 1 < n:
                out.append(snippet[i + 1])
                i += 2
            else:
                i += 1
            continue
        if ch == '"':
            j = i + 1
            while j < n and snippet[j] in " \t\r\n":
                j += 1
            if j >= n or snippet[j] in ",:}]":
                out.append(ch)  # 真正的结束引号
                in_string = False
            else:
                out.append('\\"')  # 字面引号 → 转义
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)
