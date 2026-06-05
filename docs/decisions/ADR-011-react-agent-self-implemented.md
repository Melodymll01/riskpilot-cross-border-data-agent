# ADR-011: ReAct 主循环自实现 + LLM JSON 决策协议

- 状态: accepted
- 日期: 2026-06-05（追溯 Step 009 落地决策）
- 关联：[ADR-001: 不引入 LangChain](ADR-001-no-langchain.md)（本 ADR 是其延伸落地）

## 背景

Step 008 完成 DI 后需要落地 Agent。可选路径：

1. **OpenAI tools API**：模型直接输出 `tool_calls` 数组，SDK 解析
2. **LangChain / LangGraph**：现成 ReAct 实现
3. **自实现 ReAct + JSON 协议**：LLM 输出文本，自己解析 `{action, tool, args}`

ADR-001 已决定不上 LangChain。剩下 OpenAI tools API vs 自实现 JSON 协议。

## 决策

**自实现 ReAct 主循环**，LLM 通过 JSON 文本输出决策（不依赖 SDK 的 tools 字段）：

```python
# app/agent/copilot.py 简化
def run(self, user_message: str) -> Iterator[AgentEvent]:
    for step in range(max_steps):
        # 1. 让 LLM 决策（纯文本 chat，输出 JSON 字符串）
        raw = self._chat.chat(self._build_prompts(history, user_message))
        decision = parse_json(raw)  # {action: tool|answer|ask_user, ...}

        # 2. 软失败兜底：JSON 解析失败 / 未知 tool / args 校验失败
        if decision is None:
            yield AgentEvent.error("LLM 输出不可解析")
            return

        # 3. 分发
        if decision.action == "tool":
            yield AgentEvent.tool_call(...)
            result = self._tools.invoke(decision.tool, decision.args)
            yield AgentEvent.tool_result(...)
            history.append(...)
        elif decision.action == "answer":
            yield AgentEvent.answer(decision.text)
            yield AgentEvent.citations(...)
            return
        elif decision.action == "ask_user":
            yield AgentEvent.ask_user(decision.question)
            return
    # max_steps 兜底
    yield AgentEvent.answer("（已达最大推理步数...）")
```

### 工具声明（`ToolSpec`）

```python
ToolSpec(
    name="search_law",
    description="检索合规法规",
    arg_schema={"query": str, "top_k": int},
    invoke=lambda args, ctx: retriever.search(...),
)
```

工具在 `ComplianceCopilotAgent.__init__` 时注册到 `ToolRegistry`，prompt 自动把工具列表序列化进系统消息。

### `ChatPort` 不引入"tools"概念

```python
class ChatPort(Protocol):
    def chat(self, messages: list[dict], *, temperature: float = 0.0) -> str: ...
```

签名永远是 `messages → str`，不区分模型是否支持 tool calling。

## 后果

**正面**：
- **跨模型兼容**：智谱 GLM-4-Flash / OpenAI / Ollama / 任意 OpenAI 兼容端点都能跑，不依赖某家的 tools 字段格式
- **可解释**：LLM 输出整条 JSON 是 prompt 评估的素材，调 prompt 可见即所得
- **容错可控**：JSON 解析失败 / 字段缺失 / 未知 tool 都走 `yield AgentEvent.error(...)` 软失败，不抛崩 SSE 流
- **测试简单**：`FakeChat(responses=["{action:tool,...}", "{action:answer,...}"])` 直接驱动多轮，整个 agent 主循环可在 100ms 内跑完
- **Prompt 工程透明**：所有决策约定写在系统消息里，不藏在 SDK 里

**负面**：
- LLM 偶尔输出非 JSON 自然语言时需要软失败（Step 009 已落地）
- 比 OpenAI tools API 多一道 JSON 解析步骤（CPU 开销可忽略）
- Token 略多（系统消息要描述协议）

## 协议规范

```json
{
  "action": "tool" | "answer" | "ask_user",
  "thought": "<可选 ReAct 思考>",
  "tool": "<action=tool 时必填>",
  "args": {"k": "v"},
  "text": "<action=answer 时必填>",
  "citations": [...],
  "question": "<action=ask_user 时必填>"
}
```

未知字段忽略；缺失必填字段触发软失败 + `AgentEvent.error`。

## 事件流（9 类）

| 事件 | 触发 |
|---|---|
| `task_created` | use case yield 第一帧前 |
| `thought` | LLM 决策的 thought 字段 |
| `tool_call` | action=tool 解析后 |
| `tool_result` | 工具执行完毕 |
| `answer` | action=answer |
| `citations` | answer 之后 |
| `ask_user` | action=ask_user |
| `error` | 软失败 |
| `keepalive` | SSE 保活 |

SSE 帧格式见 `api/v2/sse.py`。

## 备选方案

| 方案 | 否决理由 |
|---|---|
| OpenAI tools API（function calling）| 锁死 OpenAI 兼容；Ollama / 国产模型支持度参差 |
| LangChain AgentExecutor | ADR-001：黑盒 |
| LangGraph | 同上；可作对照实验留独立分支 |
| 让 LLM 直接输出自然语言再 NLU 解析 | 不可靠；JSON 是模型最稳的结构化输出 |

## 实证

- 4 个生产工具（`search_law` / `search_user_docs` / `web_search` / `evidence_judge`）全跑通
- agent 测试 ~30 用例覆盖：正常分发 / max_steps / JSON 失败 / 未知 tool / args 校验失败 / owner_id 注入
- 与 Step 015 profile mode 短路并行：mode=profile 时跳过 agent 主循环直接调 `RiskProfilePort`，验证主循环可旁路

## 关联

- [ADR-001: 不引入 LangChain](ADR-001-no-langchain.md)
- [ADR-005: 对话式 Copilot 形态](ADR-005-conversational-copilot-form.md)
- 实现：`app/agent/{events,decision,tools,copilot}.py`、`app/use_cases/run_copilot.py`
- 过程：[Step 009](../process/step_009_pr5b_agent_layer.md)、[Step 015](../process/step_015_profile_mode_wiring.md)
