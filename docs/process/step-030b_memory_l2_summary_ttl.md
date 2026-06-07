# S-030b — L2 滚动摘要 + TTL 逻辑遗忘 + 异步调度

> 分层记忆系统第二步。设计全貌见 `docs/design/memory_system.md`（§14 已批准决议）。
> 本步在 S-030a 的 L1 之上补 **L2（滚动摘要）**、**TTL（逻辑遗忘）** 与
> **回复后异步调度摘要作业**；明确不实现 L3/L4，也不实现固化（提取-验证-巩固）管线。

## 1. 本步骤目标

让 Copilot 从「只带当前 task 最近 N 条原文」升级为「更早内容被压缩成摘要、过期记忆自动失效」：

- **摘要**：未摘要消息数达阈值时，LLM **增量精炼**出滚动摘要（旧摘要 + 新增对话 → 新摘要）；
- **注入**：摘要块与最近原文一起进 prompt，摘要优先吃预算；
- **遗忘**：L1 原文、L2 摘要均带 TTL，**读取时过滤过期项**，过期永不注入；
- **幂等**：靠 `msg_watermark` 记录「已摘要到第几条」，重试不重复、漏摘下轮自愈；
- **异步**：摘要作业放线程池后台跑，**绝不阻塞主回复**、绝不让异常冒泡到请求线程；
- **隔离**：所有 L2 读写继续做 `owner_id` 归属校验，跨用户绝不泄露。

## 2. 修改 / 新增文件

| 文件 | 说明 |
|---|---|
| `config.py` | 新增 `memory_summary_enabled` / `memory_summary_threshold`(默认 20) / `memory_l1_ttl_days`(默认 30) / `memory_l2_ttl_days`(默认 180) |
| `domain/models.py` | **新增** `TaskSummary`（task_id / owner_id / summary / msg_watermark / updated_at） |
| `domain/ports.py` | `MemoryPort.get_summary`/`maybe_summarize` 补 `owner_id`；**新增** `SummaryStorePort`、`MemoryJobSchedulerPort` |
| `infra/storage/_db.py` | **新增** `task_summaries` 表（PK task_id，FK→tasks ON DELETE CASCADE，+ owner 索引） |
| `infra/storage/sqlite_summary_store.py` | **新增**：`SqliteSummaryStore`——`get`(带 owner 校验) / `upsert`(ON CONFLICT 更新) |
| `infra/storage/__init__.py` | 导出 `SqliteSummaryStore` |
| `infra/memory/task_memory.py` | `TaskBackedMemory` 补 L2（`get_summary`/`maybe_summarize`/增量精炼）+ TTL 过滤 |
| `infra/memory/scheduler.py` | **新增**：`ThreadPoolMemoryScheduler`——`MemoryJobSchedulerPort` 线程池实现 |
| `infra/memory/__init__.py` | 导出 `ThreadPoolMemoryScheduler` |
| `app/memory/assembler.py` | 汇聚 L2 摘要 + L1 原文：摘要优先吃预算，剩余从最新往旧填；摘要读取独立降级 |
| `app/use_cases/run_copilot.py` | qa 分支 `finally` 调度 L2 摘要（断连 GeneratorExit 也不漏跑） |
| `app/factories.py` | 新增 `build_summary_store` / `build_memory_scheduler`；`build_memory` 注入 store + chat + TTL |
| `app/container.py` | 装配 `self.memory_scheduler`，传入 `RunCopilotUseCase` |
| `main.py` | lifespan 关闭期优雅 `memory_scheduler.shutdown(wait=False)` |
| `tests/fakes/fake_summary_store.py` | **新增**：`InMemorySummaryStore`（带 owner 校验） |
| `tests/infra/test_sqlite_summary_store.py` | **新增**：get/upsert/冲突更新/owner 隔离 |
| `tests/infra/test_memory_scheduler.py` | **新增**：提交执行 / 后台吞错 / 关闭后空操作 |
| `tests/infra/test_task_memory.py` | 扩展：L2 阈值门控 / watermark 幂等 / 增量精炼 / owner 隔离 + L1/L2 TTL |
| `tests/app/test_memory_assembler.py` | 扩展：摘要注入 / 仅摘要 / 摘要优先预算 / 摘要故障降级 |
| `tests/app/test_run_copilot.py` | 扩展：qa 收尾触发调度 / 调度故障不影响回复 / 无调度器空操作 |
| `tests/app/agent/test_copilot_memory.py` | 历史时间戳锚到「现在」，避免默认 30 天 TTL 误删 |

## 3. 设计决策

- **D1 L2 摘要独立 `task_summaries` 表，不挂 task 行**：摘要是「派生、可重建、可遗忘」的二级数据，
  与一级事实分离。`SummaryStorePort` 单独抽象，便于后续替换存储后端，FK→tasks 级联删除保证一致。

- **D2 `get_summary`/`maybe_summarize` 进 owner_id，越权返回空/空操作**：与 L1 同构的隔离语义。
  适配器内 `repo.get(task_id, owner_id) is None → 直接返回`，跨用户读摘要拿到 `None`
  （`test_other_owner_summary_not_leaked` 守护）。

- **D3 增量精炼（incremental refine），不做 map-reduce**：`_refine_summary(旧摘要, 新增消息) → 新摘要`，
  每轮成本 **O(1)**（只看 backlog，不重扫全历史）。摘要 system prompt 要求「融合旧摘要 + 新增对话」。

- **D4 `msg_watermark` 幂等水位**：记录「已摘要到第几条消息」。`backlog = msgs[watermark:]`，
  不足阈值则等下轮；摘要成功后 `watermark += len(backlog)`。重试不重复摘要、漏摘下轮按差额自愈
  （`test_watermark_idempotent_no_double_summary` 守护）。watermark 越界（消息被删/重置）时夹紧防负切片。

- **D5 TTL 逻辑遗忘 = 读取时过滤，不物理删**：`recent_messages` 过滤 `created_at < now-ttl` 的旧原文；
  `get_summary` 对 `updated_at` 过期的摘要返回 `None`。`ttl_days<=0` 表示关闭过滤。
  过期数据仍在库里（可审计），但**永不注入**（`TestL1Ttl` / `TestL2Ttl` 守护）。

- **D6 摘要作业异步上线程池，不阻塞主回复**：`ThreadPoolMemoryScheduler` 用守护命名线程池，
  `schedule_summarization` 只 `submit`，作业体 `_run` 全程 try/except 吞错（best-effort）。
  SQLite 连接池线程局部，后台线程拿自己的连接，安全。

- **D7 调度发生在 `finally`，而非生成器尾部**：qa 分支 `try: yield from agent.run(...) finally: _schedule_memory(...)`。
  放 `finally` 而非生成器最后一行——SSE 客户端提前断连触发 `GeneratorExit` 时仍会调度，**不漏跑**
  （`test_qa_schedules_summarization` / `test_scheduler_failure_does_not_break_stream` 守护）。

- **D8 装配预算：摘要优先，原文填剩余**：`MemoryAssembler._render` 先让 L2 摘要吃预算
  （超额截断摘要正文），再用剩余预算从最新往最旧填 L1 原文。摘要是价值密度更高的长期压缩信息，
  优先保留（`test_summary_takes_budget_priority` 守护）。摘要读取独立 try/except，
  摘要故障不影响最近原文注入（`test_summary_failure_degrades_to_recent_only` 守护）。

## 4. 配置项

| 配置 | 默认 | 作用 |
|---|---|---|
| `memory_summary_enabled` | `True` | False → 不装配 store/scheduler，退回纯 L1 |
| `memory_summary_threshold` | `20` | 未摘要消息数 ≥ 此值才触发一次增量摘要 |
| `memory_l1_ttl_days` | `30` | L1 原文 TTL（天）；0 关闭过滤。早于此的消息不注入 |
| `memory_l2_ttl_days` | `180` | L2 摘要 TTL（天）；0 关闭过滤。过期摘要不注入 |

## 5. 注入文本格式

```
【对话摘要（更早内容已压缩）】
用户先前咨询了数据出境安全评估的触发条件与申报路径……

【历史对话（仅供参考，避免重复已回答内容）】
用户：最近一轮问题
助手：最近一轮回答
```

## 6. 验收对照

| 验收项 | 守护测试 |
|---|---|
| 1. 达阈值触发增量摘要 | `test_summarizes_when_backlog_reaches_threshold` |
| 2. 未达阈值不触发 LLM | `test_below_threshold_is_noop` |
| 3. watermark 幂等不重复摘要 | `test_watermark_idempotent_no_double_summary` |
| 4. 增量精炼带上旧摘要 | `test_incremental_refine_includes_old_summary` |
| 5. owner 隔离（摘要不泄露） | `test_other_owner_summary_not_leaked` / `TestOwnerIsolation` |
| 6. L1/L2 TTL 过滤过期项 | `TestL1Ttl` / `TestL2Ttl` |
| 7. 摘要 + 原文一起注入、摘要优先预算 | `TestSummaryInjection` |
| 8. 回复后异步调度（断连不漏） | `test_qa_schedules_summarization` / `test_scheduler_failure_does_not_break_stream` |
| 9. SQLite 存储 get/upsert/隔离 | `tests/infra/test_sqlite_summary_store.py` |
| 10. 调度器提交/吞错/关闭 | `tests/infra/test_memory_scheduler.py` |
| 11. 本文档 | 即本文件 |

## 7. 验证结果

- 全量：**673 passed / 1 skipped**（S-030a 基线 648，+25）。
- `ruff check` 全部 changed 文件：All checks passed。
- `import main` OK，`memory_scheduler` 装配为 `ThreadPoolMemoryScheduler`，v2 路由正常挂载。

## 8. 不在本步范围（后续）

- **L4 语义事实固化管线（提取-验证-巩固，fork 异步）** → S-030c
  （§14.1 已批准：复用 `MemoryJobSchedulerPort` 显式调度，不引 Celery；
  §14.4：`MEMORY_CONSOLIDATION_MIN_BACKLOG=30`、`MEMORY_CONSOLIDATE_ON_TASK_CLOSE=true`）
- **L3 用户画像聚合 + 主动遗忘 + 审计** → S-030d

接缝已留：`MemoryJobSchedulerPort` 即后续固化作业的统一调度入口；
`MemoryAssembler.assemble()` 仍是各层汇聚 + 预算裁剪的唯一出口。
