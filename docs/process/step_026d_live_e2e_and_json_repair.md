# Step 026d — RAG 链路真服务端到端验证（live e2e）+ 修复 GLM-5 JSON 解析缺陷

## 1. 本步骤目标

补上整条 RAG 链路最后一块拼图：用**真实**外部服务（智谱 `embedding-3` + 百炼 `GLM-5`）
跑一遍 `匿名登录 → 上传文档 → 提问 → 拿到带引用的答案`，证明 Step 026b 拆分 chat/embed
双通道后，生产装配（`main.app` 真容器、真适配器）端到端真能跑通。

这是 Step 026b「Next」里挂的 **Phase 2 端到端验证（用户填 key 后）**。

意外收获：live 测试第一次跑就**暴露了一个真实缺陷**——GLM-5 偶尔在决策 JSON 的字符串值里
塞未转义的 ASCII 双引号，导致 `parse_decision` 解析失败、Agent 回退到「抱歉，无法给出可靠
回答」兜底。本步顺带修复了解析器健壮性。

## 2. 修改文件

| 文件 | 说明 |
|---|---|
| `pytest.ini` | `markers` 段新增 `live`（命中真实外部服务；需真 key + `RUN_LIVE=1`；CI 跳过） |
| `tests/live/__init__.py` | 新建空包 |
| `tests/live/conftest.py` | `live_app` fixture（`from main import app` 真容器）+ `_guard_live` autouse（双重门禁：`RUN_LIVE=1` 且 `.env` 非占位符 key，否则 `pytest.skip`） |
| `tests/live/test_rag_pipeline.py` | 唯一 live 用例：登录→上传《个人信息保护法》→同步问答→断言真 LLM 产出有效答案 |
| `app/agent/decision.py` | `parse_decision` 加 `_repair_unescaped_quotes` 兜底：`json.loads` 失败时启发式修复字符串值内未转义的双引号再重解析 |
| `tests/app/agent/test_decision.py` | `TestParseDecisionUnescapedQuotes` 4 用例（thought/answer 内未转义引号、引用保留、合法 JSON 不受影响） |

## 3. 设计决策

- **D1 新 marker `live` 而非复用 `e2e`**：`pytest.ini` 里 `e2e` 已被占用，语义是
  「FastAPI TestClient + 全 Fake」。真实服务测试语义相反（真网络、真花钱、可能 flaky），
  必须独立标记，否则会污染既有 `e2e` 语义。
- **D2 TestClient 进程内打真适配器，而非子进程起 uvicorn**：`from main import app` 拿到的
  就是生产装配（真 `AppContainer`）；TestClient 发请求，真网络照样出去打智谱/百炼。够用、轻、
  无需管理子进程生命周期。
- **D3 自带文档上传，零污染真实库**：root `tests/conftest.py` 在收集期把
  `CHROMA_PERSIST_DIR`/`UPLOAD_DIR` `setdefault` 到临时目录，故 live 测试连的是**空临时
  chroma**。因此必须自己上传文档——既覆盖「真 embedding 写入」路径，又天然**不碰**真实
  `data/chroma_db`。
- **D4 双重门禁默认关闭**：`RUN_LIVE=1` 显式开关 + `.env` 真 key 检测，二者任缺即 skip。
  保护普通 `pytest` 全量轮次不产生网络调用 / 不花钱，同时 CI（无真 key）自动跳过。
- **D5 断言「宽松但有意义」**：真 LLM 输出随机，只验 `answer` 非空（≥20 字）+ 命中领域关键词
  + ReAct 真跑过（有 `thought` 事件），不对法条号 / 引用条目精确匹配，避免 flaky。
- **D6 解析器兜底而非放宽测试**：发现 GLM-5 JSON 缺陷后，正确做法是修解析器让流水线真能用，
  而不是把测试断言放水接受兜底答复。
- **D7 解析修复仅在 `json.loads` 失败后触发**：合法 JSON 走原路径零改动；只有标准解析失败才尝试
  启发式修复，再失败才抛 `AgentDecisionParseError`，保持降级不 crash。

## 4. 核心契约 / 接口

- 同步问答端点 `POST /api/v2/copilot/chat` 把 Agent 全部事件 collect 成 list 返回
  （`ChatResponse{task_id, events:[{event_type, payload}]}`），live 测试用它直接断言
  `answer` 事件，**完全绕开 SSE 解析**。
- `_repair_unescaped_quotes(snippet: str) -> str`：启发式状态机。字符串内部遇 `"`，向后跳空白
  看下一非空白字符；是结构分隔符 `, : } ]` 或 EOF → 视为结束引号，否则视为字面引号转义成 `\"`。
  转义序列 `\x` 原样保留。
  - 已知局限：字符串文本内 `"` 紧跟 `,`（如 `他说"好",`）会被误判结束；法规问答场景极少触发，
    仅作 `json.loads` 失败后兜底，不影响合法 JSON。

## 5. 与外部服务的关系

- **embedding**：`https://open.bigmodel.cn/api/paas/v4/embeddings`（智谱 `embedding-3`，2048 维）
  —— 上传时 embed chunk、提问时 embed query。
- **chat**：`https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`（百炼 `GLM-5`）
  —— ReAct 主循环每步决策。
- 真实跑通日志佐证：`HTTP/1.1 200 OK` 两家都打到、检索返回 5 条、`POST /copilot/chat → 200`。

## 6. 当前实现范围

- 已实现：1 条贯穿真服务的 happy-path live 用例 + 解析器未转义引号兜底修复 + 4 条解析回归单测。
- 按设计未做：写链路 admin/公共库分支、research/profile 模式的 live 覆盖、SSE 流式端点的 live
  验证（同步端点已覆盖事件协议，SSE 仅传输层差异，由 `tests/api` Fake 覆盖）。

## 7. 暂未实现 / TODO

- GLM-5 JSON 健壮性根治：当前是「容错解析」，更稳的方案是改用 OpenAI function-calling /
  结构化输出约束 LLM（大改 `ChatPort`，留后续）。
- live 用例目前单条；后续可参数化覆盖 research 模式 + 多轮对话。
- `_repair_unescaped_quotes` 的 `",` 误判边界用例（罕见）未处理。

## 8. 测试与验证（命令 + 输出）

```powershell
# 解析器回归 + 既有用例（离线）
.\.venv\Scripts\python.exe -m pytest tests/app/agent/test_decision.py -q
# → 34 passed

# live 真服务端到端（需真 key）
$env:RUN_LIVE = "1"; .\.venv\Scripts\python.exe -m pytest -m live -q
# → 修复前：1 failed（答案=兜底「抱歉，无法给出可靠回答」，根因 GLM-5 未转义引号）
# → 修复后：1 passed in 67.14s

# 默认全量（不带 RUN_LIVE，live 自动 skip）
.\.venv\Scripts\python.exe -m pytest -q
# → 623 passed, 1 skipped（live 默认关闭）

# 静态检查
.\.venv\Scripts\python.exe -m ruff check app/agent/decision.py tests/live tests/app/agent/test_decision.py
# → All checks passed!
```

真实捕获的失败样本（节选，已脱敏为结构）：
`"thought": "已检索到第三章"个人信息跨境提供的规则"的相关条款..."` —— `第三章` 与 `的相关条款`
之间的 ASCII 双引号未转义，`json.loads` 在 `line 2 column 38` 报 `Expecting ',' delimiter`。
