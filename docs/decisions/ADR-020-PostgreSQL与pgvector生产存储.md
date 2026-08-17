# ADR-020：生产 Profile 选择 PostgreSQL + pgvector

- 状态：已接受
- 日期：2026-08-17

## 背景

SQLite 和 ChromaDB 适合单机 Demo，但核心 Case Assessment 需要多进程 API/Worker、
事务、唯一约束、乐观锁、并发启动保护、复杂查询和数据库迁移。向量检索同时需要在
数据库层下推 Workspace、Case、DocumentVersion 和 current-version 范围。

## 决策

生产 Profile 使用：

- PostgreSQL 保存核心业务对象、Run/Event、ProcessingJob 和审计；
- SQLAlchemy 2.x 作为 Adapter 内 ORM；
- Alembic 作为 schema migration SSOT；
- pgvector 保存案件证据向量；
- PostgreSQL FTS 与 dense vector 经应用层 RRF 融合；
- SQLite 和 Chroma 继续作为 local/demo/test profile。

配置边界：

```text
STORAGE_BACKEND=sqlite|postgres
VECTOR_BACKEND=chroma|pgvector
```

优先迁移 Workspace、Case、Document、ProcessingJob、CaseFact、AgentRun、RunEvent、
PolicyRule 和 Assessment，不一次迁移所有辅助表。

## 为什么这样设计

- PostgreSQL 能用事务和约束解决“同一 Case 只能有一个活动 Run”，避免只靠 Python
  先查后写的竞态；
- 乐观锁可使用 `UPDATE ... WHERE revision = :expected_revision`；
- pgvector 与业务范围在同一查询中下推，减少跨系统 join 和租户泄漏风险；
- 对秋招展示而言，数据库迁移、索引、事务和并发比增加另一个独立向量组件更有价值。

## 表设计原则

- 所有租户业务表显式保存 `workspace_id`；
- Case 子资源同时保存或可通过受约束外键确定 `case_id`；
- `created_at/updated_at` 使用 timezone-aware 时间；
- 可变聚合保存 `revision`；
- 活动 Run 使用 partial unique index 或事务锁保证唯一；
- 常用列表和范围查询建立组合索引；
- Domain Pydantic Model 与 ORM Model 分离。

## 备选方案

### 继续 SQLite + Chroma

保留为 local profile，但拒绝作为生产目标。多 Worker 并发、迁移和约束表达不足。

### PostgreSQL + 独立 Elasticsearch/向量库

当前拒绝。数据规模和检索需求尚不需要额外集群，增加组件会提高部署和一致性成本。

### 只使用 pgvector，删除 Chroma

拒绝。会破坏现有本地体验和离线测试；先用 Adapter 双 profile 迁移。

## 代价

- 需要 Alembic、连接池和 PostgreSQL 集成测试；
- SQLite/PostgreSQL contract 需要长期保持一致；
- FTS 中文分词能力需单独评估，不能预先宣称效果。

## 验证

- Alembic 从空库升级成功；
- SQLite/PostgreSQL 核心 Repository contract 同时通过；
- 并发启动两个 Assessment Run 只有一个成功；
- Case A 无法检索 Case B 向量；
- 新文档版本 ready 后旧版本默认不召回；
- ORM 对象不泄漏到 domain/API。
