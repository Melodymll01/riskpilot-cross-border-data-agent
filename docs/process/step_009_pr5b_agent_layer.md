# Step 009 — PR-5b Agent 子层（ComplianceCopilotAgent + 工具注册表 + RunCopilotUseCase）

## 1. 本步骤目标

Step 008 装好了"无 Agent" 的 app 骨架（4 个 use case + 8 Port 容器）。本步骤补齐 Agent 子层 —— 整个项目的"大脑"：让 LLM 能自主决策调工具 / 追问 / 给答复，并把决策过程流式产出供 API 层（Step 010）转 SSE。

- **为什么存在**：合规咨询不是"一问一答 RAG"。要回答"我把欧洲用户数据回传北京合规吗？"需要 Agent 自己判断要不要先查 PIPL、要不要看用户上传的隐私政策、要不要追问用户量/数据类型。固定流水线写死会失去这种灵活性。
- **服务于哪层**：app 层。Agent 类是 use case 的依赖；`RunCopilotUseCase` 是 API 层即将调用的入口。
- **为后续提供什么**：
  - `ComplianceCopilotAgent` —— ReAct 主循环（思考 → 工具 → 观察 → 重复 → 答复）
  - `ToolSpec` + `register_default_tools` —— 声明式工具注册，新增工具不动 Agent 核心
  - `AgentEvent` —— 流式事件协议，Step 010 直接 SSE 序列化
  - `AgentDecision` + `parse_decision` —— JSON 决策协议，让 ChatPort 保持纯文本签名
  - `RunCopilotUseCase` —— API 入口的薄壳：没 task 就建 task，附件 ID 注入 user_message
  - Step 010 (PR-6) FastAPI 路由 `POST /api/chat/run` 直接迭代 `container.run_copilot.stream(...)`
  - Step 012 (PR-7) 风险画像完成后，只需在 `register_default_tools` 加 `risk_profile / generate_checklist` 两条 `ToolSpec`，Agent 主循环零改动

## 2. 修改文件

| 路径 | 说明 |
|---|---|
| `app/agent/__init__.py` | 出口：`ComplianceCopilotAgent / ToolSpec / register_default_tools / AgentEvent / AgentEventType / AgentDecision / parse_decision / AgentDecisionParseError` |
| `app/agent/events.py` | `AgentEventType` 枚举（9 种）+ `AgentEvent` frozen dataclass + 9 个 `@classmethod` 工厂方法 |
| `app/agent/decision.py` | `AgentDecision` frozen dataclass + `parse_decision()`：剥代码围栏 → 抠 `{...}` → `json.loads` → 标准化；支持 action 别名（`tool/tools/tool_call/call_tool`、`ask/ask_user/question`、`answer/final/final_answer/done`）；解析失败抛 `AgentDecisionParseError` |
| `app/agent/tools.py` | `ToolSpec(name, description, parameters_schema, handler, timeout_s=30.0, requires_owner=True)` frozen dataclass + `register_default_tools(container)` 返回 4 个工具：`search_law / search_user_docs / web_search / evidence_judge`；后两个 `risk_profile / generate_checklist` 等 PR-7 完成 RiskProfiler 后再加 |
| `app/agent/copilot.py` | `ComplianceCopilotAgent.run(owner_id, task_id, user_message) -> Iterator[AgentEvent]`：ReAct 主循环（默认 6 步），自动持久化 user/assistant 消息 + 工具调用记录到 `TaskRepoPort` |
| `app/use_cases/run_copilot.py` | `RunCopilotUseCase.stream(owner_id, task_id\|None, user_message, attachment_doc_ids)`：task_id=None 时通过 `TaskManagementUseCase` 建 task 并 yield `AgentEvent.task_created`；有附件就在 user_message 后拼 `\n\n[已上传文档 ID: ...]` |
| `app/container.py` | 加 3 个字段：`self.tool_registry / self.copilot_agent / self.run_copilot`；显式注释"工具注册表必须晚于所有 port 初始化（handler 闭包持有 self.\* 引用）" |
| `app/use_cases/run_query.py` | 顺手补 mypy 缺失类型：`_to_citation(chunk: Chunk)` |
| `tests/app/agent/__init__.py` | 空 |
| `tests/app/agent/test_events.py` | 12 用例：每种事件工厂方法 + frozen 不可变 + payload 防御性拷贝 + 枚举字符串值 |
| `tests/app/agent/test_decision.py` | 24 用例：happy path（3 种 action）+ 别名兼容（10 种）+ 围栏/抽取容错（3 种）+ 错误路径（9 种）+ 默认值/字段净化（4 种） |
| `tests/app/agent/test_tools.py` | 9 用例：`ToolSpec` frozen + 默认值 + 4 工具都注册到 + 全部 schema 合法 + `search_law` corpus 正确 + `search_user_docs` owner 隔离 + `web_search` 序列化 + `evidence_judge` 序列化 |
| `tests/app/agent/test_copilot.py` | 13 用例：单步 final_answer 持久化 + ask_user 终止 & 不写 assistant + 工具调用闭环 + owner_id 注入 + 未知工具软失败 + handler 抛错软失败 + 工具调用持久化 + JSON 解析失败兜底 + max_steps 兜底 + 三个入参校验 |
| `tests/app/test_run_copilot.py` | 6 用例：task 自动创建 + 长消息标题截断 + 有 task_id 不发 task_created + 附件 ID 注入 + 无附件 user_message 原样 + owner/message 校验 |

总计：6 个生产文件 + 5 个测试文件，新增测试 73 个（264 → 337）。

## 3. 设计决策

### 3.1 LLM 决策走 JSON 协议而非 OpenAI 原生 function-calling

```python
# LLM 在每一步只输出这样一段 JSON：
{
  "thought": "先检索 PIPL 第38条",
  "action": "tool",
  "tool_name": "search_law",
  "tool_args": {"query": "PIPL 数据出境"}
}
```

为什么不用 OpenAI 的 `tools=[...]` + `tool_calls`：
1. `ChatPort.chat(messages, ...) -> str` 是纯文本签名 —— 所有现有 adapter（OpenAI / 本地 LLM / FakeChat）零改动
2. 一旦改 ChatPort 加 `tools` 参数，4 个 adapter + 1 个 Fake + 既有 `RunQueryUseCase` 全要跟着改
3. JSON 协议在所有 LLM 上都能用（包括开源模型），可移植性强
4. 测试只需把预设响应改成 JSON 字符串，FakeChat 不动

代价是要写一个解析器，但解析器只有 60 行且全覆盖测试。

### 3.2 AgentDecision parser 故意宽松

```python
_ACTION_ALIASES = {
    "tool": "tool", "tools": "tool", "tool_call": "tool", "call_tool": "tool",
    "ask": "ask_user", "ask_user": "ask_user", "question": "ask_user",
    "answer": "final_answer", "final": "final_answer", "final_answer": "final_answer", "done": "final_answer",
}
```

LLM 输出不稳定是常态：有时候带 markdown 围栏、有时候 `action: "answer"` 而不是 `"final_answer"`、有时候在 JSON 前后夹一段中文。Parser 主动吸收这些差异，让模型升级不会立刻打爆 Agent。

错误路径仍然严格：未知 action / tool_name 缺失 / tool_args 非 dict 都抛 `AgentDecisionParseError`，由主循环转 `AgentEvent.decision_parse_error` 软失败。

### 3.3 ToolSpec 是声明式而非命令式

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters_schema: dict[str, Any]   # JSON Schema
    handler: Callable[..., Any]
    timeout_s: float = 30.0
    requires_owner: bool = True
```

新增工具 = 加一条 `ToolSpec`：

```python
"risk_profile": ToolSpec(
    name="risk_profile",
    description="对单个 factor 做证据判定",
    parameters_schema={"type": "object", "properties": {...}},
    handler=_risk_profile,
),
```

Agent 主循环对所有工具一视同仁：从 registry 取 spec → `handler(**args, owner_id=owner_id)` → 序列化结果回喂。这是面试常见追问"Agent 怎么扩展"的标准回答。

### 3.4 owner_id 在 Agent 层强制注入而非依赖 LLM

```python
call_kwargs = dict(tool_args)
if spec.requires_owner:
    call_kwargs["owner_id"] = owner_id
```

哪怕 LLM 在 `tool_args` 里写了别的 `owner_id`，Agent 也会用调用方传入的 `owner_id` 覆盖。这是数据隔离的最后一道防线 —— LLM 不能跨用户偷数据。`test_tool_call_owner_id_injected` 专门覆盖这点。

### 3.5 软失败 vs 硬失败

Agent 主循环对**工具异常 / 解析失败 / 未知工具**全部软失败：产出 error 事件 + 喂回观察值继续走（或兜底）。只有**owner_id / task_id 为空**这种调用方契约违反才硬失败抛 `ValueError`。

为什么：LLM 自由度高，错误路径必定出现；用户体验上"AI 卡死"比"AI 给一个有点拙劣但有结果的答案"差得多。

### 3.6 Agent 持久化职责

| 写入时机 | 写入内容 |
|---|---|
| `run()` 进入时 | user message |
| 工具调用结束 | 一条 ToolCall（含 input/output/status/duration_ms） |
| `final_answer` 时 | assistant message（含 citations） |
| `decision_parse_error` 兜底 | assistant message（兜底文案） |
| `max_steps_reached` 兜底 | assistant message（兜底文案） |
| `ask_user` 终止 | 不写 assistant（等用户下一轮 user_message 回来） |

`ask_user` 不写 assistant 是关键 —— 它是"半轮对话"，下一次 `run()` 时新的 user_message 会接着上次的语境。

### 3.7 `_to_domain_citations` 容错策略

LLM 给的 `citations` 字段可能字段名错、类型错、字符串过长。这个函数：
1. 接受 `parse_decision` 已过滤过的 `list[dict]`
2. 每条单独 try/except —— 一条失败不连累整体
3. `source_url` 非 str 时置 None
4. `text_snippet` 截断到 500 字符
5. `source_name` / `source_type` 缺失给默认值（"未知来源" / "law"），避免 domain 模型 `min_length=1` 校验失败

## 4. 运行验证

```powershell
ruff check app tests/app   # All checks passed!
mypy app                    # app/ 下 0 错误（retrieval/ 42 个历史错误未动）
pytest -q                   # 337 passed, 16 warnings in 19.34s
```

测试增量：

| Step | 测试数 |
|---|---|
| Step 008 | 264 |
| Step 009 | 337 (+73) |

## 5. 与 Strangler Fig 主线的关系

- 不改 `retrieval/agent/agentic_rag.py` 等老代码 —— 老 API 路径继续可用
- 新 `ComplianceCopilotAgent` 走全新依赖（ChatPort / RetrievePort / WebSearchPort / EvidencePort / TaskRepoPort）
- Step 010 (PR-6) 会加新路由 `POST /api/chat/run` 调 `container.run_copilot.stream`，老 `/api/query` 不动
- 老 `service.py` / `api/routes.py` 何时删 —— PR-7 风险画像接入完成、前端切完新路由后

## 6. 已知未实现项（按规划留到后续 step）

1. **MemoryPort 集成**：Agent 当前不调用 `MemoryPort`（4 层记忆）。Step 011 实现 MemoryPort + Agent 加 `self.memory` 字段后，在 `run()` 开头召回 recent_messages / facts 拼进 system prompt
2. **risk_profile / generate_checklist 工具**：等 Step 012 PR-7 写 `risk/factors.py` + `RiskProfilerUseCase` 后加进 `register_default_tools`
3. **流式 token-by-token**：当前 ChatPort `chat(...) -> str` 是阻塞返回。如果未来要 streaming token，需要 `ChatPort.stream(...) -> Iterator[str]`，Agent 拼到 thought / answer 时按 token 转发

## 7. 性能/容量

- 单次 `run()` 默认最多 6 步 → 最多 6 次 LLM 调用 + 6 次工具调用 + 12 次 SQLite 写
- Observation 序列化对单条工具结果做 4KB 截断，防 prompt 爆炸
- ToolSpec 持有 handler 闭包 = 持有 `container.retriever` 等引用，**container 不能被回收**，但 container 是单例所以 OK

## 8. 下一步（Step 010 PR-6）

加 FastAPI 路由层：
- `POST /api/auth/anonymous` → `container.auth_login.login_anonymous()`
- `GET /api/tasks` / `POST /api/tasks` / `DELETE /api/tasks/{id}` → `container.task_management`
- `POST /api/chat/run` （SSE）→ `container.run_copilot.stream(...)`，逐 event 转 SSE
- 老 `api/routes.py` 继续保留，新路由挂在 `/api/v2/...` 或同前缀新路径

不改 `service.py`、不删任何老代码。
