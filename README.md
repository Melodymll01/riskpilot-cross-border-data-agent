# 数智合规 · 基于 Agentic RAG 的数据出境合规问答系统

[![CI](https://github.com/Melodymll01/riskpilot-cross-border-data-agent/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Melodymll01/riskpilot-cross-border-data-agent/actions/workflows/ci.yml)
[![tests](https://img.shields.io/badge/tests-527%20passed-brightgreen)](https://github.com/Melodymll01/riskpilot-cross-border-data-agent/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.12-blue)](pyproject.toml)
[![ruff](https://img.shields.io/badge/ruff-scoped--clean-46a)](.github/workflows/ci.yml)
[![arch](https://img.shields.io/badge/arch-DDD%204--layer-9b5bff)](docs/architecture/overview.md)

> 面向 **数据出境合规**（《个人信息保护法》《数据安全法》《网络安全法》三法 + 标准合同 / 安全评估 / 个保认证三路径）场景的对话式合规助手。
>
> 工程上从 v1 单体 Service 演进到 **v2 DDD 4 层架构（13 Port + 6 Use Case）**，两套 API 通过 Strangler Fig 模式并存，渐进迁移不停服。

## 项目演进

```text
v1 (Step 001-007)              v2 重构 (Step 008-023)
─────────────────              ─────────────────────────────────
单体 service.py     ──┐        ┌── DDD 4 层：domain / infra / app / api
api/routes.py         │        │
单一前端 HTML/JS      │        │   13 Port + 6 Use Case
                      │   ───▶ │   GitHub OAuth + JWT + admin 白名单
评测脚本              │        │   KB 管理面（admin 写 / 用户读）
ChromaDB + jieba BM25 │        │   admin 审计日志（写入 + UI 闭环）
                      └────────┤   GitHub Actions CI（scoped ruff + pytest）
                               │   13 ADR 全量索引
                               └── ⇄ v1 API 共存（Strangler Fig，渐进迁移）
```

## 关键指标

| 维度 | 数值 | 来源 |
| --- | --- | --- |
| 测试用例 | **527 passed** | `pytest -q --ignore=evaluations/ood/eval_ood.py --ignore=tests/smoke_bm25_rrf.py` |
| 代码质量 | scoped ruff 0 errors（14 路径） | [.github/workflows/ci.yml](.github/workflows/ci.yml) |
| Top-K=2 检索命中率 | **93.3%** | [chunk_eval_latest.json](evaluations/chunk_params/reports/chunk_eval_latest.json)（chunk_size=300, overlap=60） |
| Top-1 平均语义相似度 | **0.641** | 同上 |
| OOD 误杀率（in-domain） | **0.0%** | [ood_eval_latest.md](evaluations/ood/reports/ood_eval_latest.md) |
| OOD 召回率 | 66.7%（待改进，见下） | 同上 |
| 细分类型软标签准确率 | 70.0% | 同上 |

## 系统架构

### 4 层 DDD 视图（v2，主推荐）

```mermaid
flowchart TB
    subgraph API[api/v2 · 入口层]
        R1[auth.py] --- R2[copilot.py]
        R2 --- R3[documents.py]
        R3 --- R4[audit.py]
        R4 --- R5[tasks.py / health.py / sse.py]
    end

    subgraph APP[app · 用例编排层]
        C[AppContainer · 装配 13 Port]
        U1[AuthLoginUseCase]
        U2[RunCopilotUseCase]
        U3[KbManagementUseCase]
        U4[IngestionUseCase]
        U5[RunQueryUseCase]
        U6[TaskManagementUseCase]
    end

    subgraph DOMAIN[domain · 纯模型 + 端口]
        P[13 Port Protocol<br/>AuthPort / RetrievePort / EvidencePort<br/>KbDocumentRepoPort / AuditLogPort ...]
        M[Frozen Models<br/>User / Task / KbChunk / AuditEntry ...]
    end

    subgraph INFRA[infra · 适配器]
        I1[infra/auth<br/>GitHubOAuth + JwtIssuer]
        I2[infra/kb<br/>ChromaKbRepo]
        I3[infra/storage<br/>SQLite Repos + Pool]
        I4[infra/audit<br/>SqliteAuditLogRepo]
        I5[infra/llm<br/>OpenAIChatPort]
        I6[retrieval/<br/>HybridRetriever + ReAct Agent]
    end

    API --> APP
    APP --> DOMAIN
    INFRA -.实现.-> DOMAIN
    APP -.通过 Container 装配.-> INFRA
```

依赖方向：`api → app → domain`，`infra` 反向依赖 `domain`（端口实现）。**domain 层不依赖任何外部框架**，保证可单元测试。详见 [docs/architecture/overview.md](docs/architecture/overview.md)。

### Agentic RAG 决策环路

```mermaid
flowchart LR
    subgraph Ingest[知识接入]
        A1[PDF/TXT/DOCX] --> L[unified_loader]
        A2[URL 网页] --> L
        L --> C[cleaner] --> S[splitter] --> M[metadata]
        M --> E[Embedder<br/>智谱 embedding-3]
        M --> B[BM25 + jieba]
        E --> V[(ChromaDB)]
    end

    subgraph Agent[ReAct 自反思环路]
        Q[用户问题] --> QC[问题分类<br/>5 类]
        QC --> QT[查询变换<br/>改写/拆解/HyDE]
        QT --> R[混合检索<br/>Vector+BM25 RRF]
        V -.-> R
        B -.-> R
        R --> RR[Cross-Encoder<br/>bge-reranker-base]
        RR --> EC{证据质量}
        EC -- partial --> QT
        EC -- insufficient --> WS[Web 搜索兜底]
        WS --> RR
        EC -- sufficient --> GEN[LLM 生成<br/>带引用溯源]
    end

    GEN --> ANS[回答 + 引用 + AgentEvent 流]
```

## 功能矩阵

| 能力 | 匿名用户 | 普通登录用户 | admin |
| --- | :---: | :---: | :---: |
| 对话式问答（QA 模式） | ✅ | ✅ | ✅ |
| 深度研究（多轮检索 + 长报告） | ✅ | ✅ | ✅ |
| 风险画像（接口预留） | ✅ | ✅ | ✅ |
| 任务历史持久化 | ✅（匿名 ID） | ✅（GitHub user_id） | ✅ |
| 知识库查看（list / stats / detail） | ❌ | ✅ | ✅ |
| 知识库写入（上传 / 采集 / 删除） | ❌ | ❌ | ✅ |
| 审计日志查看 | ❌ | ❌ | ✅ |
| SSE 流式输出 | ✅ | ✅ | ✅ |

身份模型：`AuthPort` 支持 **匿名兜底**（首访自动 POST `/auth/anonymous` 落 cookie）+ **GitHub OAuth**（state 防 CSRF + JWT 颁发）+ **admin 白名单**（`ADMIN_USER_IDS` 命中即 `is_admin=True`）。

## 工程亮点

- **DDD 4 层架构**：domain 纯 Python + 13 Port Protocol；infra 适配器；app 用例编排 + Container DI；api 入口（v1 + v2 双栈）
- **Strangler Fig 渐进重构**：v1 `service.py` 不动，v2 路由通过闭包 `build_*_routes(container)` 注入依赖；旧端点逐步下线（Step 016d 已删 5 个 KB 写端点）
- **审计副作用语义**：admin 在 KB 上的写操作（delete / ingest_file / ingest_web）成功失败都落 audit；audit 写失败仅 `logger.warning` 不影响主业务（[ADR-013](docs/decisions/ADR-013-audit-side-effect-semantics.md)）
- **自实现 ReAct Agent**：不依赖 LangChain，纯 Python + LLM JSON 决策协议；9 类 `AgentEvent` 流式推送给前端（[ADR-001](docs/decisions/ADR-001-no-langchain.md) / [ADR-011](docs/decisions/ADR-011-react-agent-self-implemented.md)）
- **混合检索**：向量 + BM25 + **RRF 融合** + Cross-Encoder 重排序，4 级检索增强
- **CI 持续集成**：GitHub Actions 复活后 scoped ruff（14 路径）+ pytest 8 个 Fake 环境变量，每 push 自动跑

## 评测体系

项目内置三套评测，所有报告归档在 [evaluations/](evaluations/)：

1. **切块参数调优**（[chunk_params/run.py](evaluations/chunk_params/run.py)）—— 网格搜索 chunk_size × overlap，以命中率与 Top-1 相似度为指标，定型当前默认参数
2. **OOD 与细分类型分类**（[tests/eval_ood.py](tests/eval_ood.py)）—— 32 条样本（in-domain 20 + OOD 12），评估问题分类器的准召与软标签准确率
3. **端到端基准**（[benchmark/run.py](evaluations/benchmark/run.py)）—— 测量整链路延迟与回答质量

### 当前已识别的待改进项（坦诚记录，避免简历刷分式包装）

- **OOD 召回率 66.7% 未达自定目标 85%**：4 条 OOD 样本被误判为 in-domain（翻译、行程查询、邮件起草、跨法域对比），分类器对"借用领域关键词的非问答意图"识别不足。
  - 改进方向：① 在分类 prompt 中补充上述 bad case 作为 few-shot 反例；② OOD 探针检索阶段引入 distance + Top-K 一致性的联合判据，而非仅看 Top-1 distance。
- **细分类型严格准确率 55%（软标签 70%）**：`definition` 与 `condition` 经常互相混淆（如"法律定义"被误判为"条件触发"）。
  - 改进方向：拆解 prompt，将"问的是 *是什么* 还是 *什么情况下*"显式列为判别要点；考虑用小样本微调一个轻量分类头替代纯 prompt 路线。

## 项目结构

```
RagDataOut/
├── main.py                 # FastAPI 主入口（v1 + v2 双路由挂载）
├── config.py               # 全局配置（pydantic-settings + 启动期校验）
├── service.py              # v1 单体服务层（保留，逐步收敛）
├── pyproject.toml          # 项目元数据 & 测试/工具配置
├── requirements.txt        # 运行时依赖
├── requirements-dev.txt    # 开发依赖（ruff / mypy / pytest）
├── pytest.ini              # pytest 配置
├── Makefile                # 常用命令
├── .env.example            # 环境变量模板（提交）
├── .env                    # 真实密钥 + ADMIN_USER_IDS（不提交）
├── Dockerfile              # 多阶段构建
├── docker-compose.yml      # 一键启动
├── .github/workflows/      # CI 流水线（scoped ruff + pytest）
│
├── domain/                 # ★ 纯模型 + Port Protocol（不依赖任何框架）
│   ├── models.py           # User / Task / Message / KbChunk / AuditEntry ...（frozen）
│   └── ports.py            # 13 个 Port Protocol（runtime_checkable）
│
├── app/                    # ★ 用例编排层
│   ├── container.py        # AppContainer · 装配所有 Port
│   ├── factories.py        # 各 Port 的默认 build_* 工厂
│   └── use_cases/          # 6 个 Use Case（AuthLogin / RunCopilot / KbManagement ...）
│
├── infra/                  # ★ 适配器实现层
│   ├── auth/               # GitHubOAuth + JwtIssuer + FakeAuth（测试）
│   ├── kb/                 # ChromaKbRepo
│   ├── storage/            # SQLite Pool + User/Task Repo
│   ├── audit/              # SqliteAuditLogRepo（Step 021）
│   ├── llm/                # OpenAIChatPort 适配
│   ├── memory/             # 会话画像 + 事实抽取
│   └── risk/               # RiskProfile Stub（接口预留）
│
├── api/                    # FastAPI 入口
│   ├── routes.py           # v1（兼容老前端，逐步收敛）
│   ├── schemas.py
│   └── v2/                 # ★ v2 新栈（auth / copilot / documents / audit / tasks / sse / health）
│
├── retrieval/              # 检索 + 生成 + Agent
│   ├── search/             # embedder / vector_store / bm25 / fusion / reranker / retriever
│   ├── generation/         # qa_chain / chat_client / report_generator
│   └── agent/              # agentic_rag · ReAct 自实现 + 9 类 AgentEvent
│
├── ingestion/              # PDF/TXT/DOCX/URL 接入
├── processing/             # cleaner / splitter / metadata
│
├── frontend/               # 单页前端（ES module，无构建依赖）
│   ├── index.html
│   ├── app.js              # 入口 + view 切换（chat / kb / audit 三态）
│   ├── api.js              # /api/v2/* REST 客户端
│   ├── auth.js / chat.js / tasks.js / sse.js
│   ├── kb.js               # KB 管理面板（admin 写 / 用户读）
│   └── admin-audit.js      # 审计日志面板（admin-only，Step 023）
│
├── data/                   # 运行时数据（不提交内容）
│   ├── chat_db.py          # 多表 SQLite 封装（user/task/message/kb/audit）
│   ├── chroma_db/          # 向量库
│   ├── embed_cache/        # embedding 缓存
│   └── uploads/            # 用户上传文件
│
├── evaluations/            # 评测脚本与报告
│   ├── benchmark/run.py    # 端到端基准
│   ├── chunk_params/run.py # 切块参数网格搜索
│   └── ood/                # OOD 分类评测（脚本在 tests/eval_ood.py）
│
├── tests/                  # pytest（527 passed · DDD 分层布局）
│   ├── domain/  app/  infra/  api/  fakes/
│   └── conftest.py
│
├── docs/                   # ★ 工程文档
│   ├── architecture/overview.md      # 4 层 + 13 Port 全景（推荐先读）
│   ├── decisions/                    # 13 个 ADR（架构决策记录）
│   └── process/                      # Step 001-023 演进日志（每个 Step 一篇）
│
└── logs/                   # 运行日志（不提交）
```

## 快速开始

> **两种启动方式任选其一：**
> - **A. Docker（推荐）**：一键启动，零环境配置
> - **B. 本地 Python**：适合需要调试源码的开发者

---

### 方式 A：Docker 启动（推荐）

> 前置：安装 [Docker Desktop](https://www.docker.com/products/docker-desktop/)（Windows 用户启用 WSL2 后端）。

```bash
# 1. 克隆仓库
git clone https://github.com/Melodymll01/riskpilot-cross-border-data-agent.git
cd riskpilot-cross-border-data-agent

# 2. 配置环境变量（仅需填一个 API Key）
copy .env.example .env       # Windows
# cp .env.example .env       # macOS / Linux
# 编辑 .env，填入 OPENAI_API_KEY

# 3. 一键启动（首次构建约 5-10 分钟，之后秒启）
docker compose up -d

# 4. 查看日志 / 停止 / 重启
docker compose logs -f
docker compose down
docker compose restart
```

启动后访问 <http://localhost:8001> 即可。

数据卷挂载说明：
- `./data` → 容器内 `/app/data`：向量库、上传文件、聊天历史持久化
- `./logs` → 容器内 `/app/logs`：运行日志
- `hf-cache`（Docker 命名卷）：HuggingFace 模型缓存，避免重复下载 reranker

> ⚠️ **密钥安全**：`.env` 仅在本机，**不会**被打进镜像。`.dockerignore` 已显式排除 `.env`，可放心 `docker push` 或分享镜像。

---

### 方式 B：本地 Python 启动

> **前置要求**：Python 3.10+、Git；如启用本地推理还需安装 [Ollama](https://ollama.com)。

#### 1. 克隆仓库

```bash
git clone https://github.com/Melodymll01/riskpilot-cross-border-data-agent.git
cd riskpilot-cross-border-data-agent
```

#### 2. 创建并激活虚拟环境

```bash
python -m venv .venv

# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate
```

#### 3. 安装依赖

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

#### 4. 配置环境变量（必做）

```bash
copy .env.example .env       # Windows
# cp .env.example .env       # macOS / Linux
```

打开 `.env`，**至少填写以下两项**，其余保持默认即可：

```ini
OPENAI_API_KEY=<在智谱开放平台 https://open.bigmodel.cn 申请>
OPENAI_API_BASE=https://open.bigmodel.cn/api/paas/v4
```

完整示例（智谱 GLM 通道，与 `config.py` 默认值对齐）：

```ini
LLM_PROVIDER=api
EMBED_PROVIDER=api
OPENAI_API_KEY=your-key-here
OPENAI_API_BASE=https://open.bigmodel.cn/api/paas/v4
CHAT_MODEL=glm-4-flash
EMBEDDING_MODEL=embedding-3
CHUNK_SIZE=400
CHUNK_OVERLAP=80
TOP_K=5

# v2 鉴权（可选；不填则只能匿名 + 普通登录用户）
GITHUB_CLIENT_ID=<在 https://github.com/settings/developers 申请>
GITHUB_CLIENT_SECRET=<同上>
GITHUB_REDIRECT_URI=http://localhost:8765/api/v2/auth/github/callback
JWT_SECRET=<随机 32+ 字符>
ADMIN_USER_IDS=github:your-github-login     # 逗号分隔多个；命中即 is_admin=True
```

> ⚠️ **安全提示**：`.env` 已在 `.gitignore` 中，**严禁** `git add .env`；提交前用 `git status` 二次确认。`ADMIN_USER_IDS` 同时支持逗号分隔与 JSON 数组两种写法（见 [Step 018 文档](docs/process/step_018_login_bugfix_port_admin.md)）。

#### 5. （可选）运行测试，校验环境

```bash
pytest -q
```

#### 6. 启动服务

```bash
# 开发环境
uvicorn main:app --host 127.0.0.1 --port 8001 --reload

# 生产环境（去掉 --reload，按需调整 workers）
uvicorn main:app --host 0.0.0.0 --port 8001 --workers 2
```

#### 7. 访问系统

- 前端：<http://localhost:8001>
- Swagger API 文档：<http://localhost:8001/docs>

## 使用说明

### 身份与登录

- **首访自动匿名**：进入页面会自动 POST `/auth/anonymous` 落 cookie，可以直接开聊。
- **GitHub 登录**：点右下「使用 GitHub 登录」走 OAuth；登录后任务历史归到 GitHub user_id 名下。
- **admin 角色**：`.env` 的 `ADMIN_USER_IDS` 命中即 `is_admin=True`；侧栏出现 📚 知识库 + 📜 审计日志 两个新入口。

### 三种业务模式（顶部 Tab 切换，对话间不共享上下文）

1. **💬 知识问答**：日常合规查询，单轮检索 + 生成
2. **🔬 深度研究**：多轮检索 + 长报告，适合综述 / 对比 / 方案设计类问题
3. **📊 风险画像**：一句话命题 → 未来返回 evidence-state（接口已预留，模型部署前以普通对话形式回答）

### 知识库（登录用户可读，admin 可写）

- 在侧栏 📚 知识库 入口查看所有已入库文档（含 chunk 数、分类、来源类型）
- admin 额外可见上传 / 网页采集 / 删除按钮；普通用户看到只读 banner
- 写操作（delete / ingest_file / ingest_web）均落 audit_log（见下）

### 审计日志（admin-only）

- 侧栏 📜 审计日志 入口（admin 可见）
- 表格列：时间 / actor / action / resource / 状态徽章 / extra_json 摘要
- 支持按 action 下拉 + actor_id 输入框过滤；下方分页（50/页，offset 模式）

### Web 搜索兜底

当知识库证据不足且配置了搜索引擎 API 时，Agent 自动触发 Web 搜索补充检索；不依赖外部 API 也能跑（仅退化为知识库内回答 + 拒答信号）。

## API 文档

启动服务后访问 <http://localhost:8001/docs> 查看自动生成的 Swagger API 文档（含 v1 + v2 全量端点）。

### v2 端点（DDD 重构后的主推荐栈）

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/v2/auth/anonymous` | 公开 | 创建匿名 session（cookie） |
| GET | `/api/v2/auth/github/login` | 公开 | 拿 GitHub 授权 URL（含 state 防 CSRF） |
| GET | `/api/v2/auth/github/callback` | 公开 | OAuth 回调（302 回前端 + JWT cookie） |
| GET | `/api/v2/auth/me` | 公开 | 当前会话身份（含 `is_admin`） |
| POST | `/api/v2/auth/logout` | 公开 | 清 cookie |
| POST | `/api/v2/copilot/chat` | 登录 | 同步聚合对话 |
| GET | `/api/v2/copilot/stream` | 登录 | **SSE 流式 + AgentEvent** |
| GET / PATCH / DELETE | `/api/v2/tasks/*` | owner | 任务历史 CRUD（owner_id 隔离） |
| GET | `/api/v2/documents` `/stats` `/{name}` | 登录 | KB 读取（任意登录用户） |
| POST | `/api/v2/documents/file` `/web` | **admin** | KB 写入（上传 / 采集） |
| DELETE | `/api/v2/documents/{name}` | **admin** | KB 删除 |
| GET | `/api/v2/audit/logs` | **admin** | 审计日志查询（`limit/offset/action/actor_id` 过滤） |
| GET | `/api/v2/health` `/health/ready` | 公开 | 健康检查 |

### v1 端点（保留，逐步收敛）

| 方法 | 路径 | 状态 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/ask` | 保留 | v1 同步问答（v2 推荐用 `/copilot/chat`） |
| POST | `/api/chat/sse` | 保留 | v1 SSE 流（v2 推荐用 `/copilot/stream`） |
| ~~POST `/api/ingest/file`~~ | ❌ Step 016d 删除 | 已迁移到 `/api/v2/documents/file` |
| ~~POST `/api/ingest/web`~~ | ❌ Step 016d 删除 | 已迁移到 `/api/v2/documents/web` |
| ~~GET `/api/sources`~~ | ❌ Step 016d 删除 | 已迁移到 `/api/v2/documents` |
| ~~DELETE `/api/sources/{name}`~~ | ❌ Step 016d 删除 | 已迁移到 `DELETE /api/v2/documents/{name}` |

## 扩展指南

### 更换 Reranker 模型

系统默认启用 Cross-Encoder 重排序（`BAAI/bge-reranker-base`，中文友好，首次启动会从 HuggingFace 下载约 1.1GB）。
如需更换，在 `.env` 中修改：

```ini
ENABLE_RERANKER=true                              # 关闭设为 false
RERANKER_MODEL=BAAI/bge-reranker-large            # 或 cross-encoder/ms-marco-MiniLM-L-6-v2（英文小模型，约 90MB）
RERANKER_DEVICE=auto                              # cuda / cpu / auto
RERANKER_SCORE_THRESHOLD=0.0                      # 分数阈值过滤
```

底层实现见 [retrieval/search/reranker.py](retrieval/search/reranker.py)，基于 `sentence-transformers` 的 `CrossEncoder`，可在该文件中扩展自定义重排序逻辑。

### 切换 Embedding / Chat 模型

在 `.env` 中修改 `EMBEDDING_MODEL` 和 `CHAT_MODEL`，配合修改 `OPENAI_API_BASE` 可对接任意 OpenAI 兼容接口（如本地部署的 Ollama、vLLM 等）。

## 技术栈

- **后端**：FastAPI + Pydantic v2 + pydantic-settings
- **架构**：DDD 4 层 + 13 Port Protocol + AppContainer 依赖注入
- **存储**：SQLite（user / task / message / audit）+ ChromaDB（向量库）
- **鉴权**：GitHub OAuth + JWT（HS256）+ admin 白名单
- **LLM**：OpenAI 兼容接口（智谱 GLM 默认，可换 Ollama / vLLM）
- **检索**：ChromaDB（向量）+ jieba BM25 + RRF 融合 + bge-reranker-base 重排序
- **Agent**：自实现 ReAct + LLM JSON 决策协议 + 9 类 AgentEvent
- **文档解析**：PyPDF2 + python-docx + BeautifulSoup4
- **前端**：原生 HTML + ES module + CSS（无构建依赖）
- **质量**：pytest（527 passed）+ ruff（scoped-clean）+ GitHub Actions CI

## 项目文档

工程文档全部在 [`docs/`](docs/) 下，建议按下面顺序阅读：

1. **架构全景**：[`docs/architecture/overview.md`](docs/architecture/overview.md) — 4 层 + 13 Port + 6 Use Case + 3 张时序图 + KB 权限矩阵 + CI 现状
2. **架构决策**：[`docs/decisions/`](docs/decisions/) — 13 个 ADR（含演化标记 Augmented-by）
   - [ADR-001 No LangChain](docs/decisions/ADR-001-no-langchain.md) · [ADR-006 4-layer Architecture](docs/decisions/ADR-006-4-layer-architecture.md) · [ADR-007 GitHub OAuth + Anonymous](docs/decisions/ADR-007-github-oauth-with-anonymous.md) · [ADR-008 Owner-ID Tenancy](docs/decisions/ADR-008-owner-id-tenancy.md)
   - [ADR-009 Closure Router + Container DI](docs/decisions/ADR-009-closure-router-container-di.md) · [ADR-010 Strangler Fig v1/v2](docs/decisions/ADR-010-strangler-fig-v1-v2.md) · [ADR-011 ReAct 自实现](docs/decisions/ADR-011-react-agent-self-implemented.md) · [ADR-012 Admin RBAC 白名单](docs/decisions/ADR-012-admin-rbac-allowlist.md) · [ADR-013 审计副作用语义](docs/decisions/ADR-013-audit-side-effect-semantics.md)
3. **演进日志**：[`docs/process/`](docs/process/) — Step 001-023 每步一篇，含改动清单 / 决策 / 验证 / 下一步候选

## 协议

[MIT](LICENSE)
