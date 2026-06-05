# Step 008 — PR-5 App 层骨架（DI 容器 + use case，无 Agent）

## 1. 本步骤目标

Step 005-007 把 4 层架构的下半部装好了（domain + infra）；本步骤搭"应用层骨架"，让所有 Port 实现通过一个 `AppContainer` 统一装配，并落地 4 个最基础的 use case，让上层（API/Agent）有可调用入口。

- **为什么存在**：infra 适配器写完了，但谁来 new 它们？谁来给 use case 注入？谁来保证两个 SqliteRepo 用同一个连接池？—— `AppContainer`。
- **服务于哪层**：app 层。本身不动 api 路由，但为 Step 010 把老 `service.py` Strangler 替换出去做准备。
- **为后续提供什么**：
  - `AppContainer` —— DI 中央配电盘，8 个 Port + 4 个 use case
  - `app/factories.py` —— 9 个 `build_*` 工厂函数，可独立用于评测脚本
  - `AuthLoginUseCase / TaskManagementUseCase / IngestionUseCase / RunQueryUseCase` —— 4 个无 Agent 业务编排
  - Step 009 (PR-5b) Agent + tools 只需在 container 加 2 个字段
  - Step 010 (PR-6) API 路由从 container 拿 use case 即可

## 2. 修改文件

| 路径 | 说明 |
|---|---|
| `config.py` | 加 6 个字段：`sqlite_db_path / jwt_secret / jwt_ttl_seconds / github_client_id / github_client_secret / github_redirect_uri`（全部带 dev 默认值，向后兼容） |
| `app/__init__.py` | 出口：`AppContainer` |
| `app/factories.py` | 9 个 `build_*`：sqlite_pool / user_repo / task_repo / embedder / chat / retriever / web_search / evidence / auth |
| `app/container.py` | `AppContainer(settings, *, **overrides)`：构造时所有 Port 可注入；默认走工厂 |
| `app/use_cases/__init__.py` | 出口：4 个 use case |
| `app/use_cases/auth_login.py` | `AuthLoginUseCase`：begin/complete/anonymous/identify/require |
| `app/use_cases/task_management.py` | `TaskManagementUseCase`：create/list/get/delete/update_facts + 消息读写，全部强制 owner_id 校验 |
| `app/use_cases/ingest.py` | `IngestionUseCase`：占位 stub，仅走 EmbedPort 验链路通 |
| `app/use_cases/run_query.py` | `RunQueryUseCase`：retrieve→拼 context→chat，Step 009 后会被 Agent 调用替换 |
| `tests/app/__init__.py` | 空 |
| `tests/app/test_container.py` | 3 个用例：8 Port 契约 / 4 use case 装配 / 实例复用断言（auth_login.\_auth is container.auth） |
| `tests/app/test_factories.py` | 9 个 build_* 烟雾测试 + auth 匿名 round-trip |
| `tests/app/test_auth_login.py` | 9 用例：begin/complete/anonymous + identify(空/无效/有效) + require(抛 InvalidToken) |
| `tests/app/test_task_management.py` | 11 用例：create / owner 隔离 / facts merge / 消息追加权限 / PermissionError |
| `tests/app/test_ingest.py` | 3 用例：owner 守卫 + 空文本短路 + embedding_dim |
| `tests/app/test_run_query.py` | 4 用例：happy path + 空检索 + 空 query + corpus 透传 |

## 3. 设计决策

### 3.1 AppContainer 用"工厂 + 可选覆盖"模式而非依赖注入框架

```python
class AppContainer:
    def __init__(self, settings, *, user_repo=None, task_repo=None, ...):
        self.user_repo = user_repo or build_user_repo(settings, pool=pool)
        ...
```

为什么不用 `dependency-injector` 或 `inject`：
- 项目只有 8 个 Port，手写一组 `self.xxx = ... or build_xxx(...)` 比学一个 DI 库 API 更直观
- 测试只需 `AppContainer(settings, auth=FakeAuth(), task_repo=InMemoryTaskRepo(), ...)`
- FastAPI、Flask、Pyramid 等主流 web 框架的官方 DI 也都是这种"手写容器"模式

### 3.2 SQLite 单连接池给两个 Repo 共享

`build_sqlite_pool(settings)` 调一次，传给 `SqliteUserRepo(pool)` 和 `SqliteTaskRepo(pool)`。这是 DI 容器存在的根本理由之一 —— 如果让两个 repo 各自 new pool，并发写时锁竞争立刻出现（SQLite 单写者）。

容器内部判断："只要 user_repo / task_repo 任一未注入，就 build 一个 pool 给它们共享"。

### 3.3 use case 严格走 Port，从不知道具体实现

```python
class TaskManagementUseCase:
    def __init__(self, task_repo: TaskRepoPort) -> None:  # ← Port，不是 SqliteTaskRepo
        self._repo = task_repo
```

后果：
- 单测一律用 `InMemoryTaskRepo`，~1ms 一个 case
- 评测脚本可以用 `InMemoryTaskRepo` 跑 benchmark，不需要 SQLite 文件
- 想换 PostgreSQL，只需要写 `PostgresTaskRepo(TaskRepoPort)` + 改 factories.py 一行

### 3.4 强制 owner_id 隔离写到 use case 而非 repo

`TaskManagementUseCase.list_messages(task_id, owner_id)` 先校 `repo.get(task_id, owner_id)`，找不到就抛 `PermissionError`。**这一层不能省**。理由：
- Repo 是无状态 CRUD，不知道"谁有权访问"
- API 层（Step 010）只接管 token → owner_id 这一段；具体的"按 owner 过滤"应当在 use case 里强制
- spec §4.2 明确：`task_repo.get(task_id, owner_id)` 已经按 owner 过滤，但**写**操作（append_message）的 owner 校验必须在 use case 显式 ensure

### 3.5 ingest / run_query 是"占位 + 验链路"，故意不全功能

- `IngestionUseCase.ingest_texts(owner_id, texts)` 只调 EmbedPort，不分块、不落 chroma。真实分块/落库管线已经在 `ingestion/` + `processing/` 现成可用，Step 010 接 API 时再 wire；当前只验"EmbedPort 链路通"。
- `RunQueryUseCase` 是"无 Agent 的 RAG 简化版"，把 RetrievePort + ChatPort 串成 retrieve→拼 context→chat。**仅是过渡占位**：Step 009 引入 `ComplianceCopilotAgent` 后，本 use case 会被改成 "agent.run(...)" 的薄壳。

### 3.6 config.py 加字段而非拆模块

spec PR-5 提到"`config.py` 拆 `settings.py` + `factories.py`"。本步骤选择**只加字段不拆模块**：
- 现有 13+ 个文件 `from config import settings`，拆模块需要同步改所有 import，与"重构不动业务"原则冲突
- pydantic-settings 的 `BaseSettings` 在一个类里放 30+ 字段完全合理，社区主流写法
- 拆 factories 已通过新建 `app/factories.py` 完成
- 真正需要"拆 Settings"是出现命名空间冲突或 Settings 类超过 100 行时；当前 60 行

## 4. 核心契约 / 接口

```python
class AppContainer:
    # Ports
    user_repo: UserRepoPort
    task_repo: TaskRepoPort
    embedder: EmbedPort
    chat: ChatPort
    retriever: RetrievePort
    web_search: WebSearchPort
    evidence: EvidencePort
    auth: AuthPort
    # Use cases
    auth_login: AuthLoginUseCase
    task_management: TaskManagementUseCase
    ingest: IngestionUseCase
    run_query: RunQueryUseCase

class AuthLoginUseCase:
    def begin(provider) -> tuple[str, str]
    def complete(provider, code, state) -> tuple[User, str]   # User + JWT
    def login_anonymous() -> tuple[User, str]
    def identify(token) -> str | None
    def require(token) -> str   # 失败抛 InvalidToken

class TaskManagementUseCase:
    def create_task(owner_id, *, title, user_goal) -> Task
    def list_tasks(owner_id, *, limit) -> list[Task]
    def get_task(task_id, owner_id) -> Task | None
    def delete_task(task_id, owner_id) -> bool
    def update_facts(task_id, owner_id, facts) -> Task | None
    def append_user_message(task_id, owner_id, content) -> Message
    def append_assistant_message(task_id, owner_id, content, *, citations) -> Message
    def list_messages(task_id, owner_id) -> list[Message]

class IngestionUseCase:
    def ingest_texts(owner_id, texts) -> {owner_id, text_count, embedding_dim}

class RunQueryUseCase:
    def answer(owner_id, query, *, top_k, corpus, temperature) -> {answer, citations, used_chunks}
```

错误模型：
- OAuth 流程错（unknown provider / bad state / code 换 token 失败）→ `OAuthFlowError`
- token 无效但调用 `require()` → `InvalidToken`
- 任务越权访问（owner 不匹配）→ `PermissionError`（写）或 `None`（读 / update_facts / delete）

## 5. 与外部服务的关系

| 依赖 | 谁注入 | 测试隔离 |
|---|---|---|
| SQLite | `build_sqlite_pool(settings)` 单例 | 测试用 `tmp_path` / `:memory:` 或全 fake |
| 智谱/OpenAI Chat | `OpenAIChatAdapter()` 懒构造 ChatClient | `FakeChat(responses=[...])` |
| 智谱/Ollama Embedding | `EmbedderAdapter()` 懒构造 | `FakeEmbed(dim=8)` |
| chroma + BM25 + Reranker | `HybridRetrieverAdapter()` 懒构造完整流水线 | `FakeRetrieve(chunks=[...])` |
| GitHub OAuth | `build_auth` 用 settings 4 个字段 | `FakeAuth()` 走全内存 |
| DuckDuckGo | `DuckDuckGoAdapter()` 懒构造 | `FakeWebSearch(results=[...])` |
| Evidence 服务 | `MockEvidenceClient()`（spec 阶段未接真服务） | `FakeEvidence(judgements=[...])` |

`config.py` 的 6 个 dev 默认值（jwt_secret / github_*）足以让单测/启动跑通，**生产部署必须从 env 覆盖**。

## 6. 当前实现范围

**已实现**：
- 9 个 `build_*` 工厂全部 isinstance(Port) 验证通过
- `AppContainer` 全工厂模式与全注入模式都能构造
- 4 个 use case 主流程 + 边界 + 越权 case 全覆盖
- 单连接池在 user_repo / task_repo 间共享（避免并发 SQLite 锁）
- `auth_login._auth is container.auth` 实例复用断言通过
- 累计 **264 passed**（Step 007 是 224，+40）

**按设计未实现**：
- `ComplianceCopilotAgent` + `register_default_tools`（Step 009 PR-5b）
- `MemoryPort` 实现 `infra/memory/*`（Step 011 PR-6）
- `IngestionUseCase` 接 `ingestion/` 真实分块 + chroma 落库（Step 010）
- `RunQueryUseCase` 替换为 Agent 调用（Step 009 后）
- API 层路由切换（Step 010）
- `service.py` 删除（Strangler Fig，PR-7）

## 7. 暂未实现 / TODO

- [ ] Step 009：`ComplianceCopilotAgent` ReAct 主循环 + `ToolSpec` + `register_default_tools(container)`
- [ ] Step 010：FastAPI 路由通过 `container.auth_login / .task_management / .run_query` 提供端点；老 routes 标 deprecated
- [ ] Step 011：`infra/memory/{short_term,summary,profile,semantic}.py` 实现 L1-L4 记忆
- [ ] Settings 真正读 `.env` 的 `GITHUB_CLIENT_ID` 等（pydantic-settings 已支持，只是 dev 默认值挡着）
- [ ] `IngestionUseCase` 接 `ingestion/unified_loader` + 真实分块 + chroma 落库

## 8. 测试与验证

```powershell
# 单元 + 集成
pytest -q --no-cov
# 264 passed, 16 warnings in 10.68s  （Step 007 是 224）

# 类型（app/ 全绿，retrieval/* 42 个老遗留与本步无关）
mypy app
# Found 42 errors in 11 files —— 全部在 retrieval/* 老代码，app/ 0 错

# Lint
ruff check app tests/app
# All checks passed!
```

新增测试覆盖：
- `tests/app/test_container.py`：3 case（Port 契约 / use case 装配 / 实例复用）
- `tests/app/test_factories.py`：9 case（9 个 build_* + 匿名 round-trip）
- `tests/app/test_auth_login.py`：9 case
- `tests/app/test_task_management.py`：11 case
- `tests/app/test_ingest.py`：3 case
- `tests/app/test_run_query.py`：4 case

合计 **+40 case**，仓库累计 **264 passed**（Step 007 是 224）。
