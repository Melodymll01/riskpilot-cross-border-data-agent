# Phase 3 实施复盘：pgvector、PostgreSQL FTS 与对象存储

- 状态：已完成
- 日期：2026-08-17
- 前置提交：`9660a6e`

## 1. 本阶段目标

1. 新增 `VECTOR_BACKEND=chroma|pgvector`，保留本地 Chroma/SQLite 体验；
2. 把 production profile 的案件证据向量升级为 PostgreSQL `vector`；
3. 把 Workspace、Case、当前文档版本过滤下推到候选查询；
4. 使用 dense vector + PostgreSQL FTS + RRF，避免把整个租户候选集拉回 Python；
5. 新增 `OBJECT_STORE_BACKEND=local|s3`；
6. 实现与 `LocalObjectStore` 语义一致的 `S3ObjectStore`；
7. 使用真实 PostgreSQL + pgvector 和真实 MinIO 完成集成验收；
8. 保持 domain Port 和既有 API 不变。

## 2. 为什么这样设计

### 2.1 为什么 pgvector 与业务数据库放在一起

案件证据检索不是一个只按向量相似度排序的问题。每次查询都必须同时满足：

- `workspace_id` 相同；
- `case_id` 相同；
- Document 仍为 `ready`；
- EvidenceChunk 属于 Document 的 `current_version_id`；
- Workspace 知识只能来自显式 `workspace_knowledge` 文档。

如果向量在独立服务、业务范围在 PostgreSQL，应用层需要跨系统拼接和二次过滤，既增加一致性
成本，也会扩大跨租户泄漏风险。pgvector 让作用域过滤、当前版本过滤和向量候选排序在同一个
SQL 查询中完成。

### 2.2 为什么保留 Chroma/SQLite profile

本地开发、零密钥测试和简单 Demo 不应强制安装 PostgreSQL、pgvector 和 MinIO。Phase 3
只新增 production Adapter，不删除现有 local Adapter，继续证明 Port/Adapter 的替换能力。

支持的组合是：

| Profile | `STORAGE_BACKEND` | `VECTOR_BACKEND` | 用途 |
| --- | --- | --- | --- |
| local/demo/test | `sqlite` | `chroma` | 零外部依赖、本地开发 |
| production | `postgres` | `pgvector` | API/Worker 共享、范围过滤下推 |

拒绝 `sqlite + pgvector` 和 `postgres + chroma` 混搭。前者无法运行 pgvector；后者会让核心
业务事务与案件证据索引跨存储分裂，难以保证原子完成索引。

### 2.3 为什么数据库列使用可变维度 `vector`

Phase 2 已经可能写入 2048 维 JSON embedding。把列直接迁移成固定维度并不利于安全演进：
历史数据或切换 embedding 模型时，migration 会因维度不一致直接失败。

本阶段选择：

- 数据列使用 pgvector 可变维度 `vector`，迁移时可无损接收既有 JSON 数组；
- production Adapter 仍严格校验当前配置的 embedding 维度；
- 查询显式过滤 `vector_dims(embedding)`，不同维度的旧索引不会参与当前检索；
- 模型维度变更后通过重新索引切换，而不是让查询混合不可比较的向量。

这一区分把“迁移兼容性”和“运行时检索一致性”同时保留下来。

### 2.4 为什么使用 `halfvec(2048)` HNSW 表达式索引

当前默认 embedding 是 2048 维。pgvector 普通 `vector` 的 HNSW/IVFFlat 索引支持上限是
2000 维，直接建立 `vector_cosine_ops` 索引会失败。

本阶段不擅自降低现有模型维度，而是：

- 原始列继续保存 float32 `vector`；
- HNSW 索引建立在 `embedding::halfvec(2048)` 表达式上；
- 查询使用同样的 cast 和 `halfvec_cosine_ops`；
- `halfvec` 索引支持更高维度，并减少索引空间；
- Domain 和 API 仍只看到普通 `list[float]`。

代价是近似索引使用半精度，但原始向量没有丢失。真实召回质量必须在 Phase 7 用版本化数据集
评测，Phase 3 不预先宣称质量提升。

### 2.5 为什么 PostgreSQL FTS 前先做服务端分词

PostgreSQL 内置 `simple` 配置对中文没有理想的词法切分。为了不额外引入 Elasticsearch 或
数据库分词插件，本阶段继续使用项目已有 jieba，把文档和查询转成空格分隔词项，再交给：

```sql
to_tsvector('simple', search_tokens)
plainto_tsquery('simple', query_tokens)
```

FTS 使用 GIN 表达式索引。词法候选和向量候选各自在数据库限制数量，应用层只对有限候选执行
RRF。返回模型中的 `bm25_score` 字段为兼容现有 Port 暂时保留，production 值实际是
PostgreSQL `ts_rank_cd`；Phase 3 不把它伪称为 BM25。

### 2.6 为什么对象存储保持现有 Port

`ObjectStorePort` 已经只有 `put/read/delete/exists`，且业务层只保存 `object_key`。S3/MinIO
不需要改变领域语义，因此不应为了基础设施细节修改 domain。

`S3ObjectStore` 必须与本地 Adapter 保持一致：

- 对象键只能是安全 POSIX 相对键；
- 空内容拒绝；
- 同键同内容重复写是幂等成功；
- 同键不同内容拒绝覆盖；
- 不存在对象读取抛 `FileNotFoundError`；
- API 与 Worker 只要连接同一 bucket，就能读取同一对象。

不可变写入使用 S3 条件请求，而不是“先 HEAD 再 PUT”的竞态实现。

## 3. 计划修改文件

| 文件 | 目的 | 状态 |
| --- | --- | --- |
| `requirements.txt` | 增加 pgvector Python 类型和 boto3 S3 客户端 | 已实施 |
| `config.py` | 增加 vector/object-store profile 与组合校验 | 已实施 |
| `.env.example` | 给出 local 与 production 可复制配置 | 已实施 |
| `infra/storage/sqlalchemy/models.py` | embedding 改为 pgvector 类型，增加分词列与条件索引 | 已实施 |
| `infra/storage/sqlalchemy/evidence_index.py` | SQL 下推 dense/FTS 候选与 RRF | 已实施 |
| `migrations/versions/*_add_pgvector_evidence_index.py` | 安装扩展、迁移列、建立 HNSW/GIN 索引 | 已实施 |
| `infra/object_store/keys.py` | 两个 Adapter 共用对象键安全校验 | 已实施 |
| `infra/object_store/s3.py` | S3/MinIO Adapter | 已实施 |
| `infra/object_store/local.py` | 复用统一键校验，不改变行为 | 已实施 |
| `infra/object_store/__init__.py` | 导出 S3 Adapter | 已实施 |
| `app/factories.py` | 按 profile 装配 Adapter | 已实施 |
| `tests/infra/test_sqlalchemy_storage.py` | current-version、跨 Case 与真实 pgvector contract | 已实施 |
| `tests/infra/test_s3_object_store.py` | 离线 fake client 与真实 MinIO contract | 已实施 |
| `tests/app/test_factories.py` | profile 选择与非法组合 | 已实施 |
| `tests/app/test_container.py` | production 装配 | 已实施 |
| `.github/workflows/ci.yml` | PostgreSQL job 使用 pgvector 17 镜像 | 已实施 |
| `docker-compose.yml` | 增加 PostgreSQL/pgvector 与 MinIO 基础设施 | 已实施 |
| `docs/roadmap/autumn-recruitment-production-plan.md` | 完成后推进 Phase 状态 | 已实施 |

## 4. 数据模型变化

实施完成后补充实际 migration、索引和回滚行为。

### 4.1 `requirements.txt`

**为什么改**

SQLAlchemy 本身不知道 PostgreSQL `vector` 的参数绑定和结果转换；手写字符串转换容易造成
类型、精度和 SQL 注入问题。S3/MinIO 应使用标准 SDK，而不是自己拼签名协议。

**怎么实现**

- 增加 `pgvector>=0.4,<1.0`，只在 infra ORM/查询层使用；
- 增加 `boto3>=1.40,<2.0`，通过标准 S3 API 同时兼容 AWS S3 和 MinIO；
- domain 与 app 均不依赖这两个库。

### 4.2 `config.py` 与 `.env.example`

**为什么改**

如果 `STORAGE_BACKEND=postgres` 但证据仍写 Chroma，会出现业务对象和索引跨存储 split-brain；
如果 `STORAGE_BACKEND=sqlite` 却选择 pgvector，则配置根本无法执行。对象存储则与业务数据库
正交，应允许 local/S3 独立切换。

**怎么实现**

- 新增 `VECTOR_BACKEND=chroma|pgvector`；
- 新增 `OBJECT_STORE_BACKEND=local|s3` 及 endpoint、bucket、region、可选凭证；
- 启动门禁只接受 `sqlite+chroma` 或 `postgres+pgvector`；
- 当前 production HNSW 索引固定为 2048 维，因此 pgvector profile 要求
  `EMBEDDING_DIMENSIONS=2048`；
- S3 access key 与 secret key 必须成对出现；同时省略时允许 AWS 默认凭证链；
- `.env.example` 同时给出本地和 production 配置，不提交真实凭证。

### 4.3 `infra/storage/sqlalchemy/models.py`

**为什么改**

Phase 2 的 JSON embedding 只能把整批候选拉到 Python，不能使用数据库向量索引。FTS 也需要
稳定可索引的分词文本。

**怎么实现**

- `embedding` 使用 `Vector()`，SQLite 方言自动退化为 JSON，只服务离线 contract；
- 新增 `search_tokens` 保存 jieba 词项；
- PostgreSQL-only HNSW 索引：
  `embedding::halfvec(2048) halfvec_cosine_ops`；
- HNSW partial predicate 只索引 2048 维数据；
- PostgreSQL-only GIN 表达式索引：
  `to_tsvector('simple', search_tokens)`；
- 两个 production 索引均通过 `ddl_if(dialect="postgresql")` 避免污染 SQLite。

### 4.4 `migrations/versions/7ef0c8a42d14_add_pgvector_evidence_index.py`

**为什么新增**

ORM 不是 schema SSOT。已有 Phase 2 数据必须从 JSONB 可逆升级，不能删除重建表。

**怎么实现**

- 所有方言新增并回填 `search_tokens`；
- PostgreSQL 执行 `CREATE EXTENSION IF NOT EXISTS vector`；
- `embedding::text::vector` 将既有 JSONB 数组转成 pgvector；
- 建立 HNSW halfvec 与 FTS GIN 索引；
- downgrade 把 vector 转回 JSONB，再删除 `search_tokens`；
- downgrade 不删除 `vector` extension，因为扩展可能被同库其他 schema 使用。

### 4.5 `infra/storage/sqlalchemy/evidence_index.py`

**为什么改**

生产检索必须在 SQL 中先限制 Workspace/Case/当前版本，不能把整个租户候选集拉到应用层后
再过滤。

**怎么实现**

- 写入时校验 embedding 维度并生成 jieba `search_tokens`；
- PostgreSQL dense 路径使用 `halfvec` cosine distance 和维度 predicate；
- PostgreSQL lexical 路径使用 `plainto_tsquery + ts_rank_cd`；
- dense 与 lexical 两路都要求 `vector_dims(embedding)` 等于当前模型维度，防止模型切换后
  旧维度数据从词法分支混入结果；
- 两路候选各取 `top_k * 4`，应用层只对有限候选做 RRF；
- Case 检索 SQL join Document/CaseDocument，并要求 chunk 属于 `current_version_id`；
- Case 检索不额外要求 `ready`，因为现有 `replace_version_chunks()` contract 允许在任务最终
  状态提交前验证刚写入的索引；旧版本仍会被排除；
- Workspace 检索额外要求 `document_type=workspace_knowledge` 且 Document `ready`；
- SQLite 方言保留原 Python cosine/BM25，仅用于零服务 contract；
- 为兼容既有 `EvidenceSearchHit`，`bm25_score` 暂承载 `ts_rank_cd`，文档明确不把它伪称 BM25。

### 4.6 `infra/object_store/keys.py` 与 `local.py`

**为什么改**

如果 Local 与 S3 各自实现对象键校验，容易出现同一个 key 在两个 Profile 下语义不同。

**怎么实现**

- 抽出 `validate_object_key()`；
- 拒绝空键、绝对路径、`.`、`..` 和反斜杠；
- Local Adapter 只复用验证函数，原子写入和不可变行为不变。

### 4.7 `infra/object_store/s3.py`

**为什么新增**

API 与独立 Worker 不能共享容器内本地目录，S3 协议可以同时支持本地 MinIO 和云对象存储。

**怎么实现**

- boto3 使用 S3 v4 签名与 path-style addressing；
- boto3 client 延迟到首次对象 I/O 才构造，模块 import 和 Container 装配不触发凭证链或
  metadata 网络探测；
- `put_object(IfNoneMatch="*")` 原子阻止覆盖，不使用有竞态的“先 HEAD 再 PUT”；
- 对条件冲突读取对象 metadata 中的 SHA-256 和长度：同内容幂等，不同内容抛
  `FileExistsError`；
- `read` 将 S3 404 映射为 `FileNotFoundError`；
- `delete` 保持 Port 的 bool 语义；
- 构造函数支持注入 client，默认测试不访问网络。

### 4.8 `app/factories.py`

**为什么改**

Adapter 选择必须集中在 composition root，Use Case 不应出现 `if s3` 或 `if pgvector`。

**怎么实现**

- `build_object_store()` 按 profile 返回 Local 或 S3 Adapter；
- `build_evidence_index()` 按 vector profile 返回 SQLite 或 SQLAlchemy/pgvector Adapter；
- pgvector Adapter 显式接收配置维度；
- Container 仍共享同一个 SQLAlchemy Engine。

### 4.9 测试文件

**为什么改**

仅证明类能构造不足以证明租户隔离、不可变写和 profile 门禁。

**怎么实现**

- `test_s3_object_store.py` 使用纯内存 fake S3 client 跑完整离线 contract；
- 可选 `TEST_S3_*` contract 用两个独立 Adapter 模拟 API 写、Worker 读；
- 配置测试拒绝 split-brain 组合、错误维度和残缺 S3 凭证；
- factory/container 测试显式装配完整 production profile；
- Alembic offline SQL 断言 extension、vector cast、halfvec HNSW 与 FTS GIN；
- 当前聚焦结果：`78 passed, 2 skipped, 5 warnings`；两个 skip 分别等待真实
  PostgreSQL 和真实 MinIO 环境。

### 4.10 `migrations/env.py`

**为什么改**

PostgreSQL 会把 `CAST(embedding AS halfvec(2048))` 反射为
`embedding::halfvec(2048)`。两者语义相同，但 Alembic 无法稳定规范化含 operator class 的
表达式索引，最初因此产生假 drift。

**怎么实现**

- ORM 仍用结构化 `cast(...).label + postgresql_ops`，保证 `create_all` 能创建正确索引；
- migration 仍显式创建 HNSW 索引；
- Alembic `include_object` 只排除这一条已知无法规范化比较的表达式索引；
- 真实 PostgreSQL contract 通过 inspector 和 `pg_indexes` 验证索引名称、HNSW 方法、
  halfvec cast、operator class 与维度 predicate；
- 其他表、列、约束和索引仍由 `alembic check` 完整比较，不能借此隐藏普通 schema drift。

### 4.11 `.github/workflows/ci.yml`

**为什么改**

原生 `postgres:17-alpine` 镜像没有 pgvector extension，Phase 3 migration 会在 CI 中真实失败。
本地安装扩展不能代替 CI 可复现环境。

**怎么实现**

- PostgreSQL service 切换为 `pgvector/pgvector:pg17`；
- 显式配置 `STORAGE_BACKEND=postgres`、`VECTOR_BACKEND=pgvector` 和 2048 维；
- CI 继续先跑 Alembic upgrade 与 drift，再验证 migration 创建的 extension/HNSW/GIN，
  最后运行真实 PostgreSQL Repository contract；
- 普通 `test` job 不设置这些变量，继续保持零外部服务的 local/Fake profile。

### 4.12 `docker-compose.yml`

**为什么改**

Phase 3 的 production Adapter 必须有可复制的 PostgreSQL/pgvector 和 MinIO 服务定义，不能只
依赖开发者本机 Homebrew。完整一键启动、migration entrypoint、Worker 和 Redis 仍属于
Phase 4/9，本阶段不越界伪装为最终 Compose。

**怎么实现**

- 增加 `pgvector/pgvector:pg17`，持久化 `postgres-data`，使用 `pg_isready`；
- 增加 Registry 实际存在的固定 MinIO release
  `RELEASE.2025-09-07T16-13-09Z`，持久化 `minio-data`，暴露 API/Console 并检查 live endpoint；
- 本地默认凭证只用于开发，可通过环境变量覆盖，仓库不提交真实 secret；
- app 的 `.env` 声明为 optional：存在时加载，不存在时仍可解析 Compose 并单独启动
  PostgreSQL/MinIO；真实模型 app 启动仍由运行配置门禁负责；
- app 当前仍保持原有 local profile 默认，避免在 migration 启动步骤尚未于 Phase 9 完成前
  强制切换 production；
- Phase 9 将把 app/worker depends_on、migration、Redis、graceful shutdown 和 resource
  limit 收敛成最终一键启动闭环。

首次曾按 Homebrew 版本误写 `RELEASE.2025-10-15T17-29-55Z`，真实
`docker compose up` 发现 Quay/Docker Hub 均无该 manifest。随后通过
`docker manifest inspect` 验证并改为两个 Registry 都存在的
`RELEASE.2025-09-07T16-13-09Z`。这说明 Compose 验收不能只做 YAML parse。

## 5. API 变化

计划不修改 HTTP 路由和响应 Schema。上传、解析和检索 API 继续只传业务 ID 与 `object_key`。

## 6. Agent 状态变化

无。Phase 3 只替换基础设施 Adapter；Case Assessment Graph 不新增节点，也不把正文写进
checkpoint。核心 Agent 强化留在 Phase 5。

## 7. 验收门禁

- [x] 默认离线 `pytest` 不访问 PostgreSQL、MinIO 或模型服务；
- [x] `ruff check .`、`ruff format --check .`、`mypy domain app infra` 通过；
- [x] Alembic 从 Phase 2 升级到新 head；
- [x] `CREATE EXTENSION vector` 成功；
- [x] HNSW halfvec 表达式索引和 FTS GIN 索引真实存在；
- [x] Case A 查询不能召回 Case B；
- [x] Document 新版本成为 current 后，旧版本不再召回；
- [x] SQL 查询先做 Workspace/Case/current-version 过滤；
- [x] 两个独立 S3 Adapter 实例可以写后读；
- [x] 同键不同内容不能覆盖；
- [x] LocalObjectStore contract 保持通过；
- [x] 全量离线测试通过；
- [x] `docker compose` 真实启动 pgvector 与 MinIO。

## 8. 测试结果

### 8.1 全量离线质量门禁

```bash
make ci
```

结果：

- `ruff check .`：通过；
- `ruff format --check .`：`364 files already formatted`；
- `mypy domain app infra`：`Success: no issues found in 126 source files`；
- `pytest -q`：`1246 passed, 4 skipped, 5 warnings in 17.63s`。

四个 skip 是：

1. 普通离线测试不设置 `TEST_POSTGRES_URL`，跳过真实 migration schema contract；
2. 普通离线测试不设置 `TEST_POSTGRES_URL`，跳过真实 Repository 并发 contract；
3. 普通离线测试不设置 `TEST_S3_*`；
4. live RAG 默认关闭，需显式 `RUN_LIVE=1`。

### 8.2 真实 PostgreSQL 17 + pgvector

环境：

- PostgreSQL `17.11`；
- pgvector extension `0.8.6`；
- UTF-8 临时集群，端口 `55432`；
- 测试完成后服务已停止，`/tmp` 数据已删除。

执行：

```bash
DATABASE_URL=postgresql+psycopg://bytedance@127.0.0.1:55432/riskpilot_test \
  alembic upgrade head
DATABASE_URL=... alembic check
TEST_POSTGRES_URL=... pytest -q tests/infra/test_sqlalchemy_storage.py
```

结果：

- Alembic 从空库升级成功；
- `alembic check`：`No new upgrade operations detected`；
- 真实方言 contract：`7 passed in 3.66s`；
- 最终把 migration schema 与 Repository 合并复验：`10 passed in 3.25s`；
- contract 中案件证据使用实际 2048 维向量；
- `vector:0.8.6`；
- `pg_indexes` 确认：
  - `USING hnsw (((embedding)::halfvec(2048)) halfvec_cosine_ops)`；
  - `WHERE vector_dims(embedding) = 2048`；
  - `USING gin (to_tsvector('simple'::regconfig, search_tokens))`。

另完成 `upgrade → downgrade 至 Phase 2 → upgrade` 可逆迁移，随后 drift 仍为零。

### 8.3 真实 MinIO

Homebrew 的固定 MinIO release 使用 `go-m1cpu v0.1.6`，在当前 macOS 26.5.2 / Apple M5
启动时发生依赖层 SIGSEGV。为区分环境二进制缺陷和 Adapter 缺陷，本次从同一 MinIO release
源码在 `/tmp` 临时构建，只把 `go-m1cpu` 更新为修复版本 `v0.2.1`，不修改项目仓库。

临时 MinIO：

- API：`127.0.0.1:59000`；
- 数据目录：`/tmp/riskpilot-phase3-minio-data`；
- 测试后服务、源码、二进制和数据均已删除。

执行：

```bash
TEST_S3_ENDPOINT_URL=http://127.0.0.1:59000 \
TEST_S3_ACCESS_KEY_ID=riskpilot \
TEST_S3_SECRET_ACCESS_KEY=... \
TEST_S3_BUCKET=riskpilot-phase3-test \
pytest -q tests/infra/test_s3_object_store.py
```

结果：`11 passed in 1.27s`。确认两个独立 Adapter 可以 API 写、Worker 读，同内容重复写幂等，
同键不同内容拒绝覆盖。

### 8.4 聚焦回归

- pgvector/S3/Profile/Alembic 聚焦：`78 passed, 2 skipped, 5 warnings`；
- Evidence/current-version 聚焦：`39 passed, 1 skipped, 5 warnings`；
- ObjectStore 离线 contract：`21 passed, 1 skipped`；
- 最终基础设施聚焦：`27 passed, 2 skipped, 5 warnings`。

### 8.5 Docker Compose 真实验收

本机安装临时 Docker CLI + Colima，不注册后台常驻服务。执行：

```bash
docker-compose -f docker-compose.yml up -d postgres minio
docker inspect -f '{{.State.Health.Status}}' riskpilot-postgres
docker inspect -f '{{.State.Health.Status}}' riskpilot-minio
```

结果：

- `postgres_health=healthy`；
- `minio_health=healthy`；
- 容器内 `CREATE EXTENSION vector` 返回 `0.8.6`；
- MinIO `/minio/health/ready` 返回成功；
- Compose PostgreSQL migration schema contract：`3 passed`；
- Compose PostgreSQL 2048 维 Repository contract：`7 passed`；
- Compose MinIO S3 contract：`11 passed`。

验收还真实发现并修复了三项只做静态检查无法发现的问题：

1. `.env` 缺失会阻止只启动基础设施，因此改为 optional；
2. 最初使用的 MinIO tag 在 Registry 不存在，因此改为 manifest 已验证的固定 tag；
3. 新增 migration schema 测试最初错误地把 SQLAlchemy `CursorResult` 直接传给 `dict()`，
   改为显式逐行映射。

验收后已执行 `docker-compose down -v`，删除容器、网络与数据卷，并停止 Colima。

## 9. 尚未解决的风险

1. jieba + PostgreSQL `simple` FTS 是低组件成本方案，不等同于专业中文检索；
2. HNSW 参数和 candidate multiplier 尚未经过 Phase 7 数据集评测；
3. MinIO 在 Phase 3 只验证 Adapter，共享文件的 Celery Worker 闭环在 Phase 4 完成；
4. 当前 VisualIndex 仍是辅助模块的 SQLite 实现，不在本阶段扩大迁移范围；
5. `DocumentRepoPort` 目前只有首次上传，没有公开的新增版本方法；current-version 检索已验证，
   但完整版本上传业务 API 仍需单独设计，不能由测试夹具代替；
6. 完整 production Docker Compose 按路线留在 Phase 9，本阶段只增加 pgvector/MinIO
   基础设施；Worker、Redis、migration entrypoint 和最终一键启动尚未宣称完成；
7. Homebrew MinIO 固定 release 在当前 Apple M5 上有上游依赖崩溃，Compose 使用官方容器规避
   本机 Go 二进制问题。

## 10. 下一阶段

Phase 3 全部门禁通过后，进入 Phase 4：Redis + Celery，将解析、OCR、切块、Embedding 和索引
从 API 进程移到独立 Worker，并验证重复投递幂等和失败恢复。

## 11. 验收结论

满足 Phase 3 验收标准，可以进入 Phase 4。

- SQLite/Chroma local profile 保留，默认离线开发不依赖外部服务；
- PostgreSQL/pgvector production profile 可迁移、可查询、可检查 drift；
- Workspace、Case 和 current-version 范围在 SQL 查询层执行；
- Local/S3 ObjectStore 共用安全键和不可变写语义；
- API 与独立 Worker 形态的两个 S3 Adapter 可共享同一对象；
- PostgreSQL/pgvector 与 MinIO Compose 服务已真实启动且 healthy；
- 没有修改 domain Port、HTTP API 或 Agent 状态；
- 未声称 Phase 4 Celery Worker 和 Phase 9 最终一键启动已经完成。
