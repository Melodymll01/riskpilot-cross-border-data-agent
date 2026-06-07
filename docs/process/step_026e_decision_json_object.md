# Step 026e — GLM-5 决策 JSON 根治（response_format json_object）

## 1. 本步骤目标

Step 026d 用 `_repair_unescaped_quotes` 启发式**容错**修好了 GLM-5 偶发的非法 JSON，
但那是「事后补救」。本步从**源头**根治：在 Agent 决策轮让兼容 OpenAI 的网关启用
`response_format={"type": "json_object"}`，在**模型层**强制输出语法合法的 JSON，
把 026d 的引号修复降级为「纵深防御」第二道闸。

采用**轻量方案**（结构化输出）而非完整 function-calling：保留既有 JSON 协议架构
（`decision.py` / `copilot.py` / 事件流 / `FakeChat` 全不动主结构），爆炸半径最小。
完整原生 tool-calling 迁移属重型改造，留作后续 step。

## 2. 修改文件

| 文件 | 说明 |
|---|---|
| `domain/ports.py` | `ChatPort.chat` 签名加关键字参数 `json_mode: bool = False`（向后兼容） |
| `retrieval/generation/chat_client.py` | `complete()` / `_openai_complete()` 加 `response_format` 参数；`BadRequestError` 时去掉该参数**降级重试一次** |
| `infra/chat/openai_chat.py` | `OpenAIChatAdapter.chat` 加 `json_mode`，`True` 时转 `{"type":"json_object"}` 透传给 client；同步更新 `_ChatClientLike` 鸭子协议 |
| `app/agent/copilot.py` | 决策轮调用加 `json_mode=True`（自由文本的 `run_query` 保持默认 `False`） |
| `tests/fakes/fake_chat.py` | `FakeChat.chat` 加 `json_mode` 参数并记录到 `calls` |
| `tests/infra/test_service_adapters.py` | `_StubChatClient.complete` 加 `response_format` 记录；新增「json_mode 透传 response_format」断言 |
| `tests/retrieval/test_chat_client_response_format.py` | 新建：4 用例覆盖透传 / 默认省略 / BadRequest 降级重试 / 无 format 时异常上抛 |
| `tests/retrieval/__init__.py` | 新建空包 |

## 3. 设计决策

- **D1 轻量 `response_format` 而非完整 function-calling**：`json_object` 仅约束输出**语法
  合法**，不改变「LLM 吐 JSON、我方解析」的协议形态。整套 `decision.py` 解析、`copilot.py`
  主循环、`FakeChat` 测试替身全部零结构改动。原生 tool-calling 需重写决策协议 + 事件流 +
  Fake 的 `tool_calls` 模拟，重型，留后续。
- **D2 `BadRequestError` 优雅降级**：部分模型/网关不支持 `response_format`。`_openai_complete`
  捕获 `BadRequestError`，若当前带了 `response_format` 就**去掉它重试一次**；无该参数则原样上抛。
  保证换任意兼容网关都不会因为这个增强而整体挂掉。
- **D3 `json_mode` 关键字参数默认 `False`**：向后兼容。只有 Agent 决策轮显式传 `True`；
  `run_query`（自由文本生成）保持 `False`，避免把散文答复也强制成 JSON。
- **D4 保留 026d 的 `_repair_unescaped_quotes` 兜底**：纵深防御。即使某网关静默忽略
  `response_format`、或 json_object 实现有瑕疵，引号修复仍是 `json.loads` 失败后的第二道闸。
- **D5 系统提示已含 "json" 字样**：OpenAI 规范要求启用 `json_object` 时提示词必须出现 "json"，
  copilot 系统提示本就指示输出 JSON 决策，天然满足，无需改提示。

## 4. 核心契约 / 接口

- `ChatPort.chat(messages, *, temperature=0.2, max_tokens=None, json_mode=False) -> str`
  —— 新增 `json_mode` 关键字参数；唯一实现 `OpenAIChatAdapter` + 唯一替身 `FakeChat` 同步更新。
- `ChatClient.complete(..., response_format: dict | None = None) -> str`
  —— `response_format` 透传给 `chat.completions.create`；`BadRequestError` 触发去参降级重试一次。
- 调用面：`copilot.py` 决策轮 `json_mode=True`；`run_query.py` 自由文本保持默认 `False`。

## 5. 与外部服务的关系

- **chat**：`https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`（百炼 `GLM-5`）。
  live 验证：GLM-5 **原生接受** `response_format={"type":"json_object"}`，未触发 `BadRequestError`
  降级路径，决策轮直接拿到合法 JSON。
- **embedding**：不受影响（`json_mode` 仅作用于 chat 通道）。

## 6. 当前实现范围

- 已实现：决策轮模型层强制 JSON + 网关不支持时优雅降级 + 4 条 chat_client 单测 + 适配器透传断言。
- 按设计未做：完整原生 function-calling 迁移（重型，后续）；`run_query` 自由文本不启用 json_mode。

## 7. 暂未实现 / TODO

- 完整 OpenAI function-calling / tool-calling 迁移（重写决策协议，留后续 step）。
- research/profile 模式 live 覆盖（沿用 026d backlog）。

## 8. 测试与验证（命令 + 输出）

```powershell
# 新增 chat_client response_format 单测 + 适配器断言
.\.venv\Scripts\python.exe -m pytest tests/retrieval/test_chat_client_response_format.py tests/infra/test_service_adapters.py -q
# → 全绿（含 4 条新单测：透传 / 默认省略 / BadRequest 降级重试 / 无 format 上抛）

# 默认全量（live 自动 skip）
.\.venv\Scripts\python.exe -m pytest -q
# → 627 passed, 1 skipped（较 026d 的 623 +4 新单测）

# live 真服务端到端（需真 key）：验证 GLM-5 接受 json_object
$env:RUN_LIVE = "1"; .\.venv\Scripts\python.exe -m pytest -m live -q
# → 1 passed in 66.26s（response_format 透传成功，未触发降级）

# 静态检查
.\.venv\Scripts\python.exe -m ruff check domain/ports.py retrieval/generation/chat_client.py infra/chat/openai_chat.py app/agent/copilot.py tests/fakes/fake_chat.py tests/infra/test_service_adapters.py tests/retrieval/test_chat_client_response_format.py
# → All checks passed!
```
