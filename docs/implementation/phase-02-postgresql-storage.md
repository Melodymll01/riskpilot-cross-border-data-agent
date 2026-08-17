# Phase 2 实施复盘：PostgreSQL 核心存储

- 状态：已完成
- 日期：2026-08-17
- 前置提交：`4c9444f`

## 1. 本阶段目标

1. 引入 SQLAlchemy 2.x、Alembic 和 psycopg；
2. 保留现有 SQLite Adapter；
3. 新增 `STORAGE_BACKEND=sqlite|postgres`；
4. 迁移核心案件闭环 Repository；
5. 用数据库约束保证同一 Case 只有一个活动 Assessment Run；
6. 用 revision 条件更新保证 AgentRun 乐观锁；
7. 建立 SQLite/PostgreSQL 双 profile contract 测试。

## 2. 为什么这样设计

现有 Port 已定义业务语义，Phase 2 不改 domain，而是在 infra 增加 SQLAlchemy Adapter。
PostgreSQL 的价值不是“换数据库”，而是把事务、唯一约束、并发和迁移从 Python 约定提升
为数据库保证。

本阶段采用两层测试：

- 本地零依赖：SQLAlchemy Adapter 使用 SQLite 内存 Engine 跑 Repository contract；
- PostgreSQL 专属：GitHub Actions 启动 PostgreSQL service，运行真实方言、partial unique
  index 和并发 Run 测试。

开始实施时当前环境没有 Docker/psql。为完成真实方言验收，后续通过 Homebrew 安装
PostgreSQL 17.11，不注册系统服务，使用 `/tmp/riskpilot-pg17` 临时集群和端口 55432
运行验证；验证后立即停止并删除临时数据目录。

## 3. 修改文件与实现说明

### 3.1 依赖与配置

#### `requirements.txt`

**为什么改**

PostgreSQL production profile 需要稳定 ORM、迁移和驱动。

**怎么实现**

- SQLAlchemy 2.x；
- Alembic；
- psycopg 3 binary。

#### `config.py`、`.env.example`

**为什么改**

必须在不破坏本地 SQLite 的前提下选择生产数据库。

**怎么实现**

- 新增 `STORAGE_BACKEND=sqlite|postgres`；
- 新增 `DATABASE_URL`；
- postgres profile 启动时校验 URL 协议；
- 默认仍为 SQLite，现有用户无需配置即可运行。

### 3.2 SQLAlchemy Engine 与 ORM

#### `infra/storage/sqlalchemy/database.py`

**为什么新增**

所有 PostgreSQL Repository 必须共享 Engine/连接池，同时每个调用使用短 Session 和明确
事务边界。

**怎么实现**

- `SqlAlchemyDatabase` 管理 Engine 和 `sessionmaker`；
- `session()` 使用 `Session.begin()` 自动提交/回滚；
- `read_session()` 用于只读查询；
- `pool_pre_ping=True`；
- `ping()` 服务 readiness；
- `dispose()` 在 FastAPI shutdown 调用；
- SQLite 内存 Engine 使用 `StaticPool`，支持零外部依赖 contract 测试。

#### `infra/storage/sqlalchemy/models.py`

**为什么新增**

SQLAlchemy ORM 只属于 infra，不能复用或污染 Domain Pydantic Model。

**怎么实现**

- 建立 Workspace、Case、Document、ProcessingJob、EvidenceChunk、CaseFact、PolicyRule、
  Assessment、AgentRun/Checkpoint/Event 等核心表；
- JSON 在 SQLite 使用 JSON，在 PostgreSQL 自动切换 JSONB；
- 时间列使用 `DateTime(timezone=True)`；
- 可变聚合保存 revision；
- 核心表显式 workspace/case 外键和组合索引；
- `AgentRun` 建立 partial unique index：
  `case_id + workflow_type WHERE status IN active statuses`；
- `CaseFactEvidence` 使用 `(fact_id, fact_version)` 组合外键；
- CheckConstraint 使用稳定显式名称，避免 PostgreSQL 自动命名造成 Alembic drift；
- Phase 2 不迁 Visual Asset，避免与 Phase 3 多模态/pgvector 混合。

#### `infra/storage/sqlalchemy/mapping.py`

**为什么新增**

Domain 当前使用 float timestamp，数据库使用 timezone-aware datetime，需要集中转换，不能
让每个 Repository 自己猜时区。

**怎么实现**

- `require_datetime(float)`；
- `to_datetime(float | None)`；
- `require_timestamp(datetime)`；
- `to_timestamp(datetime | None)`；
- 所有转换固定 UTC。

### 3.3 核心 Repository Adapter

#### `workspace_repo.py`

**为什么新增**

Workspace 与创建者 admin membership 必须同事务创建。

**怎么实现**

- 原子创建 Workspace + Membership；
- 用户 Workspace 列表按更新时间排序；
- PostgreSQL 使用 `ON CONFLICT` 更新 role；
- SQLite contract profile 使用等价 get/update 分支；
- upsert 不覆盖原始 `joined_at`。
- Workspace 主表插入后显式 flush，再插 Membership；没有 ORM relationship 时不能依赖
  SQLAlchemy 自动推断父子 flush 顺序。

#### `case_repo.py`

**为什么新增**

Case 是所有核心资源的租户根，需要 production CRUD 和 Workspace 范围查询。

**怎么实现**

- create/get/update；
- Workspace 过滤；
- 默认隐藏 archived；
- Domain ↔ ORM 显式映射。

#### `document_repo.py`

**为什么新增**

Document、Version、Case binding 和 ProcessingJob 必须原子创建；解析结果必须原子推进四个
对象。

**怎么实现**

- `create_upload()` 在一个事务插入四对象；
- 版本、绑定、任务查询；
- `save_parse_result()` 同事务更新 Version/Document/Job 与 ParseSnapshot；
- ParseSnapshot 以 JSONB 保存完整 Pydantic schema；
- 不保存本地绝对路径，只保存 object_key。
- Document、Version、Binding/Job 之间显式父子 flush，确保 PostgreSQL 外键顺序；

#### `case_fact_repo.py`

**为什么新增**

Fact 当前快照、历史版本和原文证据必须保持一致，Reviewer 批量确认不能部分成功。

**怎么实现**

- create/create_many 原子写；
- 证据必须绑定当前 Case 的 DocumentVersion；
- revision 保留历史 payload；
- 批量状态更新同步更新当前快照与版本快照；
- Evidence 使用组合外键绑定 FactVersion。
- Fact 与 FactVersion 分阶段 flush，再插 Evidence，保持外键顺序且仍在同一事务回滚。

#### `policy_rule_repo.py`

**为什么新增**

确定性规则必须按 Workspace/Ruleset/Jurisdiction/Status 查询，并保证同版本主键唯一。

**怎么实现**

- 组合主键；
- JSONB 保存 required fields、condition、result、clause IDs；
- 条件化列表查询；
- status 更新验证 rowcount。

#### `assessment_repo.py`

**为什么新增**

Assessment 版本切换和审批必须原子，不能出现 Assessment 已批准但 Case 未完成。

**怎么实现**

- 新版本、旧版本 supersede、Case.active_assessment_id 更新同事务；
- 条件更新实现 compare-and-set；
- Bundle 拆为 Assessment/Finding/Action/Citation 表；
- 审批时同时 CAS 更新 Assessment 与 Case；
- 版本号 `(case_id, version)` 唯一。
- Assessment 父记录 flush 后再插 Finding/Action/Citation，解决真实 PostgreSQL 外键顺序。

#### `agent_run_repo.py`

**为什么新增**

Agent Run 是并发和恢复核心，需要数据库级乐观锁、事件连续性和活动 Run 唯一。

**怎么实现**

- 创建 Run/初始 Checkpoint/Event 同事务；
- `save_progress()` 要求 revision 恰好 +1；
- `UPDATE ... WHERE run_id AND revision=expected`；
- stale revision 抛 `AgentRunConflict`，不写入 Checkpoint/Event；
- `(run_id, sequence)`、`(run_id, version)` 唯一；
- partial unique index 阻止同 Case + Workflow 的第二个活动 Run；
- PostgreSQL 并发 contract 用两个线程同时 create，期望一个 created、一个 conflict。
- Run 父记录 flush 后再插 Checkpoint/Event。

#### `evidence_index.py`

**为什么新增**

若业务对象在 PostgreSQL、EvidenceChunk 仍在 SQLite，会形成 split-brain，Worker/API 无法
共享同一案件数据。

**怎么实现**

- Phase 2 先把 EvidenceChunk 也放 SQLAlchemy 数据库；
- 写入前数据库验证 Workspace/Case/DocumentVersion 归属；
- Case/Workspace 过滤在 SQL WHERE 下推；
- 暂以 JSON 保存 embedding，在候选集上计算 cosine/BM25/RRF；
- 明确这是过渡实现，Phase 3 改为 pgvector + PostgreSQL FTS。

#### `infra/storage/sqlalchemy/__init__.py`

**为什么新增**

集中导出 production Adapter，避免调用方依赖内部文件布局。

**怎么实现**

只导出 Database、Base 和核心 Repository。

### 3.4 Alembic

#### `alembic.ini`、`migrations/env.py`、`migrations/script.py.mako`

**为什么新增**

生产 schema 必须版本化，不能在启动时 `create_all` 代替迁移。

**怎么实现**

- Alembic 使用 `Settings.database_url`；
- 测试可通过 `Config.attributes["database_url"]` 注入临时数据库；
- offline/online 两种运行模式；
- `compare_type=True`；
- revision 模板使用 Python 3.12 类型。

#### `migrations/versions/0ddb370aee40_initial_core_schema.py`

**为什么新增**

冻结核心 schema 的第一版，后续 ORM 修改必须新增 migration，不能重写历史。

**怎么实现**

- 20 张业务表 + `alembic_version`；
- JSONB、timezone-aware timestamp、外键、唯一约束和组合索引；
- 活动 Run partial unique index；
- downgrade 按依赖逆序删除；
- 本地执行 `upgrade → downgrade → upgrade`；
- PostgreSQL offline SQL 编译确认 JSONB 和 partial index。

### 3.5 Profile 装配与生命周期

#### `app/factories.py`

**为什么改**

Port 的价值必须体现在真实 profile 切换，而不是只保留两套未使用实现。

**怎么实现**

- 新增共享 `build_sqlalchemy_database()`；
- 7 个核心 Repository 和 EvidenceIndex 按 `storage_backend` 选择；
- SQLite local profile 保持原实现；
- PostgreSQL profile 复用同一 Database。

#### `app/container.py`

**为什么改**

Engine 必须单例共享并在关闭时释放，不能每个 Repository 建独立连接池。

**怎么实现**

- PostgreSQL profile 构造一个 `storage_database`；
- 注入 Workspace/Case/Document/Evidence/Fact/Policy/Assessment/Run；
- readiness 使用 SQLAlchemyDatabase ping；
- User/Task/Memory/Audit 等辅助模块暂留 SQLite；
- 测试可继续全 Fake 注入。

#### `main.py`

**为什么改**

应用 shutdown 应释放 SQLAlchemy Engine。

**怎么实现**

- lifespan 关闭阶段 best-effort `dispose()`；
- 关闭失败只记录 warning，不阻塞进程退出。

#### `infra/health/readiness.py`、`infra/storage/_db.py`

**为什么改**

readiness 不应知道具体数据库类型。

**怎么实现**

- 抽象为 `database.ping()`；
- SQLite pool 和 SqlAlchemyDatabase 都实现 ping；
- API health 无需修改即可支持双 profile。

### 3.6 测试与 CI

#### `tests/infra/test_sqlalchemy_storage.py`

**为什么新增**

需要一套同样语义的 Repository contract，既可本地运行，也可在真实 PostgreSQL 运行。

**怎么实现**

- 默认 SQLite 内存 Engine：验证 ORM 和 Port 语义；
- `TEST_POSTGRES_URL` 存在时运行同套测试；
- 覆盖 7 核心 Repository + EvidenceIndex；
- 覆盖版本历史、审批原子性、乐观锁、租户检索；
- PostgreSQL 专属并发测试使用两个线程同时创建活动 Run；
- 本地无 PostgreSQL 时该项明确 skip。

#### `tests/infra/test_alembic_migrations.py`

**为什么新增**

Migration 可运行和 ORM schema 不漂移必须自动验证。

**怎么实现**

- SQLite 临时库执行 upgrade/downgrade/upgrade；
- 检查关键表和 partial index；
- PostgreSQL offline SQL 检查 JSONB 和 partial unique SQL。

#### `tests/app/test_factories.py`、`tests/app/test_container.py`

**为什么改**

需要证明 production profile 真正选中 SQLAlchemy Adapter，并且共享 Engine。

**怎么实现**

- 用 SQLAlchemy SQLite Engine 验证 profile 选择；
- 不声称这是 PostgreSQL 方言测试；
- 检查 readiness、EvidenceIndex 和 7 核心 Repository。

#### `tests/test_config_chat_override.py`

**为什么改**

postgres profile 使用 SQLite URL 应在 startup 前 fail fast。

**怎么实现**

新增 database URL 协议校验测试。

#### `.github/workflows/ci.yml`

**为什么改**

当前执行机器没有 Docker/psql，真实 PostgreSQL 不能靠本地 SQLite contract 冒充。

**怎么实现**

- 新增 PostgreSQL 17 service job；
- `alembic upgrade head`；
- `alembic check` 防 schema drift；
- 设置 `TEST_POSTGRES_URL` 跑同一 contract；
- 并发活动 Run 测试在该 job 不会 skip。

## 4. 数据模型变化

新增 infra ORM 表，不改变 Domain Pydantic Model 字段。

核心数据变化：

- float timestamp 在 domain 保持兼容，数据库使用 timezone-aware datetime；
- JSON 文档在 PostgreSQL 使用 JSONB；
- AgentRun 增加数据库级 partial unique index；
- AgentRun revision 使用数据库 CAS；
- CaseFactEvidence 增加 FactVersion、DocumentVersion 等外键；
- Assessment Bundle 拆表并保持原子事务。

## 5. API 变化

无破坏性 API 变化。通过环境变量选择 Adapter：

```ini
STORAGE_BACKEND=sqlite

# production
STORAGE_BACKEND=postgres
DATABASE_URL=postgresql+psycopg://...
```

现有路由、请求和响应保持兼容。

## 6. Agent 状态变化

Graph 节点与 checkpoint state 不变。

变化只在持久化保证：

- 活动 Run 唯一从 Python 预检查升级为数据库 partial unique index；
- Run 进度使用 revision CAS；
- Checkpoint/Event 与 Run 更新同事务。

## 7. 测试结果

### 本地完整质量门禁

```text
ruff check .                         passed
ruff format --check .                359 files formatted
mypy domain app infra                124 source files passed
pytest -q                            1229 passed, 2 skipped, 5 warnings in 21.02s
```

两个 skip：

1. live 模型测试默认关闭；
2. 当前机器没有 Docker/PostgreSQL，真实并发活动 Run contract 跳过。

### SQLAlchemy contract

```text
tests/infra/test_sqlalchemy_storage.py
5 passed, 1 skipped
```

### Alembic

```text
upgrade → downgrade → upgrade        passed
alembic check                        No new upgrade operations detected
PostgreSQL offline SQL               JSONB + partial unique index confirmed
```

### 真实 PostgreSQL

临时 PostgreSQL 17.11 实测：

```text
alembic upgrade head             passed
alembic check                    No new upgrade operations detected
Repository contracts             6 passed
并发活动 Run                     1 created + 1 conflict
partial unique index             已在 pg_indexes 中确认
alembic_version                  1 row
```

实测过程发现并修复两类 SQLite contract 未暴露的问题：

1. 未命名 CheckConstraint 在 PostgreSQL 反射后造成 Alembic schema drift；
2. 无 ORM relationship 的多 Row 插入不能依赖自动 flush 顺序，父表必须显式 flush。

GitHub Actions 仍保留 PostgreSQL 17 service job，作为后续每次提交的持续回归。

## 8. 尚未解决的风险

1. Evidence embedding 在 Phase 2 仍为 JSONB + Python 排序，只为保持完整 profile；
   Phase 3 必须升级 pgvector/FTS；
2. User/Task/Memory/Audit 辅助模块仍使用 SQLite，当前不是全仓 PostgreSQL；核心案件闭环
   已迁移；
3. LangGraph checkpointer 仍是 SQLite，目标 PostgreSQL checkpointer 后续接入；
4. PostgreSQL transaction isolation 目前使用 SQLAlchemy/数据库默认级别；高竞争场景需
   根据真实压测决定是否提高隔离级别。

## 9. 下一阶段建议

进入 Phase 3：pgvector、PostgreSQL FTS 和 MinIO/S3ObjectStore。继续复用本阶段
SQLAlchemy schema、真实 PostgreSQL contract 和 CI service。

## 10. 验收标准

| 验收项 | 状态 |
| --- | --- |
| Alembic 空库迁移 | 满足：SQLite 可逆 + PostgreSQL 17.11 upgrade |
| SQLite/PostgreSQL profile 可装配 | 满足：双 profile contract |
| 核心 Repository contract | 满足：本地 + PostgreSQL 6 passed |
| AgentRun 乐观锁 | 满足：stale revision fail closed |
| 同 Case 活动 Run 唯一 | 满足：真实并发 1 created + 1 conflict |
| ORM 不泄漏到 domain | 满足：ORM 仅位于 infra |

Phase 2 验收通过。
