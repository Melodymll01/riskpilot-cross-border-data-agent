# S-030d — L3 用户画像（稳定偏好）+ 主动遗忘（被遗忘权）+ 记忆审计

> 分层记忆系统第四步，也是「记忆」收官步。设计全貌见 `docs/design/memory_system.md`
> （§14.5 L3 画像策略、§6 主动遗忘、§10/§11 审计与合规）。本步在 S-030a/b/c 的 L1/L2/L4 之上补
> **L3 用户画像层（按 `owner_id` 跨会话稳定偏好）**、**主动遗忘 `forget()`（PIPL §47 被遗忘权）**
> 与 **遗忘审计**。明确不实现「自动从 L4 聚合 L3」「Agent 工具主动写画像」（按 §14.5 推迟）。

## 1. 本步骤目标

让 Copilot 从「记住用户长期事实」升级为「沉淀稳定偏好 + 支持用户一键清空记忆」：

- **L3 画像**：按 `owner_id` 存一张稳定偏好表（如「语言：中文」「行业：跨境电商」），
  跨 task / 跨会话注入，作为「你已知道的用户偏好」；
- **读/写画像**：`get_profile`（无 store 返回空画像）/ `update_profile`（浅合并，空输入空操作）；
- **注入**：画像块汇聚进 prompt，优先级 **L4 > L2 > L3 > L1**（偏好稳定但价值密度低于即时事实/摘要）；
- **主动遗忘**：`forget(owner_id, scope)` 级联**物理删除**派生记忆——
  `scope="memory"` 清 L2 摘要 / L3 画像 / L4 事实 / 固化水位，**保留 L1 原始 task**；
  `scope="all"` 额外级联删 L1 原始 task；
- **审计**：`ForgetMemoryUseCase` 把「谁、删了哪个范围、各层删了多少条」落 `AuditLogPort`，
  **只记删除计数、不回存被删内容**（审计自身守数据最小化）；失败也先落审计再抛；
- **API**：`GET /memory/profile`（查自己画像）+ `POST /memory/forget`（行使被遗忘权），全程 owner 隔离；
- **隔离**：所有 L3 读写 / forget 继续按 `owner_id` 归属，跨用户绝不串改（合规底线）。

## 2. 修改 / 新增文件

| 文件 | 说明 |
|---|---|
| `config.py` | 新增 L3 配置块：`memory_profile_enabled`(True) / `memory_profile_max_facts`(8) |
| `domain/models.py` | **新增** `ForgetResult`（owner_id/scope/各层删除计数 + `total_deleted` 属性）；`AuditAction` 补 `MEMORY_FORGET` / `MEMORY_PROFILE_UPDATE`（`SessionProfile` S-030a 已存在） |
| `domain/ports.py` | `MemoryPort` 补 `forget`；4 个 Store Port（Summary/Fact/ConsolidationState/Profile）统一加 `delete_owner`；**新增** `ProfileStorePort`（get/upsert/delete_owner） |
| `infra/storage/_db.py` | **新增** `profiles` 表（PK owner_id，无 FK——按 owner 而非 task） |
| `infra/storage/sqlite_profile_store.py` | **新增**：`SqliteProfileStore`——get / upsert(ON CONFLICT) / delete_owner，facts JSON 序列化（`ensure_ascii=False`） |
| `infra/storage/sqlite_summary_store.py` | 补 `delete_owner`（DELETE WHERE owner_id） |
| `infra/storage/sqlite_consolidation_state.py` | 补 `delete_owner` |
| `infra/storage/__init__.py` | 导出 `SqliteProfileStore` |
| `infra/memory/fact_store.py` | `ChromaFactStore` 补 `delete_owner`（count 后 `where={owner_id}` 批删，返回删除数） |
| `infra/memory/task_memory.py` | `TaskBackedMemory` 注入 `profile_store` / `state_store`；落地 L3 `get_profile` / `update_profile`（替换占位）；**新增** `forget`（按 scope 级联 delete_owner，scope=all 再遍历删 task） |
| `app/memory/assembler.py` | 汇聚补 L3：注入画像块（L4>L2>**L3**>L1），预算耗尽整块丢弃、不挤占 L1 ≥1 保底；`_safe_profile` 独立降级 |
| `app/use_cases/forget_memory.py` | **新增**：`ForgetMemoryUseCase`——编排 `memory.forget` + 审计（成功记计数 / 失败记错误后抛 / memory=None 零结果不审计） |
| `app/factories.py` | **新增** `build_profile_store`；`build_memory` 注入 profile_store + state_store |
| `app/container.py` | 装配 `profile_store`，注入 memory；assembler 传 `profile_max_facts`；**新增** `forget_memory` use case |
| `api/v2/schemas.py` | **新增** `ProfileResponse` / `ForgetRequest`(scope Literal memory\|all) / `ForgetResponse` |
| `api/v2/memory.py` | **新增**：`build_memory_routes`——`GET /memory/profile` + `POST /memory/forget`，过 `require_owner` |
| `api/v2/router.py` | 挂载 `build_memory_routes` |
| `tests/fakes/fake_profile_store.py` | **新增**：`InMemoryProfileStore` |
| `tests/fakes/fake_memory.py` | `FakeMemory` 落地 L3 get/update_profile + `forget`（可断言调用），新增 `profiles` 预置 |
| `tests/fakes/fake_summary_store.py` / `fake_fact_store.py` | 各 Store Fake 补 `delete_owner` |
| `tests/infra/test_sqlite_profile_store.py` | **新增**：协议契约 / get-upsert-覆盖 / owner 隔离 / delete_owner |
| `tests/infra/test_task_memory.py` | 扩展：`TestL3Profile`（空/合并/空输入空操作/无 store）+ `TestForget`（memory/all 双 scope、计数、owner 隔离、未知 scope 回退、缺 store 计 0） |
| `tests/app/test_forget_memory.py` | **新增**：编排委派 / 成功落审计 / 无审计静默 / memory=None 零结果不审计 / 失败落审计后抛 |
| `tests/app/test_memory_assembler.py` | 扩展：`TestProfileInjection`（max_facts=0 不注入 / 渲染 / 截断 / 空画像不注入 / 排 L2 后 L1 前 / 故障降级） |
| `tests/api/test_memory.py` | **新增**：鉴权门 401 / 画像查询 / forget 计数 + owner 隔离 / 默认 scope / 非法 scope 422 / memory 禁用降级 |

## 3. 设计决策

- **D1 L3 独立表按 `owner_id`，不挂 task**：画像是「跨 task 的稳定偏好」，故 `profiles` 表主键为
  `owner_id`、无 task 外键——与 L2 摘要 / L4 固化水位（挂 task）正交。删 task 不连带删画像。

- **D2 起步只支持显式偏好声明 + 系统配置（§14.5）**：本步只落地 L3 的**存储 / 读写 / 注入 / 遗忘**接缝，
  `update_profile` 由调用方显式写入；**自动从 L4 已验证事实聚合 L3**、**Agent 工具主动写画像**
  按设计推迟。`get_profile`/`update_profile` 即聚合落地后的稳定下游接口。

- **D3 `forget` 双 scope，区分「记忆」与「全部」**：`scope="memory"` 只清**派生记忆**
  （L2/L3/L4 + 固化水位），保留 L1 原始 task（用户的对话历史仍在，只是不再有长期沉淀）；
  `scope="all"` 在此基础上**级联删 L1 task**（遍历 `list_for_owner` + `delete`），真正清空。
  未知 scope 归一化回退到 `"memory"`（保守：不误删 task）。`TestForget` 双档守护。

- **D4 forget = 物理删除（区别于 S-030c 的逻辑遗忘）**：S-030c 的冲突/过期是「召回时过滤、记录留库」；
  被遗忘权要求**真正抹除**，故 4 个 Store 统一加 `delete_owner` 做物理 DELETE / Chroma 批删。
  两种「遗忘」语义不同、并存：S-030c 是「记忆演化」，S-030d 是「用户主张删除」。

- **D5 审计只记计数、不回存内容**：`ForgetMemoryUseCase` 落审计的 `extra_json` 只含
  `scope` + 各层删除条数 + `total_deleted`，**绝不写入被删的画像/事实文本**——审计本身也守数据最小化
  （否则「删除日志」反而成了泄露面）。`test_success_records_audit` 守护字段。

- **D6 失败也落审计再抛**：`memory.forget` 抛异常时，先记一条 `success=False` + `error` 审计，
  再向上抛由 API 层映射错误——保证「删除尝试」无论成败都留痕（合规可追溯）。
  `test_failure_records_audit_then_raises` 守护。

- **D7 L3 注入优先级最低（L4>L2>L3>L1），整块丢弃不挤占 L1**：画像稳定但价值密度低于即时召回事实
  与本 task 摘要，故排在 L4/L2 之后、L1 原文之前。预算耗尽时**整块丢弃**（不做行级截断造成半截画像），
  且永不破坏「L1 至少保留 1 条」的保底。`TestProfileInjection` 守护顺序与降级。

- **D8 `delete_owner` 统一加到 4 个 Store Port**：被遗忘权需要「一次调用清干净」，故把 owner 维度删除
  抽象进 `SummaryStorePort` / `FactStorePort` / `ConsolidationStatePort` / `ProfileStorePort` 统一契约，
  `forget` 只编排、不关心各后端细节；缺某个 store（该层禁用）时该层计 0、不报错。

- **D9 API owner 隔离 + memory 禁用优雅降级 200**：`/memory/profile` 与 `/memory/forget` 均过
  `require_owner`，只能读/删**自己**；`container.memory is None`（记忆全关）时
  profile 返回空画像、forget 返回零计数，**均 200**——不对外暴露内部装配状态。
  `TestDegradation` 守护。

## 4. 配置项

| 配置 | 默认 | 作用 |
|---|---|---|
| `memory_profile_enabled` | `True` | False → 不装配 profile_store，L3 退化（get 返回空 / update 空操作） |
| `memory_profile_max_facts` | `8` | 注入画像的最大偏好条数；0 关闭 L3 注入（assembler 不渲染画像块） |

## 5. 注入文本格式

```
【相关长期记忆（你已知的用户事实，仅供参考）】
- 用户在跨境电商行业

【对话摘要（更早内容已压缩）】
用户先前咨询了数据出境安全评估的触发条件……

【用户画像（稳定偏好，跨会话）】
- 语言：中文
- 行业：跨境电商

【历史对话（仅供参考，避免重复已回答内容）】
用户：最近一轮问题
助手：最近一轮回答
```

> 预算优先级：**L4 事实 > L2 摘要 > L3 画像 > L1 原文**（L1 始终保留 ≥1 条；L3 整块丢弃不行级截断）。

## 6. 验收对照

| 验收项 | 守护测试 |
|---|---|
| 1. L3 无 store → 空画像 / update 空操作 | `TestL3Profile::test_no_store_*` |
| 2. L3 update 浅合并、空输入空操作 | `TestL3Profile::test_update_then_get_merges` / `test_update_empty_is_noop` |
| 3. forget memory scope 清派生、保留 task | `TestForget::test_memory_scope_clears_derived_keeps_tasks` |
| 4. forget all scope 级联删 task | `TestForget::test_all_scope_also_deletes_tasks` |
| 5. forget owner 隔离 | `TestForget::test_owner_isolation` |
| 6. 未知 scope 回退 memory | `TestForget::test_unknown_scope_falls_back_to_memory` |
| 7. 缺 store 各层计 0 | `TestForget::test_missing_stores_count_zero` |
| 8. ForgetUseCase 委派 + 成功落审计（计数） | `test_delegates_and_returns_counts` / `test_success_records_audit` |
| 9. memory=None 零结果不审计 / 无审计静默 | `test_memory_none_returns_zero_no_audit` / `test_no_audit_log_is_silent` |
| 10. forget 失败落审计后抛 | `test_failure_records_audit_then_raises` |
| 11. L3 注入：max_facts=0 不注入 / 渲染 / 截断 / 排 L2 后 L1 前 / 降级 | `TestProfileInjection`（test_memory_assembler.py） |
| 12. SQLite 画像 get/upsert/覆盖/隔离/delete_owner | `tests/infra/test_sqlite_profile_store.py` |
| 13. API 鉴权 401 / 画像查询 / forget 计数 + 隔离 / 非法 scope 422 / 禁用降级 | `tests/api/test_memory.py` |
| 14. 本文档 | 即本文件 |

## 7. 验证结果

- 全量：**738 passed / 1 skipped**（S-030c 基线 702，+36）。
- `ruff check` 全部 changed 文件：All checks passed（`reranker.py` / `sqlite_task_repo.py` 等既有遗留非本步引入）。
- `import main` OK，`profile_store` / `forget_memory` 正常装配，`/api/v2/memory/*` 路由正常挂载。

## 8. 不在本步范围（后续）

- **自动从 L4 已验证事实聚合 L3 画像**（§14.5：起步只显式声明 + 系统配置）。
- **Agent 工具主动写画像**（让模型在对话中自助更新用户偏好）。
- **task-close 触发固化接线**：`memory_consolidate_on_task_close` 配置仍就位，触发点接线沿用 S-030c 推迟。

接缝已留：`ProfileStorePort` 为 L3 后端替换的统一抽象，`get_profile`/`update_profile` 为自动聚合落地后的稳定下游；
`delete_owner` 统一契约支撑被遗忘权的「一次清干净」；`MemoryAssembler.assemble()` 仍是各层汇聚 + 预算裁剪的唯一出口；
`ForgetMemoryUseCase` 是「删除 + 审计」的单一入口，后续接 task-close / 账户注销可直接复用。

---

至此分层记忆系统四步（L1 短期 → L2 摘要 → L4 语义固化 → L3 画像 + 被遗忘权）收官：
Copilot 具备**即时上下文、会话压缩、跨会话长期事实、稳定偏好画像**四级记忆，
并通过**主动遗忘 + 审计**满足 PIPL 被遗忘权与可追溯合规底线。
