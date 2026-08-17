# Phase 9 实施复盘：Docker Compose 与固定 Seed Demo

- 状态：已完成
- 日期：2026-08-17
- 前置提交：`5118754`

## 1. 本阶段目标

把前八个 Phase 已实现的组件组成一个可重复启动的本地生产模拟环境：

```text
API
Worker
PostgreSQL + pgvector
Redis
MinIO
Migration
Seed Demo
可选 Prometheus
```

最终入口：

```bash
docker compose up -d
make seed-demo
```

本阶段不增加新 Agent 能力，只解决部署边界、共享基础设施、固定演示数据和重启恢复。

## 2. 实施前审计

### 2.1 已有资产

- 多阶段 `Dockerfile`；
- runtime 使用非 root `app` 用户；
- API 和 Worker 已共用一个镜像、不同启动命令；
- Compose 已声明 PostgreSQL/Redis/MinIO/migrate/worker/app；
- Alembic 已有 PostgreSQL + pgvector migration；
- PostgreSQL、Redis、MinIO 和 Worker 已有 healthcheck；
- PostgreSQL/Redis/MinIO 已使用 named volume；
- API 有 liveness/readiness；
- Celery 有 retry、timeout、幂等和 cooperative cancel；
- deterministic embedding 和 deterministic planner 已存在，可用于无模型费用 Demo。

### 2.2 P0 缺口

1. API Compose 默认仍是 `sqlite + chroma + manual + local object store`；
2. Worker 固定使用 `postgres + pgvector + celery + s3`；
3. 因此 API 创建的 Job 与 Worker 读取的数据库不是同一业务 SSOT；
4. API 上传到 LocalObjectStore，Worker 从 MinIO 读取，文件也不共享；
5. 默认 `LLM_PROVIDER/EMBED_PROVIDER=api`，新机器没有 Key 时无法通过启动门禁；
6. 没有 `make seed-demo`；
7. 没有固定、脱敏、幂等的三类 Demo 数据；
8. API Compose 没有依赖 migration、Redis 和 MinIO init；
9. `./data` / `./logs` bind mount 在非 root 容器中可能出现宿主目录权限问题；
10. Compose 只有 `deploy.resources`，本地 Compose 不一定执行；
11. 没有 optional Prometheus service；
12. README 明确承认当前 Compose 尚未完成真实一键启动验收。

### 2.3 当前环境限制

客户端存在：

```text
Docker 29.7.2
Docker Compose 5.4.0
```

但本轮开始时 Docker daemon 未启动：

```text
failed to connect to the docker API at unix:///var/run/docker.sock
```

因此先实施静态/单元/Compose config 门禁；代码稳定后尝试启动本机 daemon。若环境仍阻塞，
必须在本文件中如实记录，不能把 `docker-compose config` 冒充真实容器验收。

## 3. 为什么这样设计

### 3.1 Compose 默认就是 production profile

Compose 不是 local SQLite Demo。API 和 Worker 必须共同使用：

```text
STORAGE_BACKEND=postgres
VECTOR_BACKEND=pgvector
TASK_BACKEND=celery
OBJECT_STORE_BACKEND=s3
```

否则“API 返回 202，Worker 独立处理”只是配置文件里的假架构。

### 3.2 无 Key Demo 使用确定性 Adapter，不伪造模型效果

固定 Demo 默认：

```text
LLM_PROVIDER=local
AGENT_PLANNER_BACKEND=deterministic
EMBED_PROVIDER=deterministic
ENABLE_RERANKER=false
```

目的：

- 新机器不需要 API Key；
- 不联网、不下载模型、不产生费用；
- 验证业务状态机、Worker、pgvector、HITL、checkpoint 和引用闭环；
- README 明确 deterministic 只证明协议，不代表真实模型质量。

真实模型通过 `.env` 显式覆盖，不进入默认一键启动门禁。

### 3.3 Seed 必须幂等、脱敏、可重复

Seed 使用固定业务 ID 和合成材料，重复执行只补齐缺失数据，不创建重复 Workspace/Case/
Rule/Document/Job。

计划准备：

- Demo A：材料和 confirmed fact 完整，启动后到 Reviewer；
- Demo B：缺失关键事实，启动后进入 Human-in-the-loop；
- Demo C：预置 failed ProcessingJob，重试后由 Worker 恢复。

Seed 只构造业务输入，不直接伪造最终 Assessment 或 Eval 指标。

### 3.4 Migration 和 Seed 都使用同一镜像

避免维护单独工具镜像：

```text
同一 image
├── api: uvicorn
├── worker: celery
├── migrate: alembic upgrade head
└── seed: python -m scripts.seed_demo
```

这能证明运行依赖一致，也减少“本机能运行、容器缺包”的漂移。

### 3.5 Named Volume 代替宿主 bind mount

PostgreSQL、Redis、MinIO、应用日志和可选模型缓存使用 named volume。原因：

- 非 root 容器不会依赖宿主目录 uid/gid；
- 重启后数据保留；
- 新机器无需先创建并 chmod `data/`、`logs/`；
- 原始文件实际进入 MinIO，不依赖 API 单机目录。

## 4. 实际修改：为什么改、怎么实现

### 4.1 Compose 与 Docker

| 文件 | 为什么改 | 怎么实现 |
| --- | --- | --- |
| `docker-compose.yml` | API/Worker 原来使用不同存储 Profile，无法形成真实异步闭环 | API/Worker 强制共享 PostgreSQL、pgvector、Redis 和 MinIO；migration/minio-init 成功后再启动；配置 restart、health、CPU/memory、named volume、graceful stop |
| `Dockerfile` | 新机镜像必须 non-root、可健康检查、支持 OCR 和优雅退出 | 多阶段构建；`USER app`；`STOPSIGNAL SIGTERM`；readiness health；暴露 8001/9101；加入 RapidOCR/OpenCV 所需最小动态库 |
| `scripts/compose.sh` | 本机只有 legacy `docker-compose`，其他机器可能只有 plugin | 自动探测 `docker compose` / `docker-compose`，Makefile 和文档只依赖统一脚本 |
| `deploy/prometheus/prometheus.yml` | Phase 8 指标需要实际 collector | optional profile 抓取 `app:8001/api/v2/metrics` 与 `worker:9101/metrics` |
| `Makefile` | 一键路径必须短且可复跑 | 新增 `docker-build/up/down/observability/seed-demo/docker-smoke`；只 build `app` 一次，其他服务复用同一镜像 |
| `.env.example` | 开发通道不能覆盖 Compose 零 Key Profile | 新增 `COMPOSE_*` 独立变量；默认 deterministic/safe-empty；清理旧 Worker provider 噪音 |

### 4.2 零 Key Agent Profile

| 文件 | 为什么改 | 怎么实现 |
| --- | --- | --- |
| `config.py` | 缺失事实时不能因为没有模型而破坏 Seed | 新增 `FACT_PROPOSAL_BACKEND=langchain|safe_empty`；Demo 登录默认关闭 |
| `infra/qa/fact_proposals.py` | 零 Key Profile 不能猜事实 | `SafeEmptyFactProposalGenerator` 始终返回空候选和 0 usage，Graph 因此进入 HITL |
| `infra/qa/__init__.py` | Adapter 可由 composition root 发现 | 导出 safe-empty Adapter |
| `app/factories.py` | Profile 选择应留在工厂，不进 domain | 按 Settings 构造 Structured 或 SafeEmpty Fact Proposal |

### 4.3 Seed 与本地演示身份

| 文件 | 为什么改 | 怎么实现 |
| --- | --- | --- |
| `scripts/seed_demo.py` | 需要固定、脱敏、幂等的三条演示路径 | 固定 Workspace/Case/Document/Rule/Fact ID；真实 MinIO/Celery/pgvector/Repository；真实 Policy Use Case draft→publish；启动真实 Assessment Graph |
| `api/v2/auth.py` | 固定 Seed 用户无法通过匿名登录访问既有 Workspace | 新增默认关闭且不进 OpenAPI 的 `/auth/demo`；只有 Compose 显式开启且用户已 Seed 才签发 cookie |
| `scripts/compose_smoke.sh` | 终端操作必须沉淀为可复跑门禁 | 验证 API/Worker health、Demo 登录、3 Case、≥3 chunks、2 Agent Runs、Worker metric |
| `scripts/__init__.py` | 支持 `python -m scripts.seed_demo` | 标记 scripts 为 Python package |

### 4.4 真实容器验收发现的生产缺陷

#### 缺陷 A：运行镜像缺 `PyJWT`

现象：

```text
ModuleNotFoundError: No module named 'jwt'
```

原因：`PyJWT` 只在 `requirements-dev.txt`，本机测试有包，生产镜像没有。

修复：

- 移到 `requirements.txt`；
- 新增 `tests/infra/test_runtime_dependencies.py`；
- API/Worker 真容器重新启动通过。

#### 缺陷 B：PostgreSQL AgentRun 时间精度误判

现象：

```text
ValueError: AgentRun 的归属和创建字段不可修改
```

原因：Domain `time.time()` 有亚微秒精度，PostgreSQL `DateTime` round-trip 为微秒；Repo 用
float 严格相等比较不可变 `created_at`。

修复：

- Domain timestamp 先转换为 UTC datetime，再转回与数据库一致的微秒 epoch 比较；
- 新增 SQLite 与真实 PostgreSQL 精度回归；
- Seed Graph 成功写入 2 个 Run、38 个 RunEvent。

#### 缺陷 C：OCR Python 包存在但动态库缺失

现象：

```text
ImportError: libxcb.so.1: cannot open shared object file
```

修复：

- 对 `cv2.abi3.so` 执行 `ldd`；
- runtime 安装 `libgl1`、`libglib2.0-0`、`libx11-xcb1`；
- 容器内 `import cv2, rapidocr_onnxruntime` 真实通过。

#### 缺陷 D：Docker 构建缓存层不合理

现象：普通 requirements 变化导致 155MB CPU Torch 重新下载；legacy Compose 对多个 build
service 重复构建同一 image。

修复：

- CPU Torch 独立于 `COPY requirements.txt` 成层；
- `make docker-build` 只执行 `compose build app`；
- 业务源码增量重建约 3 秒，其他服务复用 `riskpilot-rag:latest`。

## 5. 三类固定 Demo

| Demo | Seed 后状态 | 证明什么 |
| --- | --- | --- |
| A Happy Path | Case=`review_required`，Run=`waiting_for_review/human_review` | Worker、pgvector、confirmed Fact、规则、Assessment、Citation、Reviewer 门禁 |
| B Human-in-the-loop | Run=`waiting_for_user/human_fact_confirmation` | safe-empty 不猜事实，缺失事实中断和 checkpoint |
| C Failure Recovery | 初始 Job=`failed`；API retry 后 `completed`、retry_count=1 | 失败状态、重试、Celery Worker、MinIO、幂等索引恢复 |

重复执行 `make seed-demo` 后：

```text
workspaces=1
cases=3
rules=1
runs=2
```

## 6. 测试与真实容器证据

### 6.1 聚焦离线测试

```text
Phase 9 聚焦：85 passed, 5 warnings in 3.29s
部署安全契约：28 passed, 5 warnings in 3.45s
Seed/Compose 核心：7 passed, 5 warnings in 10.28s
mypy: 151 source files
```

新增测试：

- `tests/infra/test_compose_contract.py`；
- `tests/infra/test_dockerfile_contract.py`；
- `tests/infra/test_runtime_dependencies.py`；
- `tests/infra/test_safe_empty_fact_proposals.py`；
- `tests/scripts/test_seed_demo.py`；
- `tests/api/test_auth.py` Demo 登录；
- `tests/infra/test_sqlalchemy_storage.py` 时间精度。

### 6.2 镜像

```text
image: riskpilot-rag:latest
size: 735,576,810 bytes
user: app (uid=999)
stop signal: SIGTERM
Python: 3.12.14
RapidOCR/OpenCV import: PASS
```

镜像偏大是因为当前统一镜像仍包含 PyTorch、Chroma、Chinese-CLIP 依赖和 OCR 动态库；
本阶段优先一镜像可运行，后续可拆 runtime extras，但不能牺牲主线。

### 6.3 服务与基础设施

真实 Colima Docker daemon 上：

```text
app        healthy
worker     healthy
postgres   healthy
redis      healthy
minio      healthy
prometheus healthy (optional)
```

数据库：

```text
Alembic head = c312b95fd8a1
pgvector = 0.8.6
Demo cases = 3
Evidence chunks = 3
Agent runs = 2
Run events = 38
```

MinIO 中存在 3 个固定合成对象；Redis `PING=PONG`。

### 6.4 Worker、幂等和 Prometheus

```text
Demo C retry: failed → queued → completed
retry_count = 1
chunks_before = 3
chunks_after idempotent replay = 3
```

optional Prometheus：

```text
riskpilot-api    UP
riskpilot-worker UP
```

Worker metric 已被 Prometheus 查询到：

```text
riskpilot_worker_tasks_total{status="completed",task="riskpilot.process_document"} 1
```

### 6.5 独立重启和持久化

API、Worker 分别 restart 后：

```text
app_restarted=true health=healthy
worker_restarted=true health=healthy
cases_after_restart=3
evidence_chunks=3
agent_runs=2
document_versions=3
```

可复跑门禁：

```text
$ make docker-smoke
demo_cases=3
app_health=healthy
worker_health=healthy
evidence_chunks=3
agent_runs=2
compose_smoke=PASS
```

### 6.6 最终全量

```text
$ PATH="$PWD/.venv/bin:$PATH" make ci
Ruff: All checks passed
Format: 430 files already formatted
mypy: Success: no issues found in 151 source files
pytest: 1357 passed, 4 skipped, 5 warnings in 31.14s
Offline Agent Eval: 39 cases / 13 categories / PASS
```

Agent Eval 的协议与安全指标继续保持：

- task/stage/tool/tool-argument/missing-fact/citation/recovery = `1.0`；
- unsupported false accept / unsafe action / cross-tenant leakage = `0.0`；
- average tool calls = `3.307692`；
- average tokens/cost = `0`，因为是 deterministic/Fake 协议，不代表真实模型免费。

## 7. API 与数据变化

新增隐藏 API：

```text
POST /api/v2/auth/demo
```

边界：

- `DEMO_LOGIN_ENABLED=false` 默认返回 404；
- 不进入 OpenAPI；
- 用户未 Seed 返回 503；
- 仅 Compose 本地演示显式开启；
- 不是企业 IAM 替代品。

数据库没有新增 Migration；修复了 AgentRun Adapter 的时间精度比较。

## 8. 验收标准

- [x] API 与 Worker 使用同一 PostgreSQL、Redis 和 MinIO；
- [x] migration 成功后 API/Worker 才启动；
- [x] 默认 Compose 不需要 API Key；
- [x] API 和 Worker 使用同一镜像；
- [x] 容器使用非 root 用户；
- [x] healthcheck 反映 API readiness、Worker、DB、Redis、MinIO；
- [x] graceful shutdown 和 restart policy；
- [x] named volume 重启后数据不丢；
- [x] `make seed-demo` 幂等；
- [x] 三类脱敏 Demo 有稳定 ID；
- [x] optional Prometheus 可抓 API/Worker；
- [x] `make docker-up` 真实通过；
- [x] API/Worker 可独立重启；
- [x] `make docker-smoke` PASS；
- [x] 最终全量 `make ci` 通过并回填真实数字。

## 9. 尚未解决的风险

1. 默认 Profile 是 deterministic 协议演示，不代表真实模型质量；
2. 统一镜像约 736MB，后续可拆 `core/visual/ocr` extras；
3. Docker 首次构建需要下载较多 wheel，网络弱时耗时明显；已加 apt/pip 超时重试和缓存分层；
4. Demo 登录只适用于本地 Compose，不应在公网环境启用；
5. Prometheus 当前为 optional 单机 profile，未加入 Grafana 和长期存储；
6. Compose 使用本地默认口令，只适合生产模拟；公网部署必须通过 secrets 覆盖；
7. Run Detail 页面与三 Demo 前端引导属于 Phase 10。

## 10. 下一阶段

Phase 9 全量门禁通过后进入 Phase 10：Agent Run Detail、固定三 Demo UI、演示脚本、简历描述。

