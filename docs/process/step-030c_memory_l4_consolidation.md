# S-030c — L4 语义事实固化管线（提取-验证-巩固）+ 写入门控 + 冲突遗忘

> 分层记忆系统第三步，也是「记忆」最核心的一步。设计全貌见 `docs/design/memory_system.md`
> （§3 固化管线、§14.3 写入门控、§14.4 调度策略）。本步在 S-030a/b 的 L1/L2 之上补
> **L4 语义事实层（跨 task / 跨设备、按 `owner_id` 可召回）**，落地「提取 → 验证 → 巩固」三段式、
> **规则+LLM 混合写入门控**、**冲突遗忘（去重强化 / 取代）**、**容量衰减淘汰** 与 **召回注入**。
> 明确不实现 L3 用户画像聚合、主动遗忘 `forget()` 与审计（留待 S-030d）。

## 1. 本步骤目标

让 Copilot 从「只记得当前 task」升级为「跨会话记住用户的长期稳定事实」：

- **提取**：回复后 fork 后台作业，规则预过滤选出「值得送检」的对话片段 → 轻量 LLM 提炼候选事实。
  口诀：**规则管「要不要送检」，LLM 管「如何表达」，验证器管「能不能入库」**；
- **验证（四关）**：接地（grounded）/ 显著性（salience 阈值）/ 去重（近邻相似度 ≥ dedup → 强化置信）/
  冲突（相似度落 `[conflict, dedup)` → 旧事实标 superseded）；
- **巩固**：通过验证的候选 embed + 落库（独立 Chroma collection `memory_facts`，余弦）；
- **冲突遗忘**：去重→强化、冲突→取代、低于冲突→新增；被取代/过期事实**逻辑删除，永不召回**；
- **容量遗忘**：超 `owner` 容量上限时，按 **衰减分** `salience·e^(-λ·age)` 淘汰最低分；
- **召回**：每轮用当前问题语义召回 top-k 长期事实，注入 prompt（最优先占预算）；
- **幂等**：`ConsolidationState.msg_watermark` 记「已固化到第几条」，重试不重复写、漏固下轮自愈；
- **隔离**：所有 L4 读写继续做 `owner_id` 归属校验 / where 过滤，跨用户绝不泄露（合规底线）。

## 2. 修改 / 新增文件

| 文件 | 说明 |
|---|---|
| `config.py` | 新增 L4 配置块：`memory_consolidation_enabled` / `memory_consolidation_min_backlog`(30) / `memory_consolidate_on_task_close` / `memory_l4_ttl_days`(365) / `memory_decay_lambda`(0.01) / `memory_fact_cap_per_owner`(500) / `memory_fact_recall_k`(3) / `memory_fact_salience_threshold`(0.5) / `memory_fact_dedup_threshold`(0.88) / `memory_fact_conflict_threshold`(0.72) |
| `domain/models.py` | `Fact` 扩展固化字段（confidence/salience/last_used_at/superseded_by/source_episode）；**新增** `ConsolidationState`（task_id/owner_id/msg_watermark/updated_at） |
| `domain/ports.py` | **新增** `FactStorePort`、`ConsolidationStatePort`；`MemoryJobSchedulerPort` 补 `schedule_consolidation` |
| `infra/storage/_db.py` | **新增** `consolidation_state` 表（PK task_id，FK→tasks ON DELETE CASCADE，+ owner 索引） |
| `infra/storage/sqlite_consolidation_state.py` | **新增**：`SqliteConsolidationStateStore`——`get`(带 owner 校验)/`upsert`(ON CONFLICT 更新) |
| `infra/storage/__init__.py` | 导出 `SqliteConsolidationStateStore` |
| `infra/memory/fact_store.py` | **新增**：`ChromaFactStore`——独立 `memory_facts` collection（余弦），owner where 隔离，add/query/get/mark_superseded/list_owner/delete/count |
| `infra/memory/consolidation.py` | **新增**：`ConsolidationWorker`——提取-验证-巩固三段式 + 冲突遗忘 + 容量淘汰 + watermark 幂等 |
| `infra/memory/task_memory.py` | `TaskBackedMemory` 补 L4 `recall_semantic`（fact_store + embedder + TTL/superseded 过滤）；L3 仍占位 |
| `infra/memory/scheduler.py` | `ThreadPoolMemoryScheduler` 补 `schedule_consolidation`（注入 worker，后台吞错） |
| `infra/memory/__init__.py` | 导出 `ChromaFactStore`、`ConsolidationWorker` |
| `app/memory/assembler.py` | 汇聚补 L4：`assemble(query=...)`，事实块**最优先**占预算，召回独立降级 |
| `app/agent/copilot.py` | `_memory_block(query=user_message)`——把当前问题传给装配器做 L4 语义召回 |
| `app/use_cases/run_copilot.py` | qa 收尾 `_schedule_memory` 追加 `schedule_consolidation` |
| `app/factories.py` | 新增 `build_fact_store` / `build_consolidation_state_store` / `build_consolidation_worker`；`build_memory` 注入 fact_store+embedder+l4_ttl；`build_memory_scheduler` 注入 worker |
| `app/container.py` | 装配 `fact_store` / `consolidation_state_store` / `consolidation_worker`，共享给 memory 与 scheduler；assembler 传 `recall_k` |
| `tests/fakes/fake_fact_store.py` | **新增**：`FakeFactStore`（内存余弦近邻 + owner 隔离）、`FakeConsolidationStateStore` |
| `tests/fakes/fake_memory.py` | `FakeMemory.recall_semantic` 改为可预置事实返回（不再抛 NotImplementedError） |
| `tests/infra/test_consolidation.py` | **新增**：门控/提取新增/显著性关/接地关/去重强化/冲突取代/watermark 幂等/容量淘汰/owner 隔离/提取失败保留 watermark |
| `tests/infra/test_sqlite_consolidation_state.py` | **新增**：get/upsert/冲突更新/owner 隔离 |
| `tests/infra/test_task_memory.py` | 扩展：L4 `recall_semantic`（无 store 空/空 query/owner 命中/superseded 过滤/TTL 过滤/owner 隔离） |
| `tests/infra/test_memory_scheduler.py` | 扩展：`schedule_consolidation` 提交执行 / 无 worker 空操作 / 后台吞错 |
| `tests/app/test_memory_assembler.py` | 扩展：L4 事实注入 / recall_k=0 或空 query 不召回 / 事实排最前 / 召回故障降级 |
| `tests/app/test_run_copilot.py` | 扩展：qa 收尾触发 `schedule_consolidation` |

## 3. 设计决策

- **D1 写入门控 = 规则 + LLM 混合**：规则预过滤（`_rule_prefilter`）只把 user/assistant 的实质内容
  （滤掉 tool/system/过短消息）送检；LLM 负责「如何表达」成结构化候选；验证器（四关）裁决「能不能入库」。
  三者职责正交，避免 LLM 既当运动员又当裁判（`TestExtractAndValidate` 守护）。

- **D2 冲突遗忘三分支（确定性，可测）**：候选 embed 后取最近邻 active 事实，按相似度分档：
  `sim ≥ dedup(0.88)` → **去重强化**（不新增，旧事实置信度 +0.2、刷新 last_used）；
  `conflict(0.72) ≤ sim < dedup` → **冲突取代**（旧事实标 `superseded_by` 新事实，新事实入库）；
  `sim < conflict` → **新增**（首次提取 tentative 低置信 0.5）。
  `TestConflictForgetting` 用受控向量精确覆盖三档。

- **D3 接地关 + 显著性关**：LLM 标 `grounded:false`（无对话证据）的候选直接丢弃（防幻觉记忆）；
  `salience < 阈值(0.5)` 的候选不固化（防低价值污染）。两关都在 embed 之前/之中早退
  （`test_ungrounded_dropped` / `test_low_salience_dropped` 守护）。

- **D4 逻辑遗忘 = 召回时过滤，不物理删**：`recall_semantic` 过滤 `superseded_by != None`（被取代）
  与 `created_at` 超 L4 TTL 的事实，**永不召回**；但记录仍在库（可审计）。
  被取代的旧事实保留并标记，而非删除（`test_conflict_supersedes_old_and_adds_new` 守护：旧的 count 仍在、active 只剩新的）。

- **D5 容量遗忘 = 衰减分淘汰**：`owner` 的 active 事实数超 `fact_cap(500)` 时，按衰减分
  `score = salience · e^(-λ·age_days)`（λ=0.01）升序淘汰最低分。低显著性 + 久未刷新的事实先走
  （`test_capacity_eviction_drops_lowest_decay` 守护）。

- **D6 L4 独立 Chroma collection，复用 RAG 向量栈**：`memory_facts` 与 KB 的 `rag_knowledge_base`
  物理隔离，余弦空间。向量由调用方用 `EmbedPort` 算好传入，`ChromaFactStore` 只管存取 + owner where 隔离。
  Chroma metadata 仅接受标量：`tags` 序列化 JSON、`superseded_by` 空串表 None。

- **D7 fork 异步显式调度，复用 `MemoryJobSchedulerPort`**：固化作业经
  `ThreadPoolMemoryScheduler.schedule_consolidation` 丢后台守护线程，best-effort 吞错，**绝不阻塞主回复**。
  与 L2 摘要同一调度入口、同一线程池（§14.1 已批准：不引 Celery）。

- **D8 每轮调度但 `MIN_BACKLOG=30` 门控**：qa 收尾每轮都 `schedule_consolidation`，但 worker 内
  `len(backlog) < min_backlog → 返回`，避免高频小批固化。§14.4 的 `CONSOLIDATE_ON_TASK_CLOSE`
  作为配置项已落地（`memory_consolidate_on_task_close`），task-close 触发点的接线推迟到后续（文档注明）。

- **D9 渐进置信 + watermark 幂等**：首次提取低置信（tentative 0.5），反复印证经去重分支强化（reinforcement）。
  `ConsolidationState.msg_watermark` 与 L2 同构：`backlog = msgs[watermark:]`，固化成功后推进；
  提取失败（LLM 返回非法 JSON）**保留 watermark 待重试**，不推进（`test_extract_failure_keeps_watermark` 守护）；
  重跑 backlog 已空则空操作（`test_watermark_idempotent` 守护）。

## 4. 配置项

| 配置 | 默认 | 作用 |
|---|---|---|
| `memory_consolidation_enabled` | `True` | False → 不装配 fact_store/state_store/worker，退回纯 L1+L2 |
| `memory_consolidation_min_backlog` | `30` | 未固化消息数 ≥ 此值才跑一次固化 |
| `memory_consolidate_on_task_close` | `True` | task 关闭触发固化（接线后续；当前每轮调度 + backlog 门控） |
| `memory_l4_ttl_days` | `365` | L4 事实 TTL（天）；0 关闭。过期事实不召回 |
| `memory_decay_lambda` | `0.01` | 衰减分 `e^(-λ·age_days)` 的 λ，容量淘汰用 |
| `memory_fact_cap_per_owner` | `500` | 单 owner active 事实上限，超出按衰减分淘汰 |
| `memory_fact_recall_k` | `3` | 每轮召回注入的 top-k 事实数；0 关闭召回 |
| `memory_fact_salience_threshold` | `0.5` | 显著性写入门控，低于不固化 |
| `memory_fact_dedup_threshold` | `0.88` | 去重相似度阈值，≥ 则强化既有事实 |
| `memory_fact_conflict_threshold` | `0.72` | 冲突相似度下界，`[conflict, dedup)` 为冲突取代 |

## 5. 注入文本格式

```
【相关长期记忆（你已知的用户事实，仅供参考）】
- 用户在跨境电商行业
- 用户偏好中文回答

【对话摘要（更早内容已压缩）】
用户先前咨询了数据出境安全评估的触发条件……

【历史对话（仅供参考，避免重复已回答内容）】
用户：最近一轮问题
助手：最近一轮回答
```

> 预算优先级：**L4 事实 > L2 摘要 > L1 原文**（事实价值密度最高、跨会话稳定；L1 始终保留 ≥1 条）。

## 6. 验收对照

| 验收项 | 守护测试 |
|---|---|
| 1. backlog 不足 / owner 不符 → 空操作 | `TestGating::test_below_min_backlog_noop` / `test_owner_mismatch_noop` |
| 2. 提取失败保留 watermark 待重试 | `test_extract_failure_keeps_watermark` |
| 3. 接地 + 高显著性候选 → 新增（tentative 置信） | `test_adds_new_grounded_salient_fact` |
| 4. 显著性关 / 接地关丢弃 | `test_low_salience_dropped` / `test_ungrounded_dropped` |
| 5. 去重 → 强化不新增 | `test_dedup_reinforces_not_adds` |
| 6. 冲突 → 旧标 superseded、新入库 | `test_conflict_supersedes_old_and_adds_new` |
| 7. watermark 幂等不重复固化 | `test_watermark_idempotent` |
| 8. 容量超限按衰减分淘汰 | `test_capacity_eviction_drops_lowest_decay` |
| 9. L4 召回：owner 命中 / superseded / TTL / 隔离 | `TestL4RecallSemantic`（test_task_memory.py） |
| 10. L4 事实注入 + 排最前 + 召回故障降级 | `TestFactInjection`（test_memory_assembler.py） |
| 11. 调度器 `schedule_consolidation` 提交/吞错/无 worker | `TestScheduleConsolidation`（test_memory_scheduler.py） |
| 12. qa 收尾触发固化调度 | `test_qa_schedules_consolidation`（test_run_copilot.py） |
| 13. SQLite 固化水位 get/upsert/隔离 | `tests/infra/test_sqlite_consolidation_state.py` |
| 14. 本文档 | 即本文件 |

## 7. 验证结果

- 全量：**702 passed / 1 skipped**（S-030b 基线 673，+29）。
- `ruff check` 全部 changed 文件：All checks passed（`sqlite_task_repo.py` 的 SIM118 为既有遗留，非本步引入）。
- `import main` OK，`fact_store` / `consolidation_worker` / `memory_scheduler` 正常装配，v2 路由正常挂载。

## 8. 不在本步范围（后续）

- **L3 用户画像聚合（只从 L4 已验证事实聚合，不直接抽原始对话）** → S-030d
  （§14.5：起步只支持显式偏好声明 + 系统配置偏好）。
- **主动遗忘 `forget()` + 记忆审计** → S-030d。
- **task-close 触发点接线**：`memory_consolidate_on_task_close` 配置已就位，触发点接线推迟。

接缝已留：`FactStorePort` / `ConsolidationStatePort` 为后续替换存储后端的统一抽象；
`ConsolidationWorker` 的提取-验证-巩固骨架即 L3 聚合的上游数据源；
`MemoryAssembler.assemble()` 仍是各层汇聚 + 预算裁剪的唯一出口。
