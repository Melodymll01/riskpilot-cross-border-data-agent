# S-031a — 每用户记忆设置（双开关）+ 注入门控 + 管理读接口

> 记忆系统第五步（用户控制面 · 后端）。在 S-030a/b/c/d 把四级记忆 + 被遗忘权落地之后，本步把
> 「记忆要不要参考」交还给用户：ChatGPT 式两个开关（参考保存的记忆 / 参考会话上下文），
> 持久化为**每用户偏好**，在记忆装配的唯一出口做注入门控；同时补一个「当前生效长期事实清单」
> 只读接口供前端管理面板渲染。明确不实现前端 UI（拆到 S-031b）、单条事实删除（推迟 v1.1）。

## 1. 本步骤目标

让用户能在设置里自主控制记忆注入，并能查看自己被系统记住了什么：

- **双开关持久化**：按 `owner_id` 存两个布尔偏好，缺省双开（向后兼容 + 与全局 `memory_enabled` 默认开一致）：
  - `use_saved_memory`（参考保存的记忆）→ 门控 **L4 长期事实 + L3 用户画像**；
  - `reference_history`（参考会话上下文）→ 门控 **L1 最近原文 + L2 摘要**；
- **注入门控**：开关在 `MemoryAssembler.assemble()`（各层汇聚的唯一出口）生效，关掉只影响「注入与否」，
  **不删除**已存记忆（删除走 S-030d 的 `forget`）；
- **读写 + 审计**：`MemorySettingsUseCase` 暴露 `get`（缺省双开）/ `update`（部分更新），
  并把开关变更落 `AuditLogPort`——记忆开关本质是**用户同意状态变更**（PIPL §14 撤回/重授）；
- **长期事实清单**：`MemoryPort.list_facts(owner_id)` 列出**当前生效**（剔除 superseded / 过 TTL）的事实，
  供管理面板展示「系统记住了你哪些事实」；
- **API**：`GET/PUT /memory/settings`（读/改开关）+ `GET /memory/facts`（事实清单 + 容量上限），全程 owner 隔离；
- **降级**：记忆全局禁用（`container.memory is None`）时 settings 返回默认、facts 返回空，均 200；
  开关读取异常 **fail-open**（退回双开），保持默认行为不被偶发故障静默关闭。

## 2. 修改 / 新增文件

| 文件 | 说明 |
|---|---|
| `domain/models.py` | **新增** `MemorySettings`（owner_id / use_saved_memory=True / reference_history=True / updated_at）；`AuditAction` 补 `MEMORY_SETTINGS_UPDATE` |
| `domain/ports.py` | **新增** `MemorySettingsStorePort`（get/upsert）；`MemoryPort` 补 `list_facts` |
| `infra/storage/_db.py` | **新增** `memory_settings` 表（PK owner_id，use_saved_memory/reference_history INTEGER DEFAULT 1，updated_at REAL） |
| `infra/storage/sqlite_memory_settings.py` | **新增**：`SqliteMemorySettingsStore`——get / upsert(ON CONFLICT)，布尔以 INTEGER 0/1 落盘 |
| `infra/storage/__init__.py` | 导出 `SqliteMemorySettingsStore` |
| `infra/memory/task_memory.py` | `TaskBackedMemory` **新增** `list_facts`——`fact_store.list_owner` 后过滤 superseded/TTL，按 created_at 倒序，无 store 返回空 |
| `app/memory/assembler.py` | `__init__` 加 `settings_store`；`assemble` 读开关门控 facts+profile / summary+recent；**新增** `_safe_settings`（无 store/未设/出错均双开 fail-open） |
| `app/use_cases/memory_settings.py` | **新增**：`MemorySettingsUseCase`——get（缺省双开）/ update（部分更新 + 审计），`_record_audit` 与 ForgetMemoryUseCase 同构 |
| `app/factories.py` | **新增** `build_memory_settings_store`（复用 task 连接池，只看 `memory_enabled`） |
| `app/container.py` | 装配 `memory_settings_store`，传入 `MemoryAssembler`；**新增** `memory_settings` use case |
| `api/v2/schemas.py` | **新增** `MemorySettingsResponse` / `UpdateMemorySettingsRequest`（bool\|None 部分更新）/ `MemoryFactItem` / `MemoryFactsResponse` |
| `api/v2/memory.py` | **新增** `GET/PUT /memory/settings` + `GET /memory/facts`，过 `require_owner` |
| `tests/fakes/fake_memory_settings_store.py` | **新增**：`InMemoryMemorySettingsStore` |
| `tests/fakes/fake_memory.py` | `FakeMemory` 补 `list_facts`（过滤 superseded）+ `list_facts_calls` |
| `tests/app/test_memory_settings.py` | **新增**：get 缺省双开 / 部分更新保留未传字段 / 审计字段 / 无 store 不持久化 / 无审计静默 |
| `tests/app/test_memory_assembler.py` | 扩展 `TestSettingsGating`：无 store 默认全开 / 双开 / use_saved_memory 关掉 L3+L4 / reference_history 关掉 L1+L2 / 双关返回空 / 读取异常 fail-open |
| `tests/infra/test_sqlite_memory_settings.py` | **新增**：协议契约 / get-upsert-覆盖 / 布尔 round-trip / owner 隔离 |
| `tests/infra/test_task_memory.py` | 扩展 `TestL4ListFacts`：无 store 空 / 倒序 / superseded 过滤 / TTL 过滤 / owner 隔离 |
| `tests/api/test_memory.py` | 扩展：settings 鉴权 401 / 默认双开 / PUT-GET round-trip + 部分更新 / owner 隔离；facts 列举 + 容量 + memory 禁用降级 |

## 3. 设计决策

- **D1 开关是每用户运行时偏好，独立表按 `owner_id`，不进 config**：`memory_enabled` 等是**部署级**开关
  （运维定夺），而双开关是**用户级**意愿（每个登录/匿名用户各自选择）。故新建 `memory_settings` 表
  键 `owner_id`、与 `profiles` 同构（无 task 外键），不污染全局配置面。

- **D2 开关到记忆层的映射**：`use_saved_memory`（参考保存的记忆）门控 **L3 画像 + L4 长期事实**
  （跨会话「记住的东西」）；`reference_history`（参考会话上下文）门控 **L1 最近原文 + L2 摘要**
  （本 task 上下文）。两个开关正交，组合出「全开 / 只长期 / 只上下文 / 全关」四态。

- **D3 门控放在 `MemoryAssembler`（单一汇聚出口），copilot 零改动**：各层读取 + 排版 + 预算裁剪本就
  收口于 `assemble()`，故开关也在此生效——`copilot.py` 仍只调 `assemble()`，不感知开关存在。
  开关只影响「这一层要不要读/注入」，已存记忆纹丝不动。

- **D4 缺省双开 + 读取异常 fail-open**：新用户没有设置记录 → `get` 返回默认双开（记忆默认开，与全局
  `memory_enabled=True` 一致）；`settings_store` 未配置 / 读取抛异常 → 退回双开。
  选 fail-open 而非 fail-safe：偶发 DB 故障不该静默关闭记忆造成「失忆」体验回归；用户主动关闭由
  `upsert` 持久化，正常路径精确生效。该权衡在文档与代码注释显式标注。

- **D5 诚实标签「参考会话上下文」而非「参考历史聊天记录」**：当前 L1/L2 是 **per-task**（单会话内），
  并无跨 task 的「历史聊天记录」召回。沿用 ChatGPT 文案会过度承诺，故 `reference_history` 对外表述为
  「参考会话上下文」。真正的跨会话历史召回留作后续（见 §8）。

- **D6 开关变更落审计（`MEMORY_SETTINGS_UPDATE`）**：记忆开关是用户对「数据被用于个性化」的同意状态，
  撤回/重新授予应可追溯（PIPL §14/§55）。`update` 成功即记一条审计，`extra_json` 只含两个开关最终值 +
  `persisted` 标志，**不回存其他内容**（数据最小化，与 S-030d 审计同纪律）。

- **D7 管理面板读 = 复用 `GET /profile` + 新增 `GET /facts`，单条删除推迟 v1.1**：面板要展示「系统记住了你
  什么」，画像走已有 `/memory/profile`，长期事实走新 `/memory/facts`（list + count + cap）。
  本步只做**只读**展示 + 整体清空（已有 `/memory/forget`）；**单条事实删除**需 `MemoryPort` 扩 `forget_fact`
  + Chroma 单点删 + UI 行级交互，复杂度独立，推迟 v1.1。

- **D8 `list_facts` 过滤口径与 `recall_semantic` 一致**：管理面板只应展示「此刻真实可被召回」的事实，
  故 `list_facts` 同样剔除 `superseded_by` 非空（冲突遗忘）与过 TTL 的事实，避免「面板显示但永不注入」
  的认知错位。额外按 `created_at` 倒序（新在前）便于前端直接渲染。

- **D9 API owner 隔离 + memory 禁用优雅降级 200**：三个端点均过 `require_owner`，只能读/改**自己**；
  `container.memory is None`（记忆全关）时 facts 返回空、settings 返回默认双开，**均 200**——
  不对外暴露内部装配状态（与 S-030d `TestDegradation` 同策略）。

## 4. 配置项

本步**不新增**配置项。双开关是每用户运行时偏好（存 `memory_settings` 表），非部署级配置。
管理面板的容量提示复用既有 `memory_fact_cap_per_owner`（默认 500，S-030c 引入）作为 `GET /facts` 的 `cap`。

## 5. 注入门控语义

| `use_saved_memory` | `reference_history` | 注入内容 |
|---|---|---|
| ✅ | ✅ | L4 事实 + L2 摘要 + L3 画像 + L1 原文（全开，等价 S-030d 行为） |
| ❌ | ✅ | 仅 L2 摘要 + L1 原文（只参考本会话上下文） |
| ✅ | ❌ | 仅 L4 事实 + L3 画像（只参考长期记忆） |
| ❌ | ❌ | 空串（assemble 返回 ""，等价无记忆） |

> 门控只决定「注入与否」；底层记忆数据不受影响。删除走 `POST /memory/forget`（S-030d）。

## 6. 验收对照

| 验收项 | 守护测试 |
|---|---|
| 1. SQLite 开关 get/upsert/覆盖/布尔 round-trip/owner 隔离 | `tests/infra/test_sqlite_memory_settings.py` |
| 2. UseCase get 缺省双开（无 store / 未设置） | `TestGet::test_no_store_*` / `test_unset_owner_*` |
| 3. UseCase 部分更新保留未传字段 | `TestUpdate::test_partial_update_keeps_unset_field` |
| 4. UseCase 更新落审计（字段 + persisted） | `TestUpdate::test_update_records_audit` |
| 5. UseCase 无 store 不持久化 / 无审计静默 | `test_no_store_update_is_silent_not_persisted` / `test_no_audit_log_is_silent` |
| 6. 装配门控四态 + 无 store 默认全开 | `TestSettingsGating::test_*` |
| 7. 装配开关读取异常 fail-open | `TestSettingsGating::test_settings_read_failure_fails_open` |
| 8. `list_facts` 无 store 空 / 倒序 / superseded 过滤 / TTL 过滤 / owner 隔离 | `TestL4ListFacts`（test_task_memory.py） |
| 9. API settings 鉴权 401 / 默认双开 / round-trip / owner 隔离 | `TestSettingsAuthGating` / `TestSettings` |
| 10. API facts 列举 + 容量 / memory 禁用降级 | `TestFacts` |
| 11. 本文档 | 即本文件 |

## 7. 验证结果

- 全量：**771 passed / 1 skipped**（S-030d 基线 738，+33）。
- `ruff check` 全部 changed 文件：All checks passed（`ports.py` 导入排序 `--fix` 自动归位）。
- `import main` OK，`memory_settings` use case / `memory_settings_store` 正常装配，`/api/v2/memory/*` 路由正常挂载。

## 8. 不在本步范围（后续）

- **S-031b 前端**：设置弹窗 + 两个 toggle + 管理面板（画像 + 长期事实清单 + 清空按钮），
  对接本步 `GET/PUT /memory/settings` + `GET /memory/facts` + 既有 `/memory/profile` + `/memory/forget`。
- **单条事实删除（v1.1）**：`MemoryPort.forget_fact` + Chroma 单点删 + 面板行级删除按钮。
- **真正的跨会话历史召回**：让「参考会话上下文」升级为「参考历史聊天记录」（跨 task L1/L2 检索），
  消除 D5 的诚实标签折中。
- **L4→L3 自动聚合 / Agent 工具写画像 / task-close 触发固化接线**（沿用 S-030d 推迟项）。

接缝已留：`MemorySettingsStorePort` 为开关后端替换的统一抽象；`MemorySettingsUseCase` 是「读写 + 同意审计」
的单一入口；`MemoryAssembler.assemble()` 仍是各层汇聚 + 预算 + **门控**的唯一出口；
`MemoryPort.list_facts` 为管理面板（及后续单条删除）的稳定读侧。
