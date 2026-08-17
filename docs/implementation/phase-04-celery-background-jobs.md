# Phase 4 实施复盘：Redis、Celery 与可恢复后台任务

- 状态：已完成
- 日期：2026-08-17
- 前置提交：`9e27676`

## 1. 本阶段目标

1. 引入 Redis 与 Celery，但不让 domain 依赖二者；
2. V3 案件文档上传后，production profile 自动投递后台处理并立即返回 202；
3. 把解析、OCR、切块、Embedding 和索引放到独立 Worker；
4. 数据库 `ProcessingJob` 是任务状态 SSOT，Celery result backend 不是业务数据库；
5. 重复投递同一 `job_id` 不重复解析或写入索引；
6. Worker 崩溃后任务可重投，瞬态错误有限重试并指数退避；
7. 支持 cooperative cancel、failed job 查询和人工 retry；
8. 默认 pytest 继续零 Redis、零网络、零模型费用；
9. 真实启动 Redis + Celery Worker 验证 API/Worker 分离。

## 2. 当前实现审计

Phase 4 开始前：

- `DocumentManagementUseCase.upload()` 已创建 Document/Version/Binding/ProcessingJob，API 返回
  202，但没有真正投递任务；
- `DocumentProcessingWorker` 同步执行解析，把任务从 queued 推进到 OCR 或 chunk；
- `EvidenceIndexWorker` 同步执行 chunk、embedding 和索引；
- `/processing-jobs/{job_id}/parse` 与 `/index` 由 HTTP 请求线程直接执行，主要是开发调试入口；
- 已有 parse snapshot 重放和 completed index 重放幂等；
- `ProcessingJob` 没有 revision，两个 Worker 同时读取 queued 后可能都开始执行；
- 没有独立 OCR Worker，空文本 PDF 只会停在 `ocr`；
- cancel 只存在于领域方法，没有 API、调度器 revoke 和并发保护；
- failed job 可按 ID 查询和 retry，但不能按 Case 列出。

## 3. 为什么这样设计

### 3.1 为什么 Celery 只接收 `job_id`

Celery 消息不携带 Workspace、Case、actor、原始正文或完整业务对象，只携带服务端生成的
`job_id`。Worker 每次从 Repository 重读：

- Job 当前 revision、status 和 stage；
- DocumentVersion 与 Document；
- Case binding；
- ObjectStore 中的原始对象；
- ParseSnapshot 与 EvidenceIndex 状态。

这样客户端和消息队列都不能伪造租户范围，消息重放也不会使用陈旧业务对象。

### 3.2 为什么增加 ProcessingJob revision

Celery 的 `acks_late` 和 Worker 崩溃恢复会天然产生重复投递。只做：

```text
SELECT status
if queued:
    UPDATE running
```

存在先查后写竞态。Phase 4 给 ProcessingJob 增加 revision，并让状态推进使用：

```sql
UPDATE processing_jobs
SET ..., revision = :next
WHERE job_id = :job_id AND revision = :expected
```

只有一个 Worker 能从同一 revision 成功推进。失败者重新读取数据库：

- 已 completed：幂等返回；
- 已进入后续 stage：从后续 stage 恢复；
- 已 cancelled：停止；
- 仍由其他 Worker 执行：确认后 ack，不重复执行。

### 3.3 为什么取消采用 cooperative cancel

Celery `revoke(..., terminate=True)` 会直接杀进程，可能中断数据库事务或底层 C 扩展。默认采用：

1. API 把 ProcessingJob CAS 到 `cancelled`；
2. Dispatcher 使用 `revoke(terminate=False)` 阻止尚未开始的消息；
3. Worker 在每个阶段前后重新读取 Job；
4. 即使 cancel 发生在解析/Embedding 中间，后续 CAS 也会因为 revision 变化而拒绝陈旧写入。

因此取消不会依赖危险的强杀。正在执行的单个不可中断模型调用可能要等到返回，但其结果不会
覆盖 cancelled 状态。

### 3.4 为什么保留手工 parse/index 端点

现有 API 和测试依赖这两个端点。Phase 4 不做无 ADR 的破坏性删除：

- production 上传与 retry 自动走 Celery；
- parse/index 端点保留为 local/manual profile 的调试和兼容入口；
- 两者调用同一组 Worker/Repository 幂等逻辑，不维护两套业务实现；
- 最终对外文档只推荐上传 → 查询 Job，不把手工端点作为产品主线。

### 3.5 为什么不是一个 Celery task 对应一个业务 stage

本阶段采用一个 `process_document(job_id)` orchestration task，内部按数据库 stage 恢复。原因：

- Job stage 已是持久化状态机；
- 每个 stage 单独发消息会引入 stage 消息之间的一致性和丢消息窗口；
- 一个 task 仍然会在每个阶段更新数据库、检查取消并可从最新 stage 重放；
- OCR、Embedding 等耗时能力仍在独立 Worker 进程，不在 API。

以后若单阶段需要独立队列或 GPU Worker，可在不改领域模型的前提下拆 task routing。

### 3.6 为什么 local profile 不偷偷使用线程池

默认 `TASK_BACKEND=manual`：

- 上传仍返回 queued Job；
- 开发者可使用兼容 parse/index 端点；
- pytest 不启动 Redis/Celery；
- 不用进程内线程池冒充持久任务系统。

production 使用 `TASK_BACKEND=celery`，并要求 PostgreSQL、S3 和 Redis 同时配置。API 与 Worker
可以独立重启并共享数据库与对象存储。

### 3.7 为什么 OCR 使用独立 Port/Adapter

解析器只识别哪些 PDF 页缺少文本层；OCR 是耗时外部能力，应通过 `DocumentOcrPort`：

- domain 只认识“把 ParseSnapshot 补成 OCR Snapshot”的语义；
- infra Adapter 懒加载 RapidOCR；
- 默认测试注入 Fake，不下载模型；
- OCR 结果继续使用稳定页码、置信度和结构化 Pydantic Schema。

## 4. 计划修改文件

| 文件 | 目的 | 状态 |
| --- | --- | --- |
| `domain/documents.py` | ProcessingJob revision 与取消/重试状态规则 | 已实施 |
| `domain/ports.py` | OCR 与后台任务 Dispatcher Port | 已实施 |
| `domain/errors.py` | ProcessingJob 乐观锁冲突 | 已实施 |
| `infra/storage/*document_repo.py` | revision CAS、Case Job 列表 | 已实施 |
| `infra/storage/sqlalchemy/models.py` | Job revision 映射 | 已实施 |
| `migrations/versions/*_processing_job_revision.py` | revision migration | 已实施 |
| `infra/document_processing/ocr.py` | RapidOCR Adapter | 已实施 |
| `app/workers/document_ocr.py` | OCR stage Worker | 已实施 |
| `app/workers/document_pipeline.py` | 可恢复阶段编排与重试准备 | 已实施 |
| `infra/tasks/celery_app.py` | Celery 配置与 task 注册 | 已实施 |
| `infra/tasks/celery_dispatcher.py` | Dispatcher Port Adapter | 已实施 |
| `app/use_cases/document_management.py` | 上传/重试投递、取消、失败列表 | 已实施 |
| `app/factories.py`、`app/container.py` | composition root 装配 | 已实施 |
| `config.py`、`.env.example` | TASK_BACKEND、Celery retry/timeout | 已实施 |
| `api/v3/documents.py`、`schemas.py` | cancel 与 Case Job 列表 API | 已实施 |
| `docker-compose.yml` | Redis 与 Worker 服务 | 已实施 |
| `requirements.txt` | Celery Redis transport | 已实施 |
| `infra/search/deterministic_embedder.py` | 离线协议/Seed Demo 向量，不冒充真实效果 | 已实施 |
| `scripts/phase4_worker_contract.py` | 显式真实 Worker contract 与 enqueue-only 模式 | 已实施 |
| `.github/workflows/ci.yml` | 离线 Celery protocol tests | 已实施（普通 pytest 自动覆盖） |
| `tests/*` | CAS、重复消费、重试、取消、真实 Worker | 已实施 |
| `docs/roadmap/autumn-recruitment-production-plan.md` | Phase 状态推进 | 已实施 |

## 5. 数据模型变化

### 5.1 ProcessingJob revision

**为什么改**

`acks_late`、Worker 丢失和人工重复投递都会让同一 Job 被多个执行器读取。没有 revision 时，
一个陈旧 Worker 可以在用户取消后把 Job 写回 running/completed。

**怎么实现**

- `ProcessingJob.revision` 初始为 0；
- 每次 `start/advance/complete/fail/retry/cancel` 自动递增 revision；
- DocumentRepo 所有 Job 写方法显式接收 `expected_revision`；
- SQLite 使用 `UPDATE ... WHERE job_id=? AND revision=?`；
- PostgreSQL 使用等价 SQLAlchemy Core 条件更新；
- 条件更新失败抛 `ProcessingJobConflict`；
- Document 与 Job 在同一事务更新，Job CAS 失败时 Document 更新一并回滚；
- EvidenceIndex 最终写 chunks、Document ready 和 Job completed 时也使用同一 CAS；
- `c312b95fd8a1` 为已有数据库增加 `revision INTEGER NOT NULL DEFAULT 0`，再移除默认；
- local SQLite 使用幂等 `PRAGMA table_info + ALTER TABLE` 补列。

### 5.2 可恢复文档阶段

**为什么改**

只设置 `acks_late` 不能自动获得恢复能力。Worker 可能在任意数据库提交后、消息 ack 前崩溃，
重投会看到 running 中间态。

**怎么实现**

- `DocumentPipelineWorker` 每轮只按数据库 `status/current_stage` 路由；
- 支持从 `queued`、`running/extract_structure`、`ocr`、`chunk`、`embedding`、
  `index_vector` 恢复；
- parse snapshot 已存在时不重复解析；
- completed + ready + 已有索引时直接幂等返回；
- `chunking → embedding → indexing → ready` 每阶段都持久化 Job revision；
- 最终 chunks、Document ready、Job completed 同一事务提交；
- 瞬态解析/Embedding 错误保留当前 stage，让 Celery 重试；
- 永久内容错误立即 failed；重试耗尽由 Celery task 统一 failed；
- Embedder 若降级返回零向量，Worker fail closed，不写入索引。

### 5.3 OCR Adapter

**为什么改**

原解析器只能发现 PDF 空文本页，不能完成 OCR，Job 会停在 `ocr`。

**怎么实现**

- `DocumentOcrPort` 保持 domain 与 RapidOCR 解耦；
- `RapidOcrDocumentAdapter` 只渲染并补全 `empty` 页；
- 保留稳定页码，写入 OCR 文本、平均置信度、warning 和 parser version；
- RapidOCR 首次调用才加载，默认测试注入 Fake，不下载模型；
- OCR Job 成功后推进到 chunk，失败遵守永久/瞬态分类。
- OCR 执行前再次计算对象 SHA-256，防止 parse 与 OCR 之间对象被替换。

### 5.4 Celery transport 与任务语义

**为什么改**

任务队列必须保证消息契约、worker-lost 恢复和限流，而不是只把函数放进进程池。

**怎么实现**

- 消息只含 `job_id`；task ID 为 `document:<job_id>:attempt<retry_count>`；
- 同一人工 attempt 重复投递得到同一 ID；人工 retry 使用新 attempt，避免 revoked ID 污染；
- JSON-only serializer，拒绝 pickle；
- `acks_late=True`、`task_reject_on_worker_lost=True`、prefetch=1；
- soft/hard timeout 分离；
- 有上限的指数退避和 jitter；
- cooperative revoke 使用 `terminate=False`；
- result backend 只保存执行结果，业务状态以 PostgreSQL 为准；
- manual profile 使用显式 no-op Dispatcher，不偷偷启动线程。
- readiness 在 `REDIS_URL` 或仅配置 `CELERY_BROKER_URL` 时都会真实 Redis PING。

### 5.5 Compose 与真实 contract

**怎么实现**

- 增加 Redis 7.4 AOF 与 healthcheck；
- API/Worker 使用同一个镜像、不同 command；
- migration one-shot 服务在 Worker 前完成；
- MinIO bucket init one-shot 服务在 Worker 前完成；
- Worker 依赖 PostgreSQL migration、Redis healthy、MinIO init；
- `scripts/phase4_worker_contract.py` 只有显式 `RUN_PHASE4_CONTRACT=1` 才运行；
- `enqueue-only` 模式在 broker 确认后退出，用于证明生产者退出不丢消息；
- deterministic embedding 只验证 2048 维 pgvector 协议，不作为效果证据。

## 6. API 变化

计划保持已有上传/查询/parse/index/retry 兼容，并新增：

- `GET /api/v3/cases/{case_id}/processing-jobs`；
- `POST /api/v3/processing-jobs/{job_id}/cancel`。

production profile 中：

- 上传成功后自动 enqueue；
- retry 成功后自动 enqueue；
- broker 失败会把 Job 持久化为 failed，而不是让 queued Job 永久悬挂。

实际已保持：

- `POST /api/v3/cases/{case_id}/documents`：202，production profile 自动投递；
- `GET /api/v3/processing-jobs/{job_id}`：数据库状态 SSOT；
- `GET /api/v3/cases/{case_id}/processing-jobs`：可按 status 过滤；
- `POST /api/v3/processing-jobs/{job_id}/retry`：revision 与 `retry_count` 递增后新 attempt；
- `POST /api/v3/processing-jobs/{job_id}/cancel`：先 CAS cancelled，再 cooperative revoke；
- 旧 parse/index 端点保留，不破坏现有 API。

## 7. Agent 状态变化

无。Celery 只负责耗时后台任务。LangGraph checkpoint、Agent Run 和 Case Assessment 状态机不在
本阶段迁移，继续遵守 ADR-017。

## 8. 验收门禁

- [x] 默认 `make ci` 零 Redis、零模型调用；
- [x] production 上传返回 202 且 Celery 收到确定性 task ID；
- [x] API 进程停止后 Worker 任务继续；
- [x] 同一 job 重复投递不重复写 chunk；
- [x] Worker 在 parse 后崩溃可从当前 stage 恢复；
- [x] 瞬态错误按上限指数退避，永久错误进入 failed；
- [x] cancelled Job 不会被陈旧 Worker 写回 running/completed；
- [x] failed Job 可按 Case 查询并 retry；
- [x] Worker task 有 soft/hard timeout、acks_late 和 worker-lost 重投；
- [x] Redis、API、Worker 可独立重启；
- [x] 真实 PostgreSQL + Redis + MinIO + Celery contract 通过；
- [x] 全量离线测试通过。

## 9. 测试结果

### 9.1 离线聚焦门禁

- ProcessingJob revision/CAS、双 Repository、migration：`118 passed, 2 skipped`；
- OCR、Pipeline、Worker：`64 passed`；
- Celery/Container/API：`120 passed`；
- Phase 4 核心聚焦：`179 passed, 2 skipped`；
- Celery/production profile 最新回归：`104 passed, 2 skipped`；
- Worker/Embedding 故障最新回归：`19 passed`。
- 最终全量：`1270 passed, 4 skipped, 5 warnings in 18.89s`；
- Ruff：`384 files already formatted`；
- mypy：`Success: no issues found in 136 source files`。

全部默认零 Redis、零模型 API、零下载、零费用。

### 9.2 真实基础设施

实际启动：

- PostgreSQL + pgvector；
- Redis 7.4 AOF；
- MinIO + bucket init；
- 宿主独立 FastAPI 进程；
- 宿主独立 Celery 5.6.3 Worker 进程。

数据库执行最新 migration：

```text
0ddb370aee40
→ 7ef0c8a42d14
→ c312b95fd8a1
```

`alembic check`：无 drift。

最终真实 PostgreSQL 17 migration + Repository contract：`11 passed in 3.40s`；head 为
`c312b95fd8a1`，`processing_jobs.revision` 为 non-null integer。

### 9.3 API 停止后 Worker 继续

真实 HTTP：

1. 匿名登录；
2. 创建 Workspace；
3. 创建 Case；
4. 上传 TXT；
5. API 返回 202、Job `queued:extract_structure:r0`；
6. Redis `riskpilot.documents` 队列长度为 1；
7. 完全停止 Uvicorn；
8. 再启动独立 Celery Worker。

结果：

- Worker 注册 `riskpilot.process_document`；
- 从 Redis 收到 `document:<job_id>:attempt0`；
- 0.68 秒完成；
- Job `completed:ready:r5`；
- Document `ready`；
- Evidence chunk 数 1；
- API 进程在任务执行期间不存在。

### 9.4 重复消费幂等

对同一 `job_id + attempt0` 连续重复投递两次：

- 两次 task 均约 4ms 返回 `completed`；
- Job revision 保持 `r5`；
- chunk 数仍为 1；
- 不重复解析、Embedding 或索引。

### 9.5 Worker 重启

1. Worker 完全停止；
2. enqueue-only producer 正常退出；
3. Redis 队列为 1，DB Job 为 queued；
4. 启动新的 Worker 实例；
5. 新 Worker 消费并在 0.71 秒完成；
6. Redis result backend 记录 `SUCCESS/completed`。

### 9.6 取消竞态

1. Worker 离线；
2. enqueue-only 创建消息；
3. API 核心逻辑先把 DB CAS 为 `cancelled:r1`；
4. revoke 广播在 Worker 离线时没有移除 Redis 消息，队列仍为 1；
5. 启动 Worker，消息真实到达。

结果：

- Worker 重读数据库后返回 `cancelled`；
- Job 仍为 `cancelled:extract_structure:r1`；
- chunk 数 0；
- Redis 队列归零。

这证明安全性依赖数据库状态和 revision，而不是依赖不可靠的 revoke 广播。

### 9.7 Docker 镜像构建说明

本机用 legacy Docker builder 首次构建统一 app/worker 镜像时，Debian apt 下载极慢，手动终止
后返回 137。本阶段随后用同一仓库 `.venv` 启动独立 Worker 完成真实进程验收。Compose 的
Redis/PostgreSQL/MinIO/migration/minio-init 定义已真实运行；统一镜像的完整缓存构建和最终
一键启动仍按路线在 Phase 9 再验收，不把本次中止伪称成功。

## 10. 尚未解决的风险

1. RapidOCR 真实图片质量尚未进入版本化数据集；本阶段只验证 Adapter 和阶段协议；
2. production Embedding 仍由外部 API/Ollama 提供，真实吞吐和费用留 Phase 7/8；
3. `deterministic` embedding 只用于离线协议和 Seed Demo，不得写入 README 效果指标；
4. Celery result backend 只用于执行结果，业务查询仍以 ProcessingJob 表为准；
5. cooperative cancel 不能中断正在执行的单个 C 扩展/模型调用，但 revision 阻止陈旧提交；
6. 完整 Docker app/worker 镜像缓存构建和一键启动在 Phase 9 完成；
7. batch eval 与报告导出尚未迁入 Celery，因为 Agent Eval 与正式报告导出业务对象分别在
   Phase 7/10 才冻结；本阶段不为不存在的业务流程造空 task。

## 11. 验收结论

满足 Phase 4 验收标准，可以进入 Phase 5。

- LangGraph 与 Celery 职责边界未混淆；
- API 上传只创建业务对象和投递 ID，不同步执行解析/OCR/Embedding；
- Worker 只通过 `job_id` 重读数据库状态；
- revision CAS 阻止重复消费与取消竞态；
- Redis/Celery 是执行设施，ProcessingJob 是查询和审计 SSOT；
- 默认测试不需要 Redis 或真实模型；
- 未伪称 Phase 9 的最终 Docker 一键启动已经完成。

## 12. 下一阶段

Phase 4 全部门禁通过后进入 Phase 5：把 EvidencePlan、Typed Tool Registry、Fact Proposal、
缺口/冲突检测和 Human-in-the-loop 正式接入核心 Case Assessment Graph。
