# Step 034 · 单条长期事实删除（被遗忘权细粒度 v1.1）

> 把 S-031a/S-031b/S-030d 一路推迟到"v1.1"的同一件事补齐：在记忆管理面板里
> 给每条长期事实加一个删除按钮，后端提供 `DELETE /api/v2/memory/facts/{id}`，
> 让用户能**精确删除某一条**记忆，而不必只能"清空全部"。

## 1. 本步目标

S-030d 实现了主动遗忘 `forget(scope)`——但只能"全清记忆"或"全清含对话"两种粗粒度。
S-031b 的管理面板把长期事实**只读**列了出来，删除留到 v1.1。本步补齐细粒度：
- 后端：`MemoryPort.delete_fact(owner_id, fact_id) -> bool`（owner 隔离 + 物理删除），
  use-case 编排 + `MEMORY_FACT_DELETE` 审计，REST `DELETE /memory/facts/{id}`（204 / 404）。
- 前端：每条事实右上角"×"删除按钮，二次确认，删后即时刷新清单。

复用 S-030c 已有的 `FactStorePort.delete(owner_id, fact_id)`（早已实现 owner 归属校验），
本步只是把它接到 MemoryPort → use-case → API → 前端的完整链路上。

## 2. 修改文件（精确路径 + 一句话）

| 文件 | 改动 |
| --- | --- |
| `domain/models.py` | `AuditAction` +`MEMORY_FACT_DELETE = "memory.fact_delete"` |
| `domain/ports.py` | `MemoryPort` +`delete_fact(owner_id, fact_id) -> bool`（list_facts 与 forget 之间） |
| `infra/memory/task_memory.py` | +`delete_fact`：fact_store 缺失/`get` 为空 → False；否则 `delete` 后 True（owner 隔离靠 get/delete 的归属校验） |
| `app/use_cases/forget_memory.py` | +`delete_fact` 方法（memory=None→False 不审计；真删→记 `MEMORY_FACT_DELETE` 审计只存 fact_id；异常→记失败审计再抛）；`_record_audit` 参数化 `action` |
| `api/v2/memory.py` | +`DELETE /facts/{fact_id}`（成功 204；未找到/越权 404）；import 补 `HTTPException/Response/status` |
| `frontend/api.js` | `memory` 客户端 +`deleteFact(factId)`（DELETE，204→null，404→ApiError） |
| `frontend/settings.js` | `loadFacts` 每条事实加"×"删除按钮；+`onDeleteFact`（二次确认 + 删后 `loadFacts`；404 当作已删除友好处理） |
| `frontend/style.css` | +`.memory-fact-del`（圆形悬浮删除钮，hover 染红）；`.memory-fact` 改 `position:relative` 留右内边距 |
| `tests/fakes/fake_memory.py` | `FakeMemory` +`delete_fact` + `delete_fact_calls` |
| `tests/app/test_forget_memory.py` | +`TestDeleteFact`（删+审计 / 未找到不审计 / memory=None / 异常审计后抛，4 例） |
| `tests/infra/test_task_memory.py` | +`TestL4DeleteFact`（无 store / 删存在 / 未找到 / 跨 owner 不删，4 例） |
| `tests/api/test_memory.py` | +`TestDeleteFact`（401 / 204+清单空 / 404 未找到 / 404 越权 / 404 memory 禁用，5 例） |

## 3. 设计决策（含替代方案）

- **D1：复用 `FactStorePort.delete` 而非新增端口方法。** S-030c 的 `delete(owner_id, fact_id)` 早已带归属校验（先 `get` 确认 owner 再删），本步零改 FactStorePort，只在 MemoryPort 暴露一个面向用例的 `delete_fact`。
- **D2：`delete_fact` 返回 `bool` 让 API 能区分 404。** 删成功 True→204；事实不存在 / 不属于当前 owner / 记忆禁用 → False → API 抛 404。避免"删了个不存在的东西却回 200"的语义含糊。
- **D3：越权删除返回 404 而非 403。** `fact_store.get(owner_id, fact_id)` 对别人的事实返回 None，对当前 owner 表现为"不存在"——不泄露"这条 id 存在但属于别人"，与 KB/文档层一致的隐私口径。
- **D4：审计只存 `fact_id`，不回存被删文本。** 与 `forget`/settings 审计同口径（数据最小化）；只有**真发生删除**才落 `MEMORY_FACT_DELETE`（未找到不写审计，避免噪声），异常则落 `success=False` 再抛。
- **D5：物理删除（区别 S-030c 的逻辑遗忘）。** 召回链路里的 superseded/过期是"逻辑遮蔽"，而用户主动删除是被遗忘权 → 直接 `_col.delete` 物理移除，与 `forget` 的物理删除一致。
- **D6：前端删后重新 `loadFacts` 而非本地摘除 DOM。** 一次轻量 GET 保证容量计数 `N/cap` 与列表跟服务端一致，避免本地状态漂移；404 当作"已不存在"友好提示并照常刷新。

## 4. 核心契约 / 接口

```python
# domain/ports.py · MemoryPort
def delete_fact(self, owner_id: str, fact_id: str) -> bool: ...
    # True=已删；False=不存在/不属于该 owner/记忆禁用
```

```python
# app/use_cases/forget_memory.py
def delete_fact(self, owner_id, fact_id, *, request_id=None) -> bool: ...
    # 真删→MEMORY_FACT_DELETE 审计（extra={"fact_id": ...}）
```

```
DELETE /api/v2/memory/facts/{fact_id}
  204 No Content        删除成功
  404 Not Found         事实不存在 / 不属于当前 owner / 记忆禁用
  401 Unauthorized      未带 owner 凭证
```

```js
// frontend/api.js
memory.deleteFact(factId)  // 204→null；404→throw ApiError(404)
```

## 5. 与外部服务关系

无新增外部依赖。底层走 ChromaDB `memory_facts` collection 的 `delete(ids=[...])`，
owner 隔离由 `get(where={owner_id})` 前置校验保证。审计经既有 `AuditLogPort` 落 SQLite。

## 6. 当前实现范围

- ✅ 后端 `delete_fact`（owner 隔离 + 物理删除 + bool 返回）。
- ✅ use-case 审计编排（真删才记、异常也留痕、memory 禁用静默）。
- ✅ REST `DELETE /memory/facts/{id}`（204 / 404 / 401）。
- ✅ 前端每条事实删除按钮 + 二次确认 + 删后刷新。
- ✅ 全链路降级：fact_store 缺失 / 记忆禁用 → False/404，不抛崩。

## 7. 暂未实现（TODO / backlog）

- L3 用户画像的单条键删除（本步只覆盖 L4 事实；画像删除仍走整体 forget）。
- 批量勾选删除 / 撤销（undo）。
- 删除后在主聊天界面的提示。
- 消息级语义跨对话召回（S-033 backlog 延续）。

## 8. 测试与验证

```powershell
# 删除相关子集
.\.venv\Scripts\python.exe -m pytest tests/app/test_forget_memory.py tests/api/test_memory.py tests/infra/test_task_memory.py tests/app/test_memory_assembler.py -q
# → 107 passed

# 全量
.\.venv\Scripts\python.exe -m pytest -q
# → 791 passed / 1 skipped（较 033 +14）

# ruff
.\.venv\Scripts\python.exe -m ruff check domain/models.py domain/ports.py infra/memory/task_memory.py app/use_cases/forget_memory.py api/v2/memory.py tests/...
# → All checks passed

# import 冒烟
.\.venv\Scripts\python.exe -c "import main"
# → api/v2 routes mounted tools=4

# 浏览器实测（匿名会话 127.0.0.1:8765）
# → /static/{api.js,settings.js,style.css} 均含新代码（deleteFact / onDeleteFact / .memory-fact-del）
# → DELETE /api/v2/memory/facts/nonexistent → 404（路由已挂载 + owner 隔离）
```
