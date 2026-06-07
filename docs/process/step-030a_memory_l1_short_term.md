# S-030a — L1 短期记忆注入 + MemoryAssembler 骨架

> 分层记忆系统第一步。设计全貌见 `docs/design/memory_system.md`。
> 本步只做 **L1（当前 task 最近 N 条历史注入 prompt）** 与 **MemoryAssembler 骨架**，
> 明确不实现 L2/L3/L4，也不实现固化（提取-验证-巩固）管线。

## 1. 本步骤目标

让 Copilot Agent 从「每轮无状态」升级为「带本 task 短期上下文」：

- **注入**：当前 task 最近 N 条消息能进 prompt；
- **隔离**：`owner_id` 校验必须生效，跨用户绝不泄露；
- **降级**：`memory=None`（或装配器为 `None`）时保持旧的无状态行为；
- **预算**：注入文本有 token 预算上限，超出从最旧消息丢弃；
- **骨架**：搭出 `MemoryPort` → `MemoryAssembler` → Agent 的接缝，
  为后续 L2/L3/L4 在同一处汇聚留好扩展点。

## 2. 修改 / 新增文件

| 文件 | 说明 |
|---|---|
| `config.py` | 新增 `memory_enabled` / `memory_recent_n`(默认 6) / `memory_token_budget`(默认 1500) |
| `domain/ports.py` | `MemoryPort.recent_messages` 签名补 `owner_id`：`recent_messages(owner_id, task_id, n)` |
| `infra/memory/__init__.py` | **新增**：导出 `TaskBackedMemory` |
| `infra/memory/task_memory.py` | **新增**：`TaskBackedMemory`——L1 挂在 task 消息表上，读前做 owner 归属校验；L2/L3/L4 抛 `NotImplementedError` |
| `app/memory/__init__.py` | **新增**：导出 `MemoryAssembler` |
| `app/memory/assembler.py` | **新增**：`MemoryAssembler`——读 L1 + 排版 + token 预算裁剪 + 全程降级 |
| `app/agent/copilot.py` | 注入 `memory_assembler`；`run()` 在写当前消息**前**读历史块；`_build_messages` 插入记忆 system 消息 |
| `app/factories.py` | 新增 `build_memory(settings, *, task_repo) -> MemoryPort \| None`（禁用返回 None） |
| `app/container.py` | 装配 `self.memory` + `self.memory_assembler`，传入 Agent；docstring 12→13 个 Port |
| `tests/fakes/fake_memory.py` | **新增**：`FakeMemory`（可预置历史 + 归属语义 + 断言调用） |
| `tests/infra/test_task_memory.py` | **新增**：隔离类测试（owner 校验、跨用户不泄露、越界/空） |
| `tests/app/test_memory_assembler.py` | **新增**：注入排版 + token 预算 + 降级 |
| `tests/app/agent/test_copilot_memory.py` | **新增**：Agent 注入 + 当前消息不重复 + owner 隔离 + 降级 |

## 3. 设计决策

- **D1 L1 不另存一份，复用 task 消息表**：`TaskBackedMemory` 底层就是 `TaskRepoPort`，
  `recent_messages` = `list_messages(task_id)[-n:]`。避免双写 / 一致性问题；
  历史落库仍由 Agent 主循环经 `task_repo` 完成，记忆层只读。

- **D2 owner_id 进 `recent_messages` 签名，越权返回空而非抛错**：验收要求 owner 校验，
  但原签名 `recent_messages(task_id, n)` 无 owner。选择**给 Protocol 补 owner_id**，
  适配器内 `repo.get(task_id, owner_id) is None → return []`。
  跨用户读 → 空列表（安全降级，不泄露、不 crash），由 `test_other_owner_gets_empty_no_leak` 守护。

- **D3 读历史发生在写当前消息之前**：`run()` 先 `memory_block = _memory_block(...)`，
  再 `append_message(当前 user)`。保证注入块只含**先前轮次**，当前问题不会重复塞回
  （`test_current_message_not_in_memory_block` 守护）。

- **D4 记忆块作为独立 system 消息插在主提示词之后**：`_build_messages` 在主 system 后、
  user 之前插入 `{"role":"system", content: 记忆块}`，仅当非空。旧行为（无块）原样保留，
  `test_no_assembler_keeps_old_behavior` 断言「恰好 1 个 system」。

- **D5 token 预算用字符数保守近似，从最旧丢弃**：`_estimate_tokens(text)=len(text)`（中文偏高估，
  宁可少注入）。从最新往最旧累加，超预算即停；单条超预算时**至少保留最近一条**，避免全空。

- **D6 装配器全程降级**：`memory is None` / `recent_n<=0` / 无历史 / 读取异常 → 一律返回 `""`，
  绝不让记忆故障中断主对话。

- **D7 只实现 L1**：`TaskBackedMemory` 与 `FakeMemory` 的 L2/L3/L4 方法均抛 `NotImplementedError`
  并标注归属步骤（S-030b/c/d），保持 `MemoryPort` 接口完整但不越界实现。

## 4. 配置项

| 配置 | 默认 | 作用 |
|---|---|---|
| `memory_enabled` | `True` | False → 容器装配 `memory=None`，Agent 退回无状态旧行为 |
| `memory_recent_n` | `6` | 注入的最近历史条数（不含当前轮）；0 等价关闭注入 |
| `memory_token_budget` | `1500` | 记忆块 token 预算（字符近似）；超出从最旧丢弃 |

## 5. 注入文本格式

```
【历史对话（仅供参考，避免重复已回答内容）】
用户：上一轮问题
助手：上一轮回答
```

## 6. 验收对照

| 验收项 | 守护测试 |
|---|---|
| 1. 最近 N 条注入 prompt | `test_recent_history_injected_into_prompt` / `TestRecentMessages` |
| 2. owner_id 校验生效 | `test_other_owner_gets_empty_no_leak` / `test_owner_isolation_no_cross_leak` |
| 3. memory=None 保持旧行为 | `test_no_assembler_keeps_old_behavior` / `test_memory_none_returns_empty` |
| 4. token 预算上限 | `TestTokenBudget`（丢最旧 + 至少留最近一条） |
| 5. 隔离 / 注入 / 降级三类测试 | `tests/infra/test_task_memory.py` + `tests/app/test_memory_assembler.py` + `tests/app/agent/test_copilot_memory.py` |
| 6. 本文档 | 即本文件 |

## 7. 验证结果

- 新增记忆测试：**20 passed**。
- 全量：**648 passed / 1 skipped**（S-029 基线 627，+21）。
- `ruff check` 全部 changed 文件：All checks passed。
- `import main` OK，v2 路由正常挂载。

## 8. 不在本步范围（后续）

- **L2 摘要 + TTL** → S-030b
- **L4 语义事实固化管线（提取-验证-巩固，fork 异步）** → S-030c
- **L3 用户画像 + 主动遗忘 + 审计** → S-030d

接缝已留：`MemoryAssembler.assemble()` 是后续各层汇聚 + 统一预算裁剪的唯一入口。
