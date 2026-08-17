# RiskPilot · 数据出境合规案件智能体

[![CI](https://github.com/Melodymll01/riskpilot-cross-border-data-agent/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Melodymll01/riskpilot-cross-border-data-agent/actions/workflows/ci.yml)
[![tests](https://img.shields.io/badge/tests-offline%20CI-brightgreen)](https://github.com/Melodymll01/riskpilot-cross-border-data-agent/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.12-blue)](pyproject.toml)
[![arch](https://img.shields.io/badge/arch-DDD%204--layer-9b5bff)](docs/architecture/overview.md)
[![agent](https://img.shields.io/badge/agent-LangChain%201.3%20%C2%B7%20LangGraph%201.2-ff7a59)](infra/agents/)
[![memory](https://img.shields.io/badge/memory-4--layer-00b3a4)](infra/memory/)

> 面向**数据出境合规**场景的证据驱动案件工作台，内置以《个人信息保护法》《数据安全法》《网络安全法》及安全评估 / 标准合同 / 个保认证三路径为代表的法规知识库。
>
> 系统直接使用 **LangChain 1.3 标准 Tool Calling Agent** 和 **LangGraph 1.2 状态图**：
> Copilot 负责会话式工具调用，Deep Research 与案件评估使用独立 Graph，
> 将案件材料、confirmed facts、版本化规则、不可变 Assessment 和 Reviewer 审批串成
> 可暂停、恢复、重试、取消和审计的闭环。工程上保留 DDD 4 层与 Port 边界，
> `/api/v2` 提供通用 Copilot，`/api/v3` 提供案件工作台；可选 LangSmith 只承接
> 隐私脱敏后的 AI Trace，不进入领域逻辑。

---

## 产品速览

| LangChain 工具调用过程 | 带溯源引用的回答 |
| :---: | :---: |
| ![Agent 推理过程](screenshots/02-回答推理.png) | ![带引用的回答](screenshots/03-回答引文.png) |

| 三模式工作台 | 知识库治理（多租户） |
| :---: | :---: |
| ![首页](screenshots/01-主页.png) | ![知识库](screenshots/04-知识库.png) |

| 审计日记（可观测性） | |
| :---: | :---: |
| ![审计日记](screenshots/05-审计日记.png) | |

> 一键体验：`docker compose up -d`，访问 <http://localhost:8001>（见[快速开始](#快速开始)）。

---

## 核心能力

| 能力 | 实现 | 代码 |
| --- | --- | --- |
| **LangChain Copilot** | `create_agent` + 原生 Tool Calling，owner 上下文由 `ToolRuntime` 注入，不暴露给模型 | [langchain_copilot.py](infra/agents/langchain_copilot.py) |
| **LangGraph Deep Research** | plan → retrieve → assess → retry/web → generate，最多三轮补查 | [langgraph_research.py](infra/research/langgraph_research.py) |
| **隐私保护 LangSmith** | 默认关闭；只上传哈希业务 ID、路径、计数和状态，隐藏输入/输出/异常/事件/附件 | [tracing.py](infra/observability/tracing.py) |
| **风险评估模型** | `RiskProfilePort` + HTTP Adapter，严格校验 evidence-state schema，支持独立部署模型 | [http_client.py](infra/risk_profile/http_client.py) |
| **混合文本检索** | Query Rewrite + Vector + BM25 + RRF + Cross-Encoder 重排 | [retriever.py](retrieval/search/retriever.py) |
| **Chinese-CLIP 图片召回** | Case 图片上传、图像向量、自然语言搜图、Workspace/Case 隔离 | [visual_evidence.py](app/use_cases/visual_evidence.py) |
| **可验证 AI 记忆** | 逐字 quote 接地写入；`hybrid_v1` 融合语义、置信度、显著性和新鲜度召回；支持安全过滤与召回解释 | [domain/memory.py](domain/memory.py) |
| **答案可溯源** | Agent/Research 输出来源标记；Evidence QA 使用独立 Claim-Citation 验证 | [evidence_qa.py](app/use_cases/evidence_qa.py) |
| **案件级证据** | Workspace/Case/Document 隔离，原件、版本、页码、Chunk、事实引用可追溯 | [domain/documents.py](domain/documents.py) |
| **确定性合规评估** | 只消费 confirmed facts 的版本化规则引擎，生成 Finding、ActionItem 和不可变 Assessment | [domain/policy_engine.py](domain/policy_engine.py) |
| **文档 Fact 提议** | 字段白名单、当前版本原文复核、冲突检测、Reviewer 确认后才进入规则计算 | [fact_management.py](app/use_cases/fact_management.py) |
| **Assessment 引用闭包** | Finding 关联 Fact / Evidence / Clause 快照，生成和批准前重验版本、SHA 与原文漂移 | [assessment_management.py](app/use_cases/assessment_management.py) |
| **可恢复案件工作流** | LangGraph + SQLite checkpointer，支持 interrupt/resume、失败重试、取消和人工审批 | [assessment_runs.py](app/use_cases/assessment_runs.py) |
| **V3 案件工作台** | Workspace/Case 创建与导航、材料上传/解析/索引/失败重试、Fact Confirmation 与继续运行 | [cases.js](frontend/cases.js) |
| **V3 Evidence QA** | 四类授权检索；结构/语义双校验；有限 Claim 过滤修复，全部失败仍安全拒答 | [evidence_qa.py](app/use_cases/evidence_qa.py) |

## 关键指标

| 维度 | 数值 |
| --- | --- |
| 离线回归 | **1219 passed · 1 skipped**；真实模型/CLIP 评测显式 `--live` |
| 架构规模 | **41 Port + 17 Use Case** · DDD 4 层 |
| V3 资源接口 | **45 个路由** · Workspace → Visual Evidence / Evidence QA / Assessment Run |
| Agent/Graph | LangChain Tool Calling + 2 张 LangGraph（Research / Assessment） |
| 记忆系统 | **4 层**（L1 最近消息 → L4 语义事实）+ `hybrid_v1` 可解释召回 |
| Top-K=2 检索命中率 | **93.3%**（chunk_size=300, overlap=60） |
| 图片评测 | **12 张合成图片** + 12 个文本查询，Recall@1/3 门禁 |

## 架构

依赖方向 `api → app → domain`，`infra` 反向实现 domain 端口；**domain 层不依赖任何框架**，保证可单元测试。详见 [docs/architecture/overview.md](docs/architecture/overview.md)。

```mermaid
flowchart TB
    API[api/v2 + api/v3 · 入口层<br/>QA / Case / Evidence / Assessment Run]
    APP[app · 用例编排层<br/>AppContainer + 17 Use Case]
    DOMAIN[domain · 纯模型 + 41 Port Protocol]
    INFRA[infra · 适配器<br/>LangChain / LangGraph / LangSmith / retrieval / memory / Chinese-CLIP]

    API --> APP --> DOMAIN
    INFRA -.实现.-> DOMAIN
    APP -.装配.-> INFRA
```

### 分层 AI 架构

- **V3 Evidence QA**：普通线性应用服务，不使用 LangGraph；先做授权范围检索，再生成
  原子 Claim，执行结构覆盖和独立语义支持校验，再以 `bounded_filter_v1` 只移除坏
  Claim；至少保留一条可信 Claim 时返回部分回答，否则安全拒答；
- **Copilot**：LangChain `create_agent` + 标准 Tool Calling，通过 `CopilotAgentPort`
  接入应用层，不再维护自定义 JSON 决策协议；
- **Deep Research**：LangGraph 显式节点，最多三轮补查，可按证据状态路由到 Web Search；
- **Case Assessment**：通过 `WorkflowRuntimePort` 使用 LangGraph，领域层不依赖框架；
- **AI 可观测性**：通过 `TracePort` 接入 LangSmith；默认 `NoopTraceAdapter`，启用时
  只记录哈希业务 ID、节点/工具路径、计数、状态和错误类型，案件正文、Prompt、模型
  回答、异常文本、事件和附件均在客户端出站前移除；
- **确定性边界**：LangGraph 负责流程状态，`PolicyRuleEngine` 负责门槛计算，
  `AssessmentManagementUseCase` 负责最终快照与审批；
- **检查点边界**：只保存对象 ID 和轻量状态，不保存文档正文、凭证、原始 prompt 或思维链。

### LangGraph Deep Research

```mermaid
flowchart LR
    Q[用户问题] --> P[Plan 查询规划]
    P --> R[Retrieve<br/>Vector + BM25 + RRF]
    R --> EC{Assess 证据充分性}
    EC -- partial --> R
    EC -- insufficient --> WS[Web Search]
    EC -- sufficient --> GEN[Generate 带来源报告]
    WS --> GEN
```

### 图片知识库召回

图片召回不是 OCR 文本检索：OCR 负责扫描文档文字恢复；Visual Evidence 使用
Chinese-CLIP 图文共享向量空间，用于机房照片、架构图、告警截图等非纯文本证据。

```text
图片 → Chinese-CLIP image embedding → Case-scoped visual index
查询 → Chinese-CLIP text embedding  → SQL scope filter → cosine Top-K
```

## 4 层记忆系统

让智能体在多轮、跨会话中保持连贯，同时把隐私控制权交还用户（PIPL「被遗忘权」）。

| 层 | 作用 | 默认 | 设计 |
| --- | --- | :---: | --- |
| **L1 最近消息** | 短期上下文 | 恒开 | 滑动窗口，超出转 L2 |
| **L2 滚动摘要** | 长对话压缩 | 恒开 | 触发式摘要 + TTL 过期 |
| **L3 用户画像** | 稳定偏好 | 按需 | 结构化字段，跨任务复用 |
| **L4 语义事实** | 可检索长期事实 | 按需 | 用户逐字原话 + message_id 接地，验证后去重合并 |

L4 采用抽取式记忆：模型只选择用户消息中的稳定 span、标签和显著性，服务端重新校验
`source_message_id + source_quote`，落库文本就是核验过的用户原话。助手回答、伪造 quote、
一次性请求、提示注入、密码/API Key、联系方式和高敏个人属性均不会进入长期记忆。

召回使用 `hybrid_v1`：先按 owner 隔离扩大向量候选池，再融合语义相关性、事实置信度、
显著性和新鲜度重排；低相关、过期和已被取代的事实不会注入。`POST
/api/v2/memory/recall/explain` 与前端召回解释器可展示综合分及各维度分数，但不返回向量、
Prompt 或其他用户数据。`evaluations/memory_recall` 以版本化数据集验证排序与安全过滤门禁。

**被遗忘权**：可逐条删除长期事实（`DELETE /api/v2/memory/facts/{id}`，owner 隔离 + 物理删除 + 审计留痕）或一键全量遗忘。

## 工程亮点

- **DDD 4 层架构** + 41 Port Protocol + Container 依赖注入，domain 不依赖 FastAPI、
  LangGraph 或具体数据库
- **标准 Agent 框架**：LangChain 负责模型和 Tool Calling；LangGraph 负责长程、有状态、
  可中断流程；领域层不依赖具体框架
- **可验证 Evidence QA**：LLM 只返回结构化 Claim；服务端重新读取当前文档版本原文，
  再用独立调用验证 Claim-Citation 语义支持；结果层只删除坏 Claim，不改写结论或
  补造引用，全部失败才拒答
- **可恢复人工闭环**：SQLite checkpointer + 产品 Run 乐观锁 + 连续事件 +
  Reviewer/Admin 审批
- **不可变引用快照**：Assessment 冻结 Fact Evidence、DocumentVersion、页码、quote、
  offset 和 SHA；Finding 的 Fact/Evidence/Clause 引用必须闭包，漂移即拒绝批准
- **混合检索**：向量 + BM25 + RRF 融合 + Cross-Encoder 重排
- **全链路审计**：admin 写操作全部落审计日志，可合规追责
- **可替换可观测性**：`TracePort` 隔离 LangSmith，默认无网络；启用后强制客户端脱敏，
  不把案件正文、记忆原文、Prompt、回答或异常栈上传第三方
- **CI 守护**：GitHub Actions 全量 Ruff + format + mypy + 零密钥 pytest；
  research 等外部能力全部注入 Fake，不访问网络、不下载模型、不产生费用

## 快速开始

### Docker（推荐）

```bash
git clone https://github.com/Melodymll01/riskpilot-cross-border-data-agent.git
cd riskpilot-cross-border-data-agent
cp .env.example .env            # Windows PowerShell 使用 copy
# 编辑 .env，填入 OPENAI_API_KEY
docker compose up -d
```

访问 <http://localhost:8001>。`.env` 不会打进镜像，可放心分享。

### 本地 Python

```bash
python -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env             # Windows PowerShell 使用 copy
uvicorn main:app --host 127.0.0.1 --port 8001 --reload
```

- 前端：<http://localhost:8001>　·　API 文档：<http://localhost:8001/docs>

零密钥质量验证（不会访问模型服务）：

```bash
pip install -r requirements-dev.txt
make ci
```

最小 `.env`（智谱 GLM 通道，与 `config.py` 默认对齐）：

```ini
OPENAI_API_KEY=<在 https://open.bigmodel.cn 申请>
OPENAI_API_BASE=https://open.bigmodel.cn/api/paas/v4
CHAT_MODEL=glm-4-flash
EMBEDDING_MODEL=embedding-3
# 可选：GitHub OAuth + admin
ADMIN_USER_IDS=github:your-github-login
```

> `.env` 已在 `.gitignore`，**严禁** `git add .env`。
>
> LangSmith 配置与隐私边界见 [docs/guides/langsmith-observability.md](docs/guides/langsmith-observability.md)；
> 不要设置 SDK 标准全局开关 `LANGSMITH_TRACING`，本项目只使用
> `RISK_PILOT_LANGSMITH_ENABLED` 经过隐私 Adapter 显式启用。

## 功能矩阵

| 能力 | 匿名 | 登录用户 | admin |
| --- | :---: | :---: | :---: |
| 对话问答 / 深度研究 / 风险画像 | ✅ | ✅ | ✅ |
| 任务历史持久化 | ✅ | ✅ | ✅ |
| 知识库查看 | ❌ | ✅ | ✅ |
| 知识库写入（上传 / 采集 / 删除） | ❌ | ❌ | ✅ |
| 审计日志 | ❌ | ❌ | ✅ |
| 长期记忆 / 被遗忘权 | ❌ | ✅ | ✅ |
| V3 案件材料 / 事实 / Assessment | 按 Workspace 成员关系 | ✅ | ✅ |
| 启动 / 继续 / 取消 Assessment Run | 按 Workspace 角色 | editor+ | ✅ |
| 确认关键事实 / 审批 Assessment | ❌ | reviewer | ✅ |

身份模型：匿名兜底 + GitHub OAuth（state 防 CSRF + JWT）+ admin 白名单（`ADMIN_USER_IDS`）。

## 主要 API（`/api/v2/*`）

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/copilot/chat/stream` | 登录 | **SSE 流式 + AgentEvent** |
| POST | `/copilot/chat` | 登录 | 同步聚合对话 |
| GET/PATCH/DELETE | `/tasks/*` | owner | 任务历史 CRUD |
| GET | `/documents` `/stats` `/{name}` | 登录 | KB 读取 |
| POST/DELETE | `/documents/file` `/web` `/{name}` | admin | KB 写入 / 删除 |
| GET | `/audit/logs` | admin | 审计日志查询 |
| GET | `/memory/facts` | owner | 长期事实清单 |
| POST | `/memory/recall/explain` | owner | 解释长期事实召回排序 |
| DELETE | `/memory/facts/{id}` | owner | **删除单条事实（被遗忘权）** |
| POST | `/memory/forget` | owner | 级联遗忘 |

> v1 HTTP 层已退役。`/api/v2` 继续提供问答、研究、知识库和记忆能力；`/api/v3`
> 提供案件工作台能力。完整端点见 <http://localhost:8001/docs>。

## 案件工作台 API（`/api/v3/*`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST/GET | `/workspaces` | Workspace 与成员权限 |
| POST/GET/PATCH | `/cases` | 合规案件和状态机 |
| POST/GET | `/cases/{case_id}/documents` | 案件材料、版本和处理任务 |
| GET | `/cases/{case_id}/evidence/search` | Case 范围混合检索 |
| POST/GET | `/cases/{case_id}/visual-assets` | Chinese-CLIP 图片上传与文本搜图 |
| POST/GET | `/cases/{case_id}/facts` | 事实候选、版本、证据和确认 |
| POST/GET | `/workspaces/{workspace_id}/policy-rules` | 版本化规则创建与发布 |
| POST/GET | `/cases/{case_id}/assessments` | 确定性 Assessment 生成与版本查询 |
| POST/GET | `/cases/{case_id}/assessment-runs` | LangGraph 案件评估运行 |
| POST | `/qa` | 四类授权语料范围的 Evidence QA |
| POST | `/runs/{run_id}/continue` | 重新检查材料/事实后恢复 |
| POST | `/runs/{run_id}/retry` | 重试 failed Run |
| POST | `/runs/{run_id}/cancel` | 幂等取消非终态 Run |
| POST | `/runs/{run_id}/review` | Reviewer/Admin 审批正式 Assessment |
| GET | `/runs/{run_id}/events` | 按 sequence 增量读取可审计事件 |

完整演示流程见：

- [V3 Evidence QA 指南](docs/guides/v3-evidence-qa.md)
- [V3 Case Assessment Run 指南](docs/guides/v3-assessment-run.md)

## 技术栈

- **后端**：FastAPI + Pydantic v2
- **架构**：DDD 4 层 + 41 Port + Container DI + WorkflowRuntimePort + TracePort
- **Agent**：LangChain 1.3 `create_agent` + OpenAI-compatible `ChatOpenAI`
- **工作流**：LangGraph 1.2 + SQLite checkpointer + interrupt/resume
- **可观测性**：可选 LangSmith + 客户端白名单/HMAC 脱敏；默认关闭
- **存储**：SQLite（业务对象 / Run / Event）+ 独立 LangGraph checkpoint SQLite + ChromaDB
- **鉴权**：GitHub OAuth + JWT（HS256）+ admin 白名单
- **LLM**：OpenAI 兼容接口（默认智谱 GLM，可换 Ollama / vLLM）
- **检索**：ChromaDB + jieba BM25 + RRF 融合 + bge-reranker-base 重排
- **多模态**：Chinese-CLIP + Pillow + Case-scoped SQLite visual index
- **记忆**：4 层分层记忆 + 逐字接地提取 + TTL + 语义去重 + 被遗忘权
- **前端**：原生 HTML + ES module（无构建依赖），含对话 / 案件 / 知识库 / 审计视图
- **质量**：离线 pytest + Ruff + GitHub Actions；版本化 Evidence/Memory/Visual 评测

## 文档

1. **2026 秋招生产化路线**：[docs/roadmap/autumn-recruitment-production-plan.md](docs/roadmap/autumn-recruitment-production-plan.md)
2. **阶段实施复盘**：[docs/implementation/](docs/implementation/)
3. **V2 完整设计**：[docs/design/riskpilot-v2.md](docs/design/riskpilot-v2.md)
4. **架构全景**：[docs/architecture/overview.md](docs/architecture/overview.md)
5. **架构决策**：[docs/decisions/](docs/decisions/)
6. **Assessment Run 演示**：[docs/guides/v3-assessment-run.md](docs/guides/v3-assessment-run.md)
7. **Evidence QA 演示**：[docs/guides/v3-evidence-qa.md](docs/guides/v3-evidence-qa.md)
8. **LangSmith 可观测性**：[docs/guides/langsmith-observability.md](docs/guides/langsmith-observability.md)

## 协议

[MIT](LICENSE)
