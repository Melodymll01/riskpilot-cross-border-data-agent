# Phase 1 实施复盘：零密钥测试与全量 CI

- 状态：已完成
- 日期：2026-08-17
- 前置提交：`6f45b05`

## 1. 本阶段目标

1. `config.py` import 不依赖真实 API Key；
2. 裸 `pytest` 零密钥、零网络、零模型下载；
3. CI 执行全量 Ruff、format、mypy 和 pytest；
4. 提供独立 liveness/readiness；
5. 清理主要 warning；
6. README Quick Start 可直接复制。

## 2. 为什么这样设计

配置模块会被 Domain、Adapter、测试和 CLI 广泛导入。若 import 时校验真实密钥，任何不
调用模型的单元测试也会失败，形成“模块加载 = 外部服务可用”的错误耦合。

本阶段采用以下边界：

- `Settings` 只解析和校验字段形状；
- `Settings.validate_runtime_configuration()` 负责显式运行配置校验；
- FastAPI lifespan 在应用真正启动时调用该校验；
- OpenAI/Embedding Client 构造不发网络请求；
- 测试使用 local 或 Fake Adapter，不依赖真实密钥；
- liveness 只证明进程活着，readiness 才检查必需依赖。

## 3. 修改文件与实现说明

### 3.1 零密钥配置边界

#### `config.py`

**为什么改**

原实现模块 import 时立即检查 `OPENAI_API_KEY`。这会让纯领域测试、迁移脚本、静态检查
和不调用模型的 CLI 都依赖真实外部凭据。

**怎么实现**

- 新增 `RuntimeConfigurationError`；
- 新增 `_is_placeholder_secret()`，识别空值、示例值和占位符；
- 新增 `runtime_configuration_errors()`，以结构化列表返回配置问题；
- 新增 `validate_runtime_configuration()`，只在应用真正启动时 fail fast；
- 删除模块 import 阶段的 `RuntimeError`；
- 新增 `redis_url`，为 Phase 4 readiness 预留配置，但当前不强制启用。

#### `main.py`

**为什么改**

移除 import-time 门禁后，生产应用仍必须在接收请求前发现无效模型配置，不能等到第一次
调用模型才返回 401。

**怎么实现**

- FastAPI lifespan 开始时调用 `settings.validate_runtime_configuration()`；
- 模块 import、OpenAPI 生成和 pytest 收集不再触发凭据门禁；
- TestClient 真正进入 lifespan 时仍验证运行配置。

#### `tests/conftest.py`

**为什么改**

全量离线测试需要统一、最早地选择零密钥 profile，不能依赖每个测试文件在 import
之后再临时设置环境变量。

**怎么实现**

- pytest bootstrap 使用 `LLM_PROVIDER=local`、`EMBED_PROVIDER=local`；
- 这只改变配置选择，具体模型调用仍由 Fake 注入；
- 测试不会连接本地 Ollama。

#### `tests/test_config_chat_override.py`

**为什么改**

需要证明“配置可安全 import”和“生产启动仍 fail fast”同时成立。

**怎么实现**

新增测试覆盖：

- 默认占位配置只报告错误，不在构造时抛异常；
- 显式运行校验拒绝占位 Key；
- local profile 零密钥通过；
- Chat/Embedding 分离 Key 分别校验；
- LangSmith 启用时要求 Key 和不少于 16 字符的哈希盐。

#### `tests/api/test_main_integration.py`

**为什么改**

原注释仍描述 `config.py` import-time 校验，与新边界不一致。

**怎么实现**

- 删除文件内重复设置 local provider；
- 统一依赖 `tests/conftest.py` 的全局离线 profile；
- 注释改为 lifespan 运行配置门禁。

### 3.2 Liveness / Readiness

#### `domain/ports.py`、`domain/__init__.py`

**为什么改**

健康检查属于可替换基础设施能力。API 不应直接访问 SQLite 或未来 Redis Client。

**怎么实现**

- 新增 `ReadinessPort.check()`；
- 返回数据库、Redis 和汇总 ready 状态；
- domain 只定义协议，不依赖 SQLite/Redis。

#### `infra/health/readiness.py`、`infra/health/__init__.py`

**为什么新增**

readiness 必须实际检查必需依赖，而不是只判断 Container 属性是否为 `None`。

**怎么实现**

- SQLite 执行 `SELECT 1`；
- `REDIS_URL` 未配置时返回 `disabled`；
- 配置 Redis 后执行短超时 `PING`，失败即 not ready；
- 捕获技术异常并返回布尔状态，不向健康接口泄漏连接错误；
- 使用最小 `_SqlitePoolLike` Protocol，便于 Phase 2 替换 PostgreSQL 探针。

#### `app/factories.py`、`app/container.py`

**为什么改**

Readiness 也应通过 DI 装配，测试可以注入 Fake，生产使用真实探针。

**怎么实现**

- 新增 `build_readiness()`；
- `AppContainer` 新增 `ReadinessPort` 注入点；
- 默认复用业务 Repository 的 SQLite pool；
- 同时补齐 `VisualIndexPort`、`VisualEmbedPort` 返回与参数类型。

#### `api/v2/health.py`

**为什么改**

liveness 和 readiness 语义不同：负载均衡器需要知道进程是否活着，编排器需要知道实例
是否可接流量。

**怎么实现**

- 保留 `/health` 向后兼容；
- 新增 `/health/live`，固定 200 且不访问任何依赖；
- `/health/ready` 调用 `ReadinessPort`；
- 数据库或已配置 Redis 不可用时返回 HTTP 503；
- 返回逐项 `checks`，不把 LLM、Web Search、风险模型等可选外部服务作为启动门禁。

#### `tests/fakes/fake_readiness.py`、`tests/fakes/__init__.py`

**为什么新增**

API 单测应保持全 Fake，不因 readiness 新能力被迫连接 SQLite。

**怎么实现**

- Fake 可控制 database/redis 状态；
- 记录调用次数，用于证明 liveness 不触发依赖检查。

#### `tests/api/conftest.py`、`tests/api/test_rate_limit.py`、`tests/app/test_container.py`

**为什么改**

这些测试构造全 Fake `AppContainer`，必须显式注入 `FakeReadiness`。

**怎么实现**

- 全 Fake Container 注入 ready 状态；
- Container 测试继续验证 Port 可替换性；
- 不改变测试业务数据和鉴权语义。

#### `tests/api/test_health_and_sse.py`、`tests/infra/test_readiness.py`

**为什么改/新增**

需要验证 HTTP 语义和 Adapter 行为，而不是只测返回字段存在。

**怎么实现**

- 验证 live 始终 200 且不调用 readiness；
- 验证 ready 成功时返回数据库/Redis 明细；
- 验证数据库失败时返回 503；
- 验证 Redis 配置后失败会阻塞 ready；
- 验证真实临时 SQLite 的 `SELECT 1`。

#### `Dockerfile`

**为什么改**

容器 healthcheck 若访问首页，会把静态文件可用性误当作 API 健康；若访问 readiness，
数据库短暂故障又会触发进程级重启。

**怎么实现**

- healthcheck 改为 `/api/v2/health/live`；
- Python builder/runtime 从 3.11 统一到 3.12；
- site-packages 拷贝路径同步到 Python 3.12。

### 3.3 全量静态质量门禁

#### `pyproject.toml`

**为什么改**

项目、CI 和本地实际使用 Python 3.12，但 Ruff/mypy 仍按 3.10 分析；Pydantic Settings
未启用插件还产生 42 项构造假阳性。

**怎么实现**

- `requires-python` 改为 `>=3.12`；
- Ruff target 改为 `py312`；
- mypy Python 版本改为 3.12；
- 启用 `pydantic.mypy`；
- 保持 `warn_unused_ignores`、`warn_redundant_casts` 和 `warn_unreachable`。

#### `.github/workflows/ci.yml`

**为什么改**

旧 CI 只检查部分目录，未运行 format 和 mypy，遗留代码可以绕过门禁。

**怎么实现**

- Quality Job 安装完整 dev 依赖；
- 执行 `ruff check .`；
- 执行 `ruff format --check .`；
- 执行 `mypy domain app infra`；
- Test Job 不再注入假 API Key；
- 直接执行裸 `pytest -q`。

#### `Makefile`

**为什么改**

本地 `make ci` 必须与 GitHub Actions 一致，否则本地通过不能预测远端结果。

**怎么实现**

- `type-check` 改为强制 `mypy domain app infra`，删除前导 `-`；
- `test` 改为 `pytest -q`；
- `ci` 继续组合 lint/type-check/test。

#### `pytest.ini`

**为什么改**

`tests/smoke_bm25_rrf.py` 是手工诊断脚本，模块顶层会打开真实 Chroma，不应被 pytest
按测试文件自动收集。

**怎么实现**

- `python_files` 只匹配 `test_*.py`；
- smoke 脚本仍可手工执行，但不再需要 CI `--ignore` 特例。

#### `requirements-dev.txt`

**为什么改**

Starlette 1.4 已迁移到 `httpx2`，继续回退旧 `httpx` 会产生弃用 warning。

**怎么实现**

- 增加已实际解析验证的 `httpx2==2.10.0`；
- 保留项目其它工具依赖。

### 3.4 mypy 真实类型修复

#### 领域模型：`domain/agent.py`、`domain/assessments.py`、`domain/cases.py`、
`domain/documents.py`、`domain/facts.py`、`domain/runs.py`

**为什么改**

- Pydantic v2 `model_copy()` 已返回当前模型类型，旧 `cast()` 冗余；
- Python 3.12 提供 `StrEnum`，无需 `str, Enum` 双继承。

**怎么实现**

- 删除冗余 cast，不改变 immutable snapshot 语义；
- `AgentEventType` 改为 `StrEnum`，序列化值保持不变。

#### 应用层：`app/memory/assembler.py`、`app/use_cases/assessment_runs.py`、
`case_management.py`、`evidence_qa.py`、`evidence_search.py`、
`policy_management.py`、`visual_evidence.py`、`app/workers/document_processing.py`

**为什么改**

mypy 发现了可选 query、可选 assessment_date、两个不同 Chunk 类型复用变量名、角色集合
类型过宽和 Worker 状态字符串过宽等真实边界问题。

**怎么实现**

- 语义召回前先归一化非空 query；
- Run 恢复时 `_require_assessment_date()` fail closed；
- regulatory `Chunk` 与 case `EvidenceChunk` 使用不同变量；
- Citation 最终通过 `EvidenceQACitation.model_validate()`；
- Reviewer 角色集合使用 `WorkspaceRole`；
- Worker 下一状态使用 `DocumentStatus/ProcessingStage`；
- 删除 Repository 返回值的冗余 cast。

#### 基础设施：`infra/agents/model.py`、`langchain_copilot.py`、
`infra/memory/fact_store.py`、`infra/storage/sqlite_document_repo.py`、
`sqlite_task_repo.py`、`infra/web/searcher.py`

**为什么改**

第三方 SDK 的复杂泛型不应泄漏到业务层；数据库字符串必须先验证再收窄为 Literal。

**怎么实现**

- ChatOpenAI Key 使用 `SecretStr`；
- LangChain 编译 Graph 在 infra 内标记为 `Any`；
- ToolCall status 显式使用 `ToolCallStatus`；
- Chroma 使用本项目最小 `_ChromaCollectionLike` Protocol；
- SQLite 状态字符串校验后 cast 为领域 Literal；
- 旧 SQLite Row 缺 `mode` 时用 `try/except` 兼容；
- Bing 参数统一为 HTTP 可接受字符串。

#### 检索/解析：`retrieval/search/fusion.py`、`reranker.py`、`retriever.py`、
`processing/splitter.py`、`ingestion/pdf_extractor.py`、
`evaluations/chunk_params/run.py`

**为什么改**

全量 Ruff/mypy 暴露 Optional、容器元素类型、可选依赖和脚本导入顺序问题。

**怎么实现**

- RRF `weights` 标注 Optional，并用 `ValueError` 替代运行时可关闭的 assert；
- Reranker 使用 `zip(strict=True)`；
- 去重/overlap 列表显式类型；
- 删除已无必要的第三方 import ignore；
- chunk 评测脚本使用函数内延迟导入，保留任意 CWD 执行能力；
- `retrieval/search/__init__.py` 用 `__all__` 声明公共导出。

### 3.5 warning 与安全默认

#### `infra/auth/jwt_issuer.py`、`tests/infra/test_jwt_issuer.py`、
`tests/infra/test_auth_service.py`

**为什么改**

PyJWT 对低于 32 字节的 HS256 Key 发出安全 warning，测试不应使用弱密钥。

**怎么实现**

- JwtIssuer 最低长度从 16 提升到 32；
- 测试使用 32 字节以上固定 Key；
- 篡改测试改签名段首字符，避免修改 Base64URL 末尾未使用 padding bit 导致偶发未篡改。

#### `processing/metadata.py`

**为什么改**

`datetime.utcnow()` 已弃用且返回无时区时间。

**怎么实现**

- 改为 `datetime.now(UTC).isoformat()`。

#### `api/v2/documents.py`

**为什么改**

FastAPI/Starlette 已弃用旧 413 常量名称。

**怎么实现**

- 改为 `HTTP_413_CONTENT_TOO_LARGE`，HTTP 数值和 API 行为不变。

#### `evaluations/evidence_qa/evaluator.py`、`run_verifier.py`

**为什么改**

Python 3.12 Ruff 要求使用标准 `UTC` 别名。

**怎么实现**

- `datetime.now(UTC)` 替代 `timezone.utc`。

### 3.6 全仓格式化文件

**为什么改**

CI 要启用 `ruff format --check .`，必须先有一次可审计的全仓统一格式化。格式化不会改变
AST 或业务行为。

**怎么实现**

执行：

```bash
ruff check --fix .
ruff format .
```

其中以下 83 个 Python 文件经 AST 比较确认只有格式变化：

```text
api/v2/audit.py
api/v2/auth.py
api/v2/copilot.py
api/v2/deps.py
api/v2/errors.py
api/v2/ratelimit.py
api/v2/router.py
api/v2/schemas.py
api/v2/sse.py
api/v3/documents.py
api/v3/visual.py
app/logging_setup.py
app/request_context.py
app/use_cases/forget_memory.py
app/use_cases/memory_settings.py
app/use_cases/run_copilot.py
app/use_cases/task_management.py
domain/models.py
domain/policy_engine.py
evaluations/memory_extraction/evaluator.py
evaluations/visual_retrieval/evaluator.py
infra/auth/auth_service.py
infra/auth/github_oauth.py
infra/kb/chroma_kb_repo.py
infra/memory/consolidation.py
infra/memory/scheduler.py
infra/memory/task_memory.py
infra/observability/tracing.py
infra/research/langgraph_research.py
infra/risk_profile/http_client.py
infra/search/hybrid_retriever.py
infra/storage/_db.py
infra/storage/sqlite_consolidation_state.py
infra/storage/sqlite_feedback_repo.py
infra/storage/sqlite_summary_store.py
infra/storage/sqlite_user_repo.py
infra/storage/sqlite_visual_index.py
infra/visual/chinese_clip.py
infra/workflows/langgraph_runtime.py
retrieval/__init__.py
retrieval/search/bm25_index.py
retrieval/search/vector_store.py
tests/api/test_audit.py
tests/api/test_auth.py
tests/api/test_copilot.py
tests/api/test_copilot_sse.py
tests/api/test_documents.py
tests/api/test_memory.py
tests/api/test_request_id_propagation.py
tests/api/test_tasks.py
tests/api/test_v3_documents.py
tests/app/test_factories.py
tests/app/test_forget_memory.py
tests/app/test_kb_management.py
tests/app/test_logging_setup.py
tests/app/test_memory_assembler.py
tests/app/test_memory_settings.py
tests/app/test_run_copilot.py
tests/evaluations/test_visual_retrieval_evaluator.py
tests/fakes/fake_agent_model.py
tests/fakes/fake_document_loader.py
tests/fakes/fake_fact_store.py
tests/fakes/fake_kb_repo.py
tests/fakes/fake_memory.py
tests/fakes/fake_repos.py
tests/infra/test_chroma_kb_repo.py
tests/infra/test_consolidation.py
tests/infra/test_fakes.py
tests/infra/test_github_oauth.py
tests/infra/test_hybrid_retriever.py
tests/infra/test_langchain_copilot.py
tests/infra/test_langgraph_research.py
tests/infra/test_langgraph_runtime.py
tests/infra/test_memory_scheduler.py
tests/infra/test_service_adapters.py
tests/infra/test_sqlite_audit_repo.py
tests/infra/test_sqlite_consolidation_state.py
tests/infra/test_sqlite_document_repo.py
tests/infra/test_sqlite_repos.py
tests/infra/test_sqlite_visual_index.py
tests/infra/test_task_memory.py
tests/infra/test_vector_store_owner.py
tests/live/test_rag_pipeline.py
```

其余 Python 文件除格式化外还包含前述 Ruff 自动修复、类型收窄、测试增强或业务边界变化，
已在 3.1～3.5 按职责解释。

## 4. 数据模型变化

没有新增业务实体或持久化 schema。

领域协议新增 `ReadinessPort`，Port 总数从 40 增至 41；这是基础设施状态协议，不是新的
业务模型。已有 Case、Document、Fact、Assessment、Run 等模型字段不变。

## 5. API 变化

已新增/调整：

- `GET /api/v2/health/live`
- `GET /api/v2/health` 保持原响应，兼容现有调用方；
- `GET /api/v2/health/ready` 从“对象已装配”升级为真实基础设施探针；
- ready 时 200，数据库或已配置 Redis 不可用时 503；
- API 业务路由、请求和响应模型未破坏兼容。

## 6. Agent 状态变化

无。

Case Assessment Graph 节点、interrupt、checkpoint 和状态转换均未修改。类型修复只让
Run 恢复在 `assessment_date` 缺失时显式 fail closed，不改变正常路径。

## 7. 测试结果

### 修改前

- 完整离线：`1208 passed, 1 skipped, 68 warnings`
- 零密钥 import：失败，`RuntimeError: OPENAI_API_KEY 未配置`
- Ruff 全仓：136 项
- mypy `domain app infra`：111 项
- format：大量文件未格式化

### 修改后零密钥全量门禁

命令：

```bash
PATH="$PWD/.venv/bin:$PATH" make ci
```

结果：

```text
ruff check .
All checks passed!

ruff format --check .
342 files already formatted

mypy domain app infra
Success: no issues found in 112 source files

pytest -q
1219 passed, 1 skipped, 5 warnings in 19.93s
```

零密钥收集：

```text
1214 tests collected
```

最终测试数增加 11，来自配置、health/readiness 和相关回归测试。

## 8. 尚未解决的风险

1. 剩余 5 条 warning 来自 PyMuPDF/RapidOCR 依赖的 SWIG 类型缺少 `__module__`，不在
   本仓库代码内；未使用全局 warning ignore 掩盖；
2. 当前 readiness 的 Redis 在未配置时为 `disabled`，Phase 4 引入 Celery 后生产
   Compose 会配置 `REDIS_URL` 并成为必选；
3. SQLite readiness 只做 `SELECT 1`，Phase 2 PostgreSQL 会替换为数据库 profile
   对应探针；
4. 全仓一次性格式化造成较大 diff，但已用 AST 比较区分 83 个纯格式文件；
5. 当前 Docker Compose 仍只有 app，Phase 9 才完成完整生产拓扑。

## 9. 下一阶段建议

进入 Phase 2，先写 PostgreSQL schema/Repository 迁移设计，再按核心聚合分批接入：

1. SQLAlchemy 2.x Base 和映射边界；
2. Alembic 空库迁移；
3. `STORAGE_BACKEND=sqlite|postgres`；
4. Workspace/Case/Document/ProcessingJob 第一批；
5. Fact/Policy/Assessment/Run/Event 第二批；
6. Repository contract 与并发 Run 唯一测试。

## 10. 验收标准

| 验收项 | 状态 |
| --- | --- |
| 零密钥裸 pytest 通过 | 满足：`1219 passed, 1 skipped` |
| Ruff 全仓通过 | 满足：`All checks passed` |
| Ruff format 全仓通过 | 满足：342 files formatted |
| mypy domain/app/infra 通过 | 满足：112 source files |
| liveness/readiness 语义分离 | 满足：live 无依赖，ready 实际探针 |
| 主要 warning 清理 | 满足：68 降至 5，剩余为第三方 SWIG |
| CI 使用上述完整命令 | 满足：GitHub Actions 与 `make ci` 对齐 |

Phase 1 验收通过。
