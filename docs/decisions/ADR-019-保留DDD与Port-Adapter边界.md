# ADR-019：生产化升级保留 DDD 与 Port/Adapter 边界

- 状态：已接受
- 日期：2026-08-17

## 背景

当前仓库已将 Case、Document、Fact、Policy、Assessment、Run/Event 等业务概念放在
domain，并通过 Repository、ObjectStore、WorkflowRuntime、Trace、Model 等 Port
隔离基础设施。生产化升级将引入 PostgreSQL、SQLAlchemy、Alembic、Celery、Redis、
pgvector、MinIO 和 OpenTelemetry。

如果为接入这些组件重写领域层，现有抽象会失去意义，测试也会与基础设施耦合。

## 决策

保持依赖方向：
```text
api → app → domain
infra → domain ports
```

具体约束：

1. domain 不导入 FastAPI、SQLAlchemy、Celery、Redis、LangGraph、LangChain 或数据库；
2. SQLAlchemy ORM Model 只存在于 infra；
3. Celery task 调用 app service/use case 或专用 worker service，不承载领域规则；
4. PostgreSQL、SQLite、Local/S3、Chroma/pgvector 通过同一个 Port 提供等价语义；
5. AppContainer 或 profile factory 选择 Adapter；
6. 测试可直接注入 Fake/InMemory Adapter，默认不依赖外部服务。

## 为什么这样设计

- 可以用 SQLite/Fake 快速跑离线测试，用 PostgreSQL/MinIO/Celery 跑生产 profile；
- Repository 并发约束可以独立集成测试，不污染领域模型；
- 后续替换模型、任务队列或对象存储时业务规则不变；
- 能证明 Port 不是“为了设计模式”，而是已经支撑真实双后端。

## 备选方案

### 让 domain 使用 SQLAlchemy Declarative Model

拒绝。领域对象会携带 Session、懒加载和数据库生命周期，难以冻结不可变快照，也让
单元测试依赖数据库。

### 在 API 路由直接访问 ORM

拒绝。权限、事务和业务状态会散落在 HTTP 层，Worker 与 Agent 无法复用同一规则。

### 为每个基础设施重写独立业务服务

拒绝。会产生 SQLite/PostgreSQL 两套业务语义，迁移期间难以保持一致。

## 代价

- 需要维护 Domain ↔ ORM 映射；
- Port 语义必须足够精确，不能只定义 CRUD 名称；
- PostgreSQL 特有事务能力需要通过 Adapter 测试证明，而不是泄漏 SQLAlchemy API。

## 验证

- `domain` 的 import graph 不包含目标框架；
- SQLite 和 PostgreSQL 对同一 Repository contract 测试通过；
- SQLAlchemy Model 不从 infra 导出给 API/domain；
- Fake Adapter 可运行全部离线协议测试；
- AppContainer 能按 profile 替换实现。
