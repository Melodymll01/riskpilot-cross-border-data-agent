# ADR-017：LangGraph 与 Celery 职责分离

- 状态：已接受
- 日期：2026-08-17

## 背景

RiskPilot 同时存在两类“长时间运行”：

1. Case Assessment 需要根据证据状态选择下一步，并在缺失事实、冲突事实和 Reviewer
   审批处暂停与恢复；
2. 文档解析、OCR、Embedding、索引、批量评测和报告导出需要在独立进程执行，并具备
   重试、超时、取消和水平扩容。

两者都可能跨越一次 HTTP 请求，但它们解决的问题不同。若只使用 LangGraph，会缺少
成熟任务队列的投递、并发和 Worker 生命周期；若只使用 Celery，会把 Agent 决策拆成
零散任务，无法清晰表达状态图和 Human-in-the-loop。

## 决策

### LangGraph 负责

- Agent 下一步决策和条件路由；
- 节点状态和最大循环限制；
- `interrupt/resume`；
- Case Assessment 与受限 Deep Research 的流程位置；
- 轻量 checkpoint。

### Celery 负责

- 文档解析、OCR、切块、Embedding 和索引；
- 批量评测和报告导出；
- Worker 并发、重试、指数退避、超时和取消；
- 任务持久化投递和独立扩容；
- failed job 管理。

### 关联方式

- 业务数据库中的 `ProcessingJob` 和 `AgentRun` 是产品状态 SSOT；
- Celery task 通过 `job_id/run_id` 关联业务对象；
- LangGraph checkpoint 只记录 ID、节点和轻量状态；
- Agent 需要耗时任务时提交 Celery task，然后根据数据库任务状态决定等待、继续或失败；
- Celery 不直接决定 Agent 的下一节点。

## 为什么这样设计

职责分离使失败语义清晰：

- “OCR Worker 崩溃”属于任务执行失败，可以重试；
- “案件缺少关键事实”属于业务决策，需要 Human-in-the-loop；
- “Reviewer 拒绝 Assessment”属于正式流程结果，不应被 Celery 当作失败重试。

这也便于独立扩容：API、Agent Graph 和 OCR/Embedding Worker 的资源需求不同。

## 备选方案

### 只使用 LangGraph

拒绝。LangGraph 擅长状态与恢复，但不是通用后台队列；无法替代 Worker 并发、任务路由、
可见性超时和独立资源池。

### 只使用 Celery Canvas

拒绝。Celery chain/chord 可以编排固定任务，但不适合模型驱动的条件决策、
Human-in-the-loop 和可解释状态图。

### 引入 Temporal

当前拒绝。Temporal 能统一持久工作流和 Activity，但会增加基础设施、SDK 和运维复杂度。
在 PostgreSQL + Redis + Celery + LangGraph 已满足当前规模时，不为技术数量引入它。

## 代价

- 需要维护 AgentRun、ProcessingJob、Celery task 和 LangGraph checkpoint 的关联；
- 必须定义幂等边界，防止任务重投导致重复索引；
- 需要统一 Trace Context，才能跨 HTTP、Graph 与 Worker 诊断。

## 验证

- API 进程停止后 Celery 任务继续运行；
- Worker 重启后任务可重试；
- 重复投递不产生重复 Chunk；
- LangGraph 能在任务完成后从 checkpoint 恢复；
- Celery task 不包含 Agent 节点选择逻辑。
