# Step 032 · 对齐 ChatGPT 记忆语义——当前对话上下文永远自动填充

## 1. 本步目标（为什么存在 / 服务于哪层 / 为后续提供什么）

S-031a/031b 落地了「每用户记忆设置」的双开关：

- `use_saved_memory` → 门控 L4 长期事实 + L3 用户画像
- `reference_history` → 门控 L1 最近原文 + L2 摘要（**当前任务的上下文**）

用户在试用后提出：「我觉得上下文应该自动填充，应该跟 GPT 类似。」

对照 ChatGPT 的三套机制：

1. **当前对话上下文窗口**：始终在线，**无开关**——这就是多轮连贯的来源。
2. **保存的记忆（画像/事实）**：开关「参考保存的记忆」控制，跨会话。
3. **跨对话历史检索**：开关「参考历史聊天记录」控制，从过去其它对话里召回。

S-031a 的 `reference_history` 实际门控的是「当前任务的 L1+L2」，关掉后**每轮从零开始**——这与 ChatGPT 的机制 (1) 冲突：当前对话上下文不应该被开关关掉。

本步纠正该语义错误：让**当前任务上下文（L1+L2）在装配出口无条件注入**，对齐 ChatGPT 的上下文窗口；`reference_history` 字段保留但退出本轮注入门控，前端第二开关隐藏，待真正的跨对话召回（机制 3）实现后再开放。

服务于：`app/memory` 装配层注入语义、`frontend` 设置弹窗。为后续提供：真正的「参考历史聊天记录」（跨对话/跨任务召回）留出干净的字段与 UI 位。

## 2. 修改文件（精确路径 + 一句话说明）

- `app/memory/assembler.py`
  - `assemble()`：`summary`（L2）与 `recent`（L1）改为**无条件注入**；`facts`（L4）与 `profile`（L3）仍受 `use_saved_memory` 门控。
  - `_safe_settings()`：返回值由 `tuple[bool, bool]` 收敛为 `bool`，仅返回 `use_saved_memory`（默认 `True`，读取异常 fail-open）。
  - docstring 更新为 S-032 语义说明。
- `tests/app/test_memory_assembler.py`
  - `TestSettingsGating` 类文档更新为 S-032 语义。
  - `test_reference_history_off_drops_summary_and_recent` → 重命名 `test_reference_history_off_keeps_current_context`：断言 `reference_history=False` 时摘要与历史**仍在**。
  - `test_both_off_returns_empty` → 重命名 `test_use_saved_memory_off_keeps_current_context`：断言两开关全关时长期记忆/用户画像不在、但对话摘要/历史对话**仍在**（block 非空）。
  - 其余用例（`test_no_store_defaults_all_on`/`test_both_on`/`test_use_saved_memory_off_drops_facts_and_profile`/`test_settings_read_failure_fails_open`）在新语义下依旧成立，无需改动。
- `frontend/index.html`
  - 第二 `.switch-row` 由「参考会话上下文 / `#toggle-reference-history`」改为「参考历史聊天记录（即将推出）」并加 `hidden` + `disabled` 预留（不删除，保留 DOM 位）。
  - 首开关「参考保存的记忆」描述补一句「当前对话内的上下文不受此开关影响，始终保持连贯」。
- `frontend/settings.js`
  - 未改：原有 `?.` 守卫使隐藏/禁用的 `#toggle-reference-history` 在 `loadSettings`/绑定时安全跳过（disabled input 不触发 change）。

## 3. 设计决策（关键技术选择 + 替代方案）

- **D1 · 当前任务上下文 L1+L2 恒在线**：对齐 ChatGPT 上下文窗口，多轮连贯不该被开关关闭。替代方案（保留 reference_history 门控 L1/L2）被否决——与用户心智模型和 ChatGPT 行为不符。
- **D2 · `use_saved_memory` 仍门控 L4+L3**：跨会话「保存的记忆」才是用户需要控制的隐私面。
- **D3 · `reference_history` 字段保留不删**：`MemorySettings` 模型 / SQLite 表 / `MemorySettingsUseCase` / API DTO 全部不动，向前兼容；仅退出 `assembler` 的注入门控，为未来真正的跨对话召回预留承载位。替代方案（直接删字段）被否决——会破坏已持久化数据与 API 契约。
- **D4 · `_safe_settings` 收敛为单 bool**：调用面只剩 `use_saved_memory` 一个门控点，减少元组解包；读取异常仍 fail-open（默认按开放处理，不因设置存储故障丢上下文）。
- **D5 · 前端隐藏而非删除第二开关**：保留 DOM 骨架（`hidden`+`disabled`），标签改诚实的「参考历史聊天记录（即将推出）」，待机制 (3) 实现后一行去掉 `hidden`/`disabled` 即可复用。
- **D6 · 用户不可用时自主决策**：本轮用户离线（系统提示「Work autonomously and make good decisions」），自主裁定为「保留单一可见开关 + 当前上下文自动填充」，与 ChatGPT 默认体验一致。

## 4. 核心契约 / 接口

`MemoryAssembler.assemble(owner_id, task_id, query) -> str` 注入块的组成（注入优先级 L4 > L2 > L3 > L1 的预算分配不变）：

| 段 | 层 | S-031a 门控 | S-032 门控 |
| --- | --- | --- | --- |
| 【相关长期记忆…】 | L4 facts | `use_saved_memory` | `use_saved_memory`（不变） |
| 【用户画像…】 | L3 profile | `use_saved_memory` | `use_saved_memory`（不变） |
| 【对话摘要…】 | L2 summary | `reference_history` | **无条件**（改） |
| 【历史对话…】 | L1 recent | `reference_history` | **无条件**（改） |

`_safe_settings(owner_id) -> bool`：返回 `use_saved_memory`；无 store / 读取异常时返回 `True`（fail-open）。

## 5. 与外部服务的关系

- 不触碰 `MemorySettingsStorePort` / SQLite `memory_settings` 表 / `GET·PUT /api/v2/memory/settings` —— `reference_history` 仍正常读写持久化，仅在装配层不再消费。
- `app/agent/copilot.py` 零改动（装配出口是唯一门控点，沿用 S-031a 的单出口设计）。

## 6. 当前实现范围（已实现 / 未实现按设计）

- 已实现：L1+L2 恒注入；`use_saved_memory` 门控 L4+L3；前端隐藏第二开关并修正描述与标签。
- 未实现（按设计推迟）：真正的「参考历史聊天记录」——跨对话/跨任务历史召回（后端无召回管线，`reference_history` 字段为其预留）。

## 7. 暂未实现 / TODO

- 跨对话/跨任务历史召回（真「参考历史聊天记录」机制）。
- 单条事实删除 v1.1（`DELETE /memory/facts/{id}` + 前端垃圾桶）。
- 开关状态在主聊天界面的可视化提示。

## 8. 测试与验证（命令 + 输出）

```
.venv\Scripts\python.exe -m pytest tests/app/test_memory_assembler.py tests/api/test_memory.py tests/app/test_memory_settings.py -q
# → 54 passed

.venv\Scripts\python.exe -m ruff check app/memory/assembler.py tests/app/test_memory_assembler.py
# → All checks passed!

.venv\Scripts\python.exe -c "import main"
# → ChromaDB 已连接（24 条）/ api/v2 routes mounted (tools=4) / import main OK

.venv\Scripts\python.exe -m pytest -q
# → 771 passed, 1 skipped（同 031a 基线，无回归）
```

浏览器实测（匿名会话 `127.0.0.1:8765`，`page.evaluate` 校验 DOM）：

- `#toggle-use-saved-memory` 存在；
- `#switch-row-reference-history` 存在但 `hidden=true`、`#toggle-reference-history` `disabled=true`；
- 首开关描述已更新为「…当前对话内的上下文不受此开关影响…」。

> 前端缓存提示：用户手动硬刷新（Ctrl+Shift+R）以加载最新 `index.html`。
