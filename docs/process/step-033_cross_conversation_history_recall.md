# Step 033 · 跨对话历史召回（真「参考历史聊天记录」）

> 把 S-031b 隐藏、S-032 标记为"即将推出"的第二开关 `reference_history` 真正落地：
> 让助手在回复时能够**参考你以往其它对话的内容**（跨对话 / 跨任务召回），
> 开关默认**关闭**——严格对齐 ChatGPT 的"参考历史聊天记录"语义（默认 off，需用户显式开启）。

## 1. 本步目标

ChatGPT 记忆有三套机制：

1. **当前对话上下文窗口**——始终在线、无开关（= 本系统 L1 最近原文 + L2 本任务摘要，S-032 已做成无条件注入）。
2. **保存的记忆（画像/事实）**——开关「参考保存的记忆」（= `use_saved_memory` → L3 画像 + L4 语义事实，缺省开）。
3. **跨对话历史检索**——开关「参考历史聊天记录」，**默认关**（= 本步新增 `reference_history` → **L5 跨任务摘要召回**）。

S-032 让 (1) 永远在线、(2) 受 `use_saved_memory` 门控，但把 (3) 的第二开关 hidden+disabled，因为彼时没有任何"跨对话召回"后端。本步补齐 (3)：新增 **L5 召回层**，复用**其它任务**已有的 L2 摘要作为"过往对话"的表示，按 `updated_at` 倒序（最近优先）召回，受 owner 隔离与 TTL 过滤；由 `reference_history` 门控、**缺省关**；并恢复前端第二开关为可用。

## 2. 修改文件（精确路径 + 一句话）

| 文件 | 改动 |
| --- | --- |
| `domain/models.py` | `MemorySettings.reference_history` 默认 `True`→**`False`**；docstring 重写为 L5 跨对话召回语义、缺省关、对齐 ChatGPT；补 L1+L2 当前上下文恒注入说明 |
| `domain/ports.py` | `MemoryPort` 新增 `recall_history(owner_id, exclude_task_id, k) -> list[TaskSummary]`（L5 跨对话历史召回契约） |
| `config.py` | 新增 `memory_history_recall_k: int = Field(3, ge=0, le=20)`（L5 召回条数预算） |
| `infra/memory/task_memory.py` | 实现 `recall_history`：经 `TaskRepoPort.list_for_owner`（updated_at 倒序）取候选，跳过当前 task / 无摘要 / 过期（L2 TTL），最多取 k 条 `TaskSummary` |
| `app/memory/assembler.py` | 装配器接 `history_k`；`assemble()` 拆 `_safe_settings` 为 `(use_saved_memory, reference_history)` 元组；`reference_history` 真时 `_safe_history` 召回 L5；`_render` 新增 history 块（排版于 L2 本任务摘要之后、L3 画像之前） |
| `app/container.py` | `MemoryAssembler` 构造补 `history_k=settings.memory_history_recall_k` |
| `frontend/index.html` | 第二 `.switch-row` 去 `hidden`+`disabled`，标签「参考历史聊天记录」、描述「允许助手在回复时参考你以往其它对话的内容（跨对话召回）。默认关闭。」 |
| `tests/fakes/fake_memory.py` | `FakeMemory` 补 `recall_history` + `history_calls` 记录 |
| `tests/app/test_memory_assembler.py` | `TestSettingsGating` 文档/夹具更新；补 history 块断言（默认关→无、开→有「过往对话」） |
| `tests/infra/test_task_memory.py` | 新增 `TestL5RecallHistory`（倒序/排除当前/k 限/owner 隔离/过期跳过/无 store/k=0 共 7 例） |
| `tests/api/test_memory.py`、`tests/app/test_memory_settings.py` | 默认值断言 `reference_history` 由 `True` 改 `False` |

## 3. 设计决策（含替代方案）

- **D1：L5 复用其它任务的 L2 摘要，而非新建消息级嵌入索引。** "过往对话"用各任务已有的滚动摘要表示，`list_for_owner` 天然 updated_at 倒序＝最近优先。替代方案（对全量原始消息建向量索引做语义召回）成本高、需新存储，留作 backlog。本步是诚实的 v1：零新存储、复用 `TaskRepoPort` + `SummaryStorePort`。
- **D2：`reference_history` 读取异常 fail-CLOSED（→ False）。** 与 `use_saved_memory` 的 fail-OPEN 不同——参考**其它对话**属于更强的隐私动作，异常时宁可不召回。`_safe_settings` 无 store/None/异常时返回 `(True, False)`。
- **D3：默认关，对齐 ChatGPT。** ChatGPT「参考历史聊天记录」出厂即关，需用户显式打开。`MemorySettings.reference_history` 默认 `False`，use-case `get` 缺省构造同此默认。
- **D4：注入排版顺序 L4 事实 > L2 本任务摘要 > L5 过往对话 > L3 画像 > L1 最近原文。** 当前任务摘要优先于其它对话摘要；L5 与 facts 同样走 token 预算，超预算丢弃整块不挤占近端上下文。
- **D5：前端恢复开关而非新建。** S-032 留下的第二 `.switch-row` 去掉 `hidden`+`disabled` 即可，`settings.js` 既有 `?.` 守卫与 `onToggle("reference_history")` 接线无需改动。

## 4. 核心契约 / 接口

```python
# domain/ports.py · MemoryPort
def recall_history(
    self, owner_id: str, exclude_task_id: str, k: int
) -> list[TaskSummary]:
    """L5 跨对话历史召回：返回该 owner 其它任务最近的 L2 摘要（最多 k 条）。"""
```

```python
# app/memory/assembler.py
_HISTORY_HEADER = "【过往对话（你与该用户更早的其它对话摘要，仅供参考）】"
# assemble(): use_saved_memory, reference_history = self._safe_settings(owner_id=...)
#   history = self._safe_history(owner_id=..., task_id=...) if reference_history else []
```

## 5. 与外部服务关系

无新增外部依赖。L5 召回纯走既有 SQLite（`tasks` + `task_summaries` 表）。不调用 LLM、不触 ChromaDB。owner 隔离由 `list_for_owner` 在 SQL 层完成，TTL 复用 L2 的 `_is_expired`。

## 6. 当前实现范围

- ✅ `recall_history` 后端 + 装配器 L5 块 + `reference_history` 门控（缺省关）。
- ✅ 前端第二开关恢复可用（标签/描述/默认关）。
- ✅ 倒序（最近优先）、排除当前 task、owner 隔离、L2 TTL 过期跳过、k 条预算。
- ✅ 全链路降级：无 store / 召回异常 → 返回空、不拖垮主回复。

## 7. 暂未实现（TODO / backlog）

- 消息级**语义**跨对话召回（对原始消息建嵌入索引，相关性而非纯时间排序）。
- 单条事实删除 v1.1（`DELETE /memory/facts/{id}` + 前端回收站按钮）。
- 开关状态在主聊天界面的可视化提示。
- L5 召回结果在前端的呈现（"参考了哪些过往对话"）。

## 8. 测试与验证

```powershell
# 记忆子集
.\.venv\Scripts\python.exe -m pytest tests/app/test_memory_assembler.py tests/app/test_memory_settings.py tests/api/test_memory.py tests/infra/test_task_memory.py -q
# → 97 passed

# 全量
.\.venv\Scripts\python.exe -m pytest -q
# → 777 passed / 1 skipped（test_jwt_issuer 的篡改用例偶发，单跑 10 passed）

# ruff
.\.venv\Scripts\python.exe -m ruff check app/memory/assembler.py infra/memory/task_memory.py tests/fakes/fake_memory.py tests/infra/test_task_memory.py tests/app/test_memory_assembler.py domain/models.py domain/ports.py config.py app/container.py
# → All checks passed

# import 冒烟
.\.venv\Scripts\python.exe -c "import main"
# → ChromaDB 已连接 24 条；api/v2 routes mounted tools=4

# 浏览器实测（匿名会话 127.0.0.1:8765）
# → #switch-row-reference-history 不再 hidden、#toggle-reference-history 不再 disabled、
#   标签「参考历史聊天记录」、描述含「默认关闭」
```
