# RagDataOut v1 重构设计方案

> 目标：把当前可运行的原型重构为**工程化、可离线测试、可扩展**的对话式 Agent 应用，
> 作为秋招 Agent 应用岗简历主项目，可直接放上 GitHub。
>
> 状态：草案 v1.1 — 2026-06-04（产品形态 → 对话式合规 Copilot；身份方案 → GitHub OAuth + 匿名）
> 作者：项目重构设计阶段
> 关联：[schema-evidence-risk-profiling](D:/py/schema-evidence-risk-profiling) 风险画像微调模型项目

---

## 0. 文档导航

1. [项目定位与产品形态](#1-项目定位与产品形态)
2. [现状诊断：当前不工程化的根因](#2-现状诊断当前不工程化的根因)
3. [目标架构：4 层 + Agent 编排 + 身份层](#3-目标架构4-层--agent-编排--身份层)
4. [模块详细设计](#4-模块详细设计)
5. [记忆系统设计](#5-记忆系统设计)
6. [风险画像集成设计（作为 Tool）](#6-风险画像集成设计作为-tool)
7. [离线测试架构（Mock & Fixtures）](#7-离线测试架构mock--fixtures)
8. [API 设计](#8-api-设计)
9. [前端设计](#9-前端设计)
10. [配置与部署](#10-配置与部署)
11. [开发过程留痕（docs/process）](#11-开发过程留痕docsprocess)
12. [当前可优化点清单](#12-当前可优化点清单)
13. [重构实施计划（7 个 PR）](#13-重构实施计划7-个-pr)
14. [验收标准](#14-验收标准)
15. [开放问题与待决策项](#15-开放问题与待决策项)

---

## 1. 项目定位与产品形态

### 1.1 一句话定位

**数据出境合规 Copilot** —— 一个面向法务/合规从业者的对话式 Agent 应用：
用户用自然语言描述合规疑问或上传业务文档，Agent 自主调用法规检索、自研 evidence-state
微调模型、Web 监管口径搜索等工具，必要时主动追问关键信息，最终产出可执行的合规建议清单。

技术栈关键词：`Tool-Use Agent` `垂直领域 LoRA 微调模型` `Schema-guided 评估` `显式规则引擎`
`混合检索（向量+BM25+RRF+Rerank）` `离线可测` `GitHub OAuth + 匿名试用`。

### 1.2 产品形态：对话式 Agent（单一入口）

**前端不再是"三 Tab 切换"，而是一个对话界面**，让 Agent 自主决定调用什么工具。
三种历史"使用模式"（快速问答 / 深度研究 / 合规体检）转化为 Agent 的内部能力：

| 用户行为 | Agent 内部行为 | 调用的 Tool |
|---|---|---|
| 问简单法律事实 | 走"短链路径" | `search_law` → 直接生成 |
| 问复杂研究问题 | 多轮迭代 + Web 兜底 | `search_law` × N + `web_search` + `reflect` |
| 上传文档 + 询问合规性 | 调风险画像 + 法条检索 + 综合 | `risk_profile` + `search_law` + `generate_checklist` |
| 信息不足以回答 | **主动追问用户** | `ask_user`（Agent 标志性能力） |

### 1.3 典型交互（产品愿景）

```
用户：我们公司打算把客户数据存到 AWS 新加坡，需要做哪些合规准备？

Agent: [工具: search_law("数据出境 安全评估")]
       [反思] 已知三种合法路径，但需要了解贵公司具体情况

       为给出准确建议，请确认：
       1. 是否属于"关键信息基础设施运营者"？
       2. 年处理个人信息是否超过 100 万？
       3. 数据类型？（基础信息 / 敏感信息 / 儿童信息）
       4. 当前是否已有数据出境流程文档？可上传，我会做合规评估。

用户：非关基，年处理 50 万用户，含手机号/地址/订单。
      [附件 privacy.pdf]

Agent: [工具: risk_profile(privacy.pdf)]    ← 自研 evidence 模型
       [工具: search_law("标准合同 备案")]
       [反思] 适用标准合同路径；F4/F6 不充分

       【适用路径】PIPL 标准合同（无需安全评估）
       【文档评估】F4 接收方告知-不充分；F6 权利行使-不充分；其余合规
       【整改清单】1...2...3...4...5...
       【后续我可以】草拟 PIPIA 问卷 / 检查标准合同补充条款 ...
```

### 1.4 身份与数据隔离方案：GitHub OAuth + 匿名试用

#### 1.4.1 设计原则

记忆与历史是 Agent 应用的核心资产，**必须绑定持久身份**。但 Agent 应用岗的项目不应在
"用户系统"上耗费工程量。最优解：**OAuth + 匿名升级**（参考 Perplexity / Cursor / v0.dev）。

#### 1.4.2 双模身份模型

```
┌─────────── Anonymous（匿名试用，默认状态）────────────┐
│   触发：首次访问，未登录                                │
│   身份：anon_session_id (UUID, localStorage)         │
│   能用：所有 Agent 能力                                │
│   限制：- 仅本浏览器可用                                │
│         - 关闭/清缓存可能丢失                           │
│         - 配额上限：3 个 task / 5 个文档（可配）        │
│   引导：用户产生有价值数据时温和提示登录                 │
└──────────────────────────────────────────────────────┘
                          ↓ Sign in with GitHub
┌─────────── Authenticated（GitHub 登录）─────────────┐
│   触发：点击"Sign in with GitHub" + OAuth 回调        │
│   身份：user_id = "github:{login}"                  │
│   合并：匿名 session 数据自动迁移到该 user_id          │
│   持久：跨设备、跨浏览器、跨清缓存                     │
│   登录态：JWT (httpOnly cookie)，30 天                │
└──────────────────────────────────────────────────────┘
```

#### 1.4.3 统一身份键 owner_id

所有业务表都用 `owner_id` 作为隔离键，业务代码**不区分匿名/登录**：

```
匿名状态：owner_id = "anon:{session_id}"
登录状态：owner_id = "github:{login}"
```

登录时一条 SQL 把 `owner_id` 从 `anon:...` 改成 `github:...`，数据无缝迁移。

#### 1.4.4 双层知识库（重要）

```
全局知识库（共享、只读）         私有知识库（按 owner_id 隔离）
─────────────────────           ─────────────────────────────
PIPL 全文 / 实施条例 / 标准合同   用户上传的隐私政策 / 业务文档
管理员通过 CLI 种子导入           用户自己上传
collection: law_corpus           collection: user_docs (filter by owner_id)
所有用户都能检索                  仅 owner 可见
```

#### 1.4.5 OAuth Provider 插件式架构

```
infra/auth/providers/
├── base.py          # OAuthProvider Protocol
├── github.py        # ✅ v1 实现
├── google.py        # 🔮 接口位（TODO）
├── magic_link.py    # 🔮 接口位（TODO）
└── anonymous.py     # ✅ 匿名身份生成器
```

`config.auth_providers_enabled = ["github", "anonymous"]`，未来加 Google/邮箱仅需实现新 provider。

### 1.5 用户使用流程

#### 1.5.1 首次访问（零摩擦）

```
打开 http://localhost:8000
  ↓
前端检查 localStorage.anon_session_id
  ↓ 不存在 → 生成 UUID 存入
  ↓
POST /api/sessions/anonymous { session_id }
后端 upsert users 表（user_id="anon:{uuid}"）
  ↓
渲染主界面：
  顶栏右上：[Sign in with GitHub]   |  匿名身份小图标
  侧栏：本身份的任务列表（首次为空）
  主区：欢迎语 + 对话输入框
```

#### 1.5.2 升级登录（一键）

```
点击 [Sign in with GitHub]
  ↓
GET /api/auth/github/login?from=anon:{session_id}
后端：生成 state，跳转 GitHub OAuth
  ↓
GitHub 同意页 → 回调 /api/auth/github/callback?code&state
后端：换 token → 拿用户信息 → upsert users 表
  ↓ 关键步骤：数据迁移
UPDATE tasks         SET owner_id='github:{login}' WHERE owner_id='anon:{uuid}';
UPDATE user_documents SET owner_id='github:{login}' WHERE owner_id='anon:{uuid}';
UPDATE memory_*      SET owner_id='github:{login}' WHERE owner_id='anon:{uuid}';
  ↓
设置 JWT cookie，重定向回首页
  ↓
用户看到：之前的匿名任务 + 头像 + 跨设备能力
```

#### 1.5.3 几个高频场景

| 场景 | 体验 |
|---|---|
| 关闭浏览器再打开 | 匿名：localStorage 在则恢复；登录：JWT cookie 在则自动登录 |
| 清浏览器缓存 | 匿名：身份丢失（提示登录避免再次丢）；登录：被迫重新 OAuth |
| 换设备 | 匿名：完全独立身份；登录：所有数据可见 |
| 多人共用电脑 | 推荐用浏览器多用户配置 / 私密窗口；或彼此登录各自 GitHub |
| 演示给面试官 | 匿名直接体验，或一键 GitHub 登录展示完整能力 |

### 1.6 明确不在范围

- 邮箱+密码注册流程（OAuth 已覆盖核心场景）
- 多租户 SaaS / 计费 / 配额管理
- 公网公开服务（默认本机/内网部署）
- 实时高并发（默认面向单人交互）

---

## 2. 现状诊断：当前不工程化的根因

### 2.1 症状与根因

| 症状 | 根因 | 影响 |
|---|---|---|
| `service.KnowledgeService` 一个类管 7 件事 | 缺少 Use Case 层 | 业务逻辑难以独立测试和扩展 |
| `Embedder`、`ChatClient` 直接 `import + new` | 缺少 Protocol/接口层 | 测试无法替换实现 |
| `agentic_rag.py` 等模块直接 `from config import settings` | 配置全局耦合 | 难以参数化、难以并发安全 |
| 测试需要真实 API Key 才能跑 | 上面三点的必然结果 | CI 友好度差，开源门槛 |
| 加风险画像要改 `service.py`/`routes.py`/`schemas.py` 三个文件 | 没有插件式扩展点 | 每加一个能力都是侵入性修改 |
| `data/chat_db.py` 只是消息存储 | 没有"记忆"语义抽象 | 摘要/画像/语义记忆无处安放 |
| `risk/` 已建好但未接入 | use case + tool 抽象层缺失 | 模块成孤岛 |
| 没有身份/认证层 | 设计阶段未考虑 | 记忆与多用户隔离没法做 |
| Agent 工具调用不显式（散在 agentic_rag.py 流程里） | 没有 Tool Registry 抽象 | "Agent 项目"标签弱 |

### 2.2 重构原则

1. **不一次性大重构**：分 7 个 PR，每个 PR 可独立合并、CI 绿。
2. **不破坏现有功能**：现有路由、API 行为保持向后兼容（直到 PR-7 完全切换）。
3. **每个 PR 配一篇过程留痕**：`docs/process/` 同步更新。
4. **测试先行**：补完测试后再重构对应模块。
5. **匿名优先**：所有功能匿名状态可用，登录是增强而非门禁。

---

## 3. 目标架构：4 层 + Agent 编排 + 身份层

### 3.1 分层架构图

```
┌────────────────────────────────────────────────────────────────────┐
│  L4  api/         路由层（HTTP 边界、参数转换、异常映射、认证中间件）│
│       ├─ /api/copilot/chat    /api/auth/github/{login,callback}    │
│       ├─ /api/tasks   /api/documents   /api/memory                  │
│       └─ /api/ingest  /api/sources  /health                         │
├────────────────────────────────────────────────────────────────────┤
│  L3  app/         能力编排层                                        │
│       ├─ agent/                                                    │
│       │   ├─ copilot.py      ComplianceCopilotAgent (主 Agent)     │
│       │   ├─ tools.py        Tool Registry（声明式工具定义）        │
│       │   └─ planner.py      规划/反思（可选独立模块）              │
│       └─ use_cases/                                                │
│           ├─ run_copilot.py        Agent 主入口用例                 │
│           ├─ ingest.py                                             │
│           ├─ session_management.py 匿名/登录/迁移                   │
│           └─ task_management.py    任务列表/详情/删除               │
├────────────────────────────────────────────────────────────────────┤
│  L2  domain/      领域层（纯 dataclass + Protocol，零外部依赖）      │
│       ├─ models.py    User / Task / Message / Chunk / Citation /   │
│       │               RiskProfile / ToolCall / Artifact / ...      │
│       ├─ ports.py     ChatPort / EmbedPort / RetrievePort /        │
│       │               EvidencePort / MemoryPort / WebSearchPort /  │
│       │               AuthPort / UserRepoPort / TaskRepoPort       │
│       └─ errors.py    领域异常                                      │
├────────────────────────────────────────────────────────────────────┤
│  L1  infra/       基础设施层（具体实现，可替换）                     │
│       ├─ retrieval/   vector_store / bm25 / reranker / retriever   │
│       ├─ generation/  chat 实现（zhipu / openai / ollama）          │
│       ├─ embedding/   embedder 实现                                 │
│       ├─ evidence/    evidence 模型客户端（http / mock）            │
│       ├─ memory/    🆕 记忆四层实现（按 owner_id）                   │
│       ├─ auth/      🆕 OAuth + JWT + 匿名身份                        │
│       │   └─ providers/ {github, google?, magic_link?, anonymous}  │
│       ├─ ingestion/   文档加载器                                    │
│       ├─ processing/  切分、清洗、元信息                             │
│       ├─ storage/     chroma / sqlite / 文件                        │
│       └─ webio/       web_loader / web_searcher                    │
└────────────────────────────────────────────────────────────────────┘
```

### 3.2 依赖方向（强制）

```
L4 api      → L3 app
L3 app      → L2 domain   ✅
L3 app      ↛ L1 infra    ❌（只通过 Port）
L2 domain   ↛ 任何外部     ❌（必须纯 Python，零第三方）
L1 infra    → L2 domain   ✅（实现 Port）
```

### 3.3 Tool Registry：Agent 能力的声明式入口

```python
# app/agent/tools.py（伪代码）
@dataclass(frozen=True)
class ToolSpec:
    name: str                       # "search_law" / "risk_profile" / "web_search" / "ask_user"
    description: str                # 给 LLM 看的描述
    parameters_schema: dict         # JSON Schema
    handler: Callable               # 实际执行函数（注入了 infra 依赖）
    timeout_s: float = 30.0
    requires_owner: bool = True

# 注册（在 AppContainer 构造时填充）
TOOL_REGISTRY: dict[str, ToolSpec] = {}

def register_tool(spec: ToolSpec) -> None:
    TOOL_REGISTRY[spec.name] = spec
```

**ComplianceCopilotAgent** 通过这个 Registry 暴露给 LLM，新增工具 = 新建一个 `ToolSpec`。
这就是 Agent 项目最值得讲的"工程化"亮点之一。

---

## 4. 模块详细设计

### 4.1 domain/models.py（核心数据结构）

```python
# 身份
@dataclass
class User:
    user_id: str                    # "github:torvalds" / "anon:uuid"
    provider: Literal["github","google","magic_link","anonymous"]
    provider_id: str
    email: str | None
    display_name: str
    avatar_url: str | None
    created_at: float
    last_active_at: float

# 任务（替代 conversation 概念）
@dataclass
class Task:
    task_id: str
    owner_id: str                   # users.user_id
    title: str                      # Agent 自动生成
    state: Literal["planning","gathering","evaluating","answering","done"]
    user_goal: str                  # 第一句话
    collected_facts: dict           # 主动追问得到的事实
    created_at: float
    updated_at: float

@dataclass
class Message:
    msg_id: str
    task_id: str
    role: Literal["user","assistant","tool","system"]
    content: str
    tool_call_id: str | None = None # 关联的工具调用
    citations: list[Citation] = field(default_factory=list)
    created_at: float = ...

@dataclass
class ToolCall:
    tool_call_id: str
    task_id: str
    tool_name: str
    input_json: dict
    output_json: dict | None
    status: Literal["pending","success","failed","timeout"]
    duration_ms: int | None
    created_at: float

@dataclass
class Artifact:
    """Agent 中间产出（risk_profile / checklist / search_result 等）。"""
    artifact_id: str
    task_id: str
    artifact_type: str              # "risk_profile" / "checklist" / ...
    payload_json: dict
    created_at: float
```

### 4.2 domain/ports.py（核心抽象）

```python
# === 身份 ===
class AuthPort(Protocol):
    def begin_oauth(self, provider: str) -> tuple[str, str]: ...  # (auth_url, state)
    def complete_oauth(self, provider: str, code: str, state: str) -> User: ...
    def issue_jwt(self, user_id: str) -> str: ...
    def verify_jwt(self, token: str) -> str | None: ...           # → user_id
    def create_anonymous(self) -> User: ...

class UserRepoPort(Protocol):
    def upsert(self, user: User) -> None: ...
    def get(self, user_id: str) -> User | None: ...
    def merge_owner(self, from_id: str, to_id: str) -> int: ...   # 匿名→登录数据迁移
    def touch(self, user_id: str) -> None: ...                    # 更新 last_active_at

# === 任务/消息 ===
class TaskRepoPort(Protocol):
    def create(self, task: Task) -> None: ...
    def get(self, task_id: str, owner_id: str) -> Task | None: ...
    def list_for_owner(self, owner_id: str, limit: int = 50) -> list[Task]: ...
    def update(self, task: Task) -> None: ...
    def delete(self, task_id: str, owner_id: str) -> bool: ...
    def append_message(self, msg: Message) -> None: ...
    def list_messages(self, task_id: str) -> list[Message]: ...
    def append_tool_call(self, call: ToolCall) -> None: ...
    def append_artifact(self, art: Artifact) -> None: ...

# === LLM / 检索 / Evidence ===
class EmbedPort(Protocol): ...
class ChatPort(Protocol): ...
class RetrievePort(Protocol):
    def retrieve(self, query: str, top_k: int = 5,
                 corpus: Literal["law","user_docs"] = "law",
                 owner_id: str | None = None,
                 filters: dict | None = None) -> list[Chunk]: ...

class EvidencePort(Protocol): ...
class WebSearchPort(Protocol): ...

# === 记忆（按 owner_id）===
class MemoryPort(Protocol):
    # L1 短期：按 task_id
    def append_message(self, task_id: str, msg: Message) -> None: ...
    def recent_messages(self, task_id: str, n: int) -> list[Message]: ...
    # L2 摘要：按 task_id
    def get_summary(self, task_id: str) -> str | None: ...
    def maybe_summarize(self, task_id: str, threshold: int = 20) -> None: ...
    # L3 用户画像：按 owner_id（跨 task / 跨设备）
    def get_profile(self, owner_id: str) -> SessionProfile: ...
    def update_profile(self, owner_id: str, facts: dict) -> None: ...
    # L4 语义事实：按 owner_id
    def recall_semantic(self, owner_id: str, query: str, k: int) -> list[Fact]: ...
```

### 4.3 app/agent/copilot.py（主 Agent）

```python
class ComplianceCopilotAgent:
    """对话式合规 Agent：自主决策 → 调用工具 → 反思 → 输出。"""

    def __init__(
        self,
        chat: ChatPort,
        memory: MemoryPort,
        task_repo: TaskRepoPort,
        tool_registry: dict[str, ToolSpec],
        max_steps: int = 8,
    ):
        ...

    def run(self, owner_id: str, task_id: str,
            user_message: str) -> Iterator[AgentEvent]:
        """主循环。每一步产出一个 AgentEvent（思考 / 工具调用 / 文本 / 完成）。"""
        # 1) 加载任务上下文 + 记忆
        ctx = self._build_context(owner_id, task_id, user_message)

        # 2) ReAct 主循环
        for step in range(self.max_steps):
            decision = self._llm_decide(ctx)        # 思考下一步
            yield AgentEvent.thought(decision.thought)

            if decision.action == "ask_user":
                yield AgentEvent.ask(decision.question)
                return                              # 等用户下一轮

            if decision.action == "final_answer":
                yield AgentEvent.answer(decision.text, decision.citations)
                self._persist(owner_id, task_id, decision)
                return

            # 工具调用
            tool = self.tool_registry[decision.tool_name]
            try:
                result = tool.handler(**decision.tool_args, owner_id=owner_id)
                yield AgentEvent.tool_result(tool.name, result)
                ctx.observations.append((tool.name, result))
            except Exception as e:
                yield AgentEvent.tool_error(tool.name, str(e))
                ctx.observations.append((tool.name, {"error": str(e)}))

        yield AgentEvent.answer("已达到最大步数，给出当前最佳答复...", ctx.partial)
```

### 4.4 app/agent/tools.py（工具注册）

```python
def register_default_tools(container: AppContainer) -> dict[str, ToolSpec]:
    return {
        "search_law": ToolSpec(
            name="search_law",
            description="在法规知识库中检索条款，返回相关条文片段",
            parameters_schema={"type":"object","properties":{
                "query":{"type":"string"},"top_k":{"type":"integer","default":5}
            },"required":["query"]},
            handler=lambda query, top_k=5, owner_id=None: container.retriever.retrieve(
                query, top_k=top_k, corpus="law"),
        ),
        "search_user_docs": ToolSpec(
            name="search_user_docs",
            description="在用户上传的文档中检索内容",
            parameters_schema={...},
            handler=lambda query, owner_id, top_k=5: container.retriever.retrieve(
                query, top_k=top_k, corpus="user_docs", owner_id=owner_id),
        ),
        "risk_profile": ToolSpec(
            name="risk_profile",
            description="对用户文档执行 schema-guided 合规风险画像",
            parameters_schema={"type":"object","properties":{
                "doc_id":{"type":"string"}
            },"required":["doc_id"]},
            handler=lambda doc_id, owner_id: container.risk_profiler.profile(
                document=container.docs.load(doc_id, owner_id),
                document_id=doc_id),
        ),
        "web_search": ToolSpec(
            name="web_search",
            description="联网搜索最新监管口径或公开判例",
            parameters_schema={...},
            handler=lambda query, top_k=5, owner_id=None: container.web_search.search(
                query, top_k=top_k),
        ),
        "ask_user": ToolSpec(
            name="ask_user",
            description="信息不足时主动追问用户。优先于猜测",
            parameters_schema={"type":"object","properties":{
                "question":{"type":"string"},
                "missing_facts":{"type":"array","items":{"type":"string"}}
            },"required":["question"]},
            handler=None,  # 由 Agent 主循环特殊处理
        ),
        "generate_checklist": ToolSpec(
            name="generate_checklist",
            description="根据收集到的信息生成结构化整改清单",
            parameters_schema={...},
            handler=lambda facts, evidence, owner_id: container.checklist_generator.run(
                facts, evidence),
        ),
    }
```

### 4.5 app/use_cases/run_copilot.py（薄入口）

```python
class RunCopilotUseCase:
    """API 入口：接收用户消息 → 跑 Agent → 流式产出。"""

    def __init__(self, agent: ComplianceCopilotAgent, task_repo: TaskRepoPort):
        self.agent = agent
        self.task_repo = task_repo

    def stream(self, owner_id: str, task_id: str | None,
               user_message: str, attachment_doc_ids: list[str]) -> Iterator[AgentEvent]:
        # 没有 task_id 则创建新 task
        if task_id is None:
            task = Task(task_id=new_id(), owner_id=owner_id,
                        title="", state="planning",
                        user_goal=user_message, collected_facts={},
                        created_at=now(), updated_at=now())
            self.task_repo.create(task)
            task_id = task.task_id
            yield AgentEvent.task_created(task_id)

        # 把附件信息塞进 user_message（让 Agent 知道有可用文档）
        if attachment_doc_ids:
            user_message += f"\n\n[已上传文档 ID: {attachment_doc_ids}]"

        yield from self.agent.run(owner_id, task_id, user_message)
```

### 4.6 app/container.py（DI 容器）

```python
class AppContainer:
    """应用级别的依赖装配。一次构造，全局复用。"""
    def __init__(self, settings: Settings):
        self.settings = settings
        # infra 单例
        self.embedder      = build_embedder(settings)
        self.chat          = build_chat(settings)
        self.vector_store  = build_vector_store(settings)
        self.retriever     = build_retriever(self.embedder, self.vector_store, settings)
        self.evidence      = build_evidence_client(settings)
        self.web_search    = build_web_search(settings)
        self.user_repo     = build_user_repo(settings)
        self.task_repo     = build_task_repo(settings)
        self.memory        = build_memory(settings, self.chat, self.embedder)
        self.auth          = build_auth(settings, self.user_repo)
        # risk
        self.risk_profiler = RiskProfiler(self.retriever, self.evidence,
                                          legal_top_k=settings.risk_legal_top_k)
        # agent
        self.tool_registry = register_default_tools(self)
        self.copilot_agent = ComplianceCopilotAgent(
            self.chat, self.memory, self.task_repo, self.tool_registry,
            max_steps=settings.agent_max_steps)
        # use case
        self.run_copilot   = RunCopilotUseCase(self.copilot_agent, self.task_repo)
        self.ingest        = IngestionUseCase(...)
```

### 4.7 config/factories.py

```python
def build_chat(s: Settings) -> ChatPort:
    if s.llm_provider == "local":
        return OllamaChatAdapter(s.ollama_api_base, s.local_chat_model)
    return ZhipuOpenAIChatAdapter(s.openai_api_base, s.openai_api_key, s.chat_model)

def build_evidence_client(s: Settings) -> EvidencePort:
    if s.risk_evidence_provider == "mock":
        return MockEvidenceClient()
    return HTTPEvidenceClient(s.risk_evidence_base_url, timeout=s.risk_evidence_timeout)

def build_auth(s: Settings, user_repo: UserRepoPort) -> AuthPort:
    providers = {}
    if "github" in s.auth_providers_enabled:
        providers["github"] = GitHubOAuthProvider(s.github_client_id,
                                                  s.github_client_secret,
                                                  s.github_redirect_uri)
    if "anonymous" in s.auth_providers_enabled:
        providers["anonymous"] = AnonymousProvider()
    return AuthService(providers=providers, jwt_secret=s.jwt_secret,
                       jwt_ttl_days=s.jwt_ttl_days, user_repo=user_repo)
```

**核心**：所有"按配置选择实现"的逻辑集中在工厂。infra 内部不读 settings。

---

## 5. 记忆系统设计

> **关键变化（v1.1）**：所有记忆按 `owner_id` 绑定，匿名 → 登录时通过 `UserRepoPort.merge_owner` 一并迁移。

### 5.1 四层记忆模型（按 owner_id 隔离）

```
┌─ L1 短期记忆 [当前任务消息原文] ────────────────────────
│   存储: sqlite messages 表（task_id 关联到 owner_id）
│   作用域: task_id（一次连续对话/会话）
│   读取: 最近 N 条原文 → 注入 Agent prompt
│   写入: Agent 每一步产出 message / tool_call / artifact 时追加
│
├─ L2 摘要记忆 [任务级压缩] ─────────────────────────────
│   存储: 新增 task_summaries 表
│         (task_id, summary, last_summarized_msg_idx, updated_at)
│   作用域: task_id
│   触发: 消息+工具调用数 > 20 时，前 15 条 LLM 摘要为 200 字
│   读取: 系统 prompt 注入「任务历史摘要」+ L1 最近 5 条
│
├─ L3 用户画像记忆 [跨任务、跨设备] ─────────────────────
│   存储: 新增 user_profiles 表 (owner_id, facts JSON, updated_at)
│   作用域: owner_id（跨所有 task；登录用户跨设备）
│   触发: 任务结束时 / 显式追问后异步抽取
│   读取: 新任务进入时注入「你之前关注过 X，从事 Y 工作」
│   字段示例: {"role":"合规专员","industry":"金融",
│             "recurrent_topics":["跨境","个保"],"tone":"正式"}
│
└─ L4 语义长期记忆 [事实回忆] ───────────────────────────
    存储: Chroma collection `memory_facts`，metadata 含 owner_id
    作用域: owner_id
    触发: 对话中识别"事实陈述"时入库
    读取: 新问题向量召回 top-3 相关历史事实（强制按 owner_id 过滤）
    备注: 优先级最低，PR-7 加分项
```

### 5.2 owner_id 在数据层的强制隔离

所有用户数据表都以 `owner_id` 为第一索引：

```sql
-- 任务
CREATE INDEX idx_tasks_owner ON tasks(owner_id, updated_at DESC);
-- 用户文档
CREATE INDEX idx_user_docs_owner ON user_documents(owner_id);
-- 摘要 / 画像
CREATE INDEX idx_task_sum_owner ON task_summaries(task_id);  -- 间接
CREATE UNIQUE INDEX uq_user_profile ON user_profiles(owner_id);
```

所有 RetrievePort 调用 `corpus="user_docs"` 时**必传** `owner_id`，infra 层强制将其写入 Chroma `where` 过滤；忘传则抛 `MissingOwnerError`。

### 5.3 匿名 → 登录的迁移逻辑

```python
# infra/auth/auth_service.py（伪代码）
def complete_oauth(self, provider, code, state) -> User:
    user = self._oauth_handshake(provider, code, state)
    self.user_repo.upsert(user)
    anon_id = self._get_anon_from_state(state)   # cookie or state param
    if anon_id and anon_id != user.user_id:
        moved = self.user_repo.merge_owner(from_id=anon_id, to_id=user.user_id)
        log.info("merged %d rows from %s to %s", moved, anon_id, user.user_id)
    return user

# infra/storage/sqlite_user_repo.py
def merge_owner(self, from_id, to_id) -> int:
    total = 0
    for table in ("tasks", "user_documents", "user_profiles", "messages_meta"):
        cur = self.conn.execute(
            f"UPDATE {table} SET owner_id=? WHERE owner_id=?", (to_id, from_id))
        total += cur.rowcount
    # Chroma：批量更新 metadata
    self.vector_store.update_owner(from_id, to_id)
    self.conn.commit()
    return total
```

### 5.4 实施顺序

- **PR-6 阶段做 L1 + L2**（绑 owner_id）：90% 价值
- **L3** PR-6 留接口位 + 简单 LLM 抽取实现
- **L4** PR-7 选做

---

## 6. 风险画像集成设计（作为 Tool）

> **关键变化（v1.1）**：风险画像不再是独立 Tab，而是 ComplianceCopilotAgent 的一个**工具**。Agent 在判断用户上传了文档且意图含"评估/合规体检"时主动调用。

### 6.1 整体集成图

```
┌────────────────────── RagDataOut ──────────────────────┐
│                                                         │
│  api/routes/copilot.py   POST /api/copilot/chat[/stream]│
│         ↓                                               │
│  app/use_cases/run_copilot.py                           │
│         ↓                                               │
│  app/agent/copilot.py   ComplianceCopilotAgent          │
│         ↓ tool_registry["risk_profile"]                │
│  risk/profiler.py        编排 6 个 factor               │
│   ├─ retriever.retrieve(legal_basis_query)  ← 法条     │
│   ├─ evidence.judge(document, factor.target) ← 证据    │
│   └─ rule_engine.derive_risk_item(...)       ← 决策    │
│         ↓                                               │
│  artifact 入库 + Agent 用结果继续推理（如生成清单）      │
│                                                         │
└──────────────┬──────────────────────────────────────────┘
               │ HTTP /v1/evidence/judge（mock 时跳过）
               ▼
┌────── schema-evidence-risk-profiling (独立服务) ────────┐
│   FastAPI + vLLM (LoRA)                                 │
│   Qwen2.5-7B-Instruct + evidence_v1 LoRA Adapter        │
│   端口: 8001  |  GPU: 必需                              │
└─────────────────────────────────────────────────────────┘
```

### 6.2 部署形态

| 部署模式 | 描述 | 适用场景 |
|---|---|---|
| **完全离线** | RagDataOut 单独跑，evidence 走 mock | 演示、测试、CI |
| **半在线** | RagDataOut + 远端 evidence 服务 | 开发联调 |
| **全本地** | RagDataOut + 本机 vLLM evidence 服务 | 生产部署 |

### 6.3 evidence 服务接口契约（已与你的项目对齐）

```http
POST /v1/evidence/judge
Content-Type: application/json

{
  "document": "...",
  "target": "The document discloses ...",
  "allowed_labels": ["supported","contradicted","not_disclosed",
                     "insufficiently_disclosed","irrelevant"]
}

Response 200:
{
  "evidence_state": "not_disclosed",
  "evidence_spans": [{"text": "...", "start": 12, "end": 48}],
  "confidence": 0.87,
  "raw_generation": "..."
}
```

模型未上线期间，`MockEvidenceClient` 提供基于关键词的确定性 fake，**前后端可以独立联调**。

### 6.4 Factor 集合（v1 数据出境）

已在 `risk/factors.py` 定义 6 个：F1 跨境识别（gating） / F2 单独同意 / F3 影响评估 / F4 接收方告知 / F5 安全措施 / F6 权利行使。

未来扩展位：F7 标准合同 / F8 安全评估 / F9 数据本地化 / F10 跨境清单。

### 6.5 规则引擎与 Schema 对齐

`risk/rule_engine.py` 的标签空间严格对齐 `schemas/evidence_v1/evidence_state_schema_v1.json`：
- 训练标签：5 类（supported / contradicted / not_disclosed / insufficiently_disclosed / irrelevant）
- 规则专属：1 类（not_applicable，由 gating 触发）

**LLM 决定证据状态，规则决定风险等级**——可解释性的关键设计。

---

## 7. 离线测试架构

### 7.1 测试金字塔

```
        ┌────── e2e (5%) ──────┐
        │ FastAPI TestClient   │  全 fake，验证 HTTP 契约 + Auth 流
        ├──── integration ────┤
        │     (25%)           │  use case + 真实 sqlite/chroma + fake LLM/OAuth
        ├────── unit ─────────┤
        │      (70%)          │  纯函数：splitter/cleaner/rule_engine/fusion
        └─────────────────────┘
```

### 7.2 Fake 边界纪律

| ✅ Mock | ❌ 不 Mock |
|---|---|
| OpenAI / 智谱 API | 自己写的 BM25Index |
| HF Reranker 模型加载 | 自己写的 Splitter |
| Evidence HTTP 服务 | 自己写的 Retriever 编排逻辑 |
| Web 搜索 API | 自己写的 RuleEngine |
| GitHub OAuth（authorize / token / user API） | 自己写的 AuthService 业务逻辑 |
| `requests` 网页抓取 | 自己写的 Cleaner |

**只 Mock 跨进程边界**。否则测试会变成"测 mock 在工作"。

### 7.3 目录结构

```
tests/
├── conftest.py                # 全局 fixture：app / container / fake_* / tmp_db
├── fakes/                     # 实现 Port 的假对象
│   ├── fake_chat.py           # 录放式 ChatClient（支持 scenario library）
│   ├── fake_embedder.py       # 哈希式确定性向量
│   ├── fake_reranker.py       # 按 distance 排序
│   ├── fake_evidence.py       # 复用 risk.MockEvidenceClient
│   ├── fake_web_search.py     # 静态 fixture
│   ├── fake_memory.py         # 内存 dict 实现
│   ├── fake_oauth.py          # 🆕 跳过真实 GitHub，回调直接合成 user
│   └── fake_user_repo.py      # 内存 dict 实现 UserRepoPort/TaskRepoPort
├── fixtures/                  # 离线数据
│   ├── chat_scenarios/        # 🆕 按场景命名的对话剧本
│   │   ├── tool_route_search_law.json
│   │   ├── tool_route_risk_profile.json
│   │   ├── ask_user_clarify.json
│   │   ├── final_answer_with_citations.json
│   │   └── multi_step_research.json
│   ├── documents/             # 测试用法律小样本 + 隐私政策
│   ├── retrieval/             # 预录的检索结果
│   ├── risk_profiles/         # 黄金答案
│   ├── oauth/                 # 🆕 GitHub OAuth 响应样本
│   │   ├── github_token_ok.json
│   │   ├── github_user_ok.json
│   │   └── github_token_error.json
│   └── embeddings/            # 固定向量（hash 生成）
├── unit/                      # 纯函数测试
├── integration/               # 用例 + 真实存储 + fake 模型
├── e2e/                       # FastAPI TestClient
└── data/                      # 临时存储目录
```

### 7.4 Fake 清单（每个都说明用途与数据来源）

| Fake | 实现 | 数据源 | 用于断言 |
|---|---|---|---|
| `FakeEmbedder` | 哈希 → 固定向量 | 即时计算 | 召回流水线连通性 |
| `FakeChatClient` | 按 scenario 选 fixture，可链式 | `chat_scenarios/*.json` | LLM 调用次数 / 入参 prompt 段 / 返回内容 |
| `FakeReranker` | 等价排序（保留输入顺序） | — | rerank 流程不破坏链路 |
| `FakeEvidence` | 关键词正则 | `risk.MockEvidenceClient._MOCK_RULES` | factor 状态确定可重放 |
| `FakeWebSearch` | 返回静态 list | `fixtures/web_results.json` | Agent 决策路径 |
| `FakeOAuth` | 跳过 HTTP，直接 issue user | `fixtures/oauth/*.json`（场景化） | OAuth 业务回调逻辑 |
| `FakeUserRepo` / `FakeTaskRepo` | dict | — | 业务流程，单测速度优先 |
| `FakeMemory` | dict + list | — | 与 use case 解耦的记忆调用 |

### 7.5 关键 Fake 实现示意

**FakeChatClient（场景化录放）**

```python
class FakeChatClient:
    """按 scenario 加载对话剧本；多轮调用按顺序返回每一轮的预录响应。"""
    def __init__(self, scenario: str = "default"):
        path = FIXTURE_DIR / "chat_scenarios" / f"{scenario}.json"
        self.script = json.loads(path.read_text(encoding="utf-8"))["turns"]
        self.calls: list[dict] = []
        self._idx = 0
    def chat(self, messages, **kw):
        self.calls.append({"messages": messages, "kwargs": kw})
        if self._idx >= len(self.script):
            raise AssertionError(f"FakeChat 超出剧本长度 ({self._idx})")
        turn = self.script[self._idx]
        self._idx += 1
        return ChatResult(text=turn["text"], tool_calls=turn.get("tool_calls", []))
```

场景文件示例（`tool_route_risk_profile.json`）：
```json
{
  "description": "用户上传隐私政策 → Agent 决定调 risk_profile → 用结果生成清单",
  "turns": [
    {"text":"思考：用户提供了文档且要求评估，调用 risk_profile",
     "tool_calls":[{"name":"risk_profile","args":{"doc_id":"DOC-1"}}]},
    {"text":"思考：拿到风险报告，生成整改清单",
     "tool_calls":[{"name":"generate_checklist","args":{...}}]},
    {"text":"最终回复：根据评估，您的政策在 F2/F3 项存在...",
     "tool_calls":[]}
  ]
}
```

**FakeOAuth（绕过 GitHub）**

```python
class FakeGitHubOAuth:
    def __init__(self, persona: str = "alice"):
        data = json.loads((FIXTURE_DIR / "oauth" / f"github_user_{persona}.json").read_text())
        self.persona_user = data
    def begin(self) -> tuple[str, str]:
        state = secrets.token_urlsafe(16)
        return f"http://fake/authorize?state={state}", state
    def exchange(self, code: str, state: str) -> User:
        return User(user_id=f"github:{self.persona_user['login']}",
                    provider="github", provider_id=str(self.persona_user["id"]),
                    email=self.persona_user.get("email"),
                    display_name=self.persona_user["name"],
                    avatar_url=self.persona_user["avatar_url"],
                    created_at=now(), last_active_at=now())
```

生产路径用 `responses` 库拦截真实 HTTP（在 integration 测试中验证 `GitHubOAuthProvider` 的 HTTP 行为）：

```python
@responses.activate
def test_github_oauth_token_exchange():
    responses.post("https://github.com/login/oauth/access_token",
                   json=load_fixture("oauth/github_token_ok.json"))
    responses.get("https://api.github.com/user",
                  json=load_fixture("oauth/github_user_alice.json"))
    provider = GitHubOAuthProvider(...)
    user = provider.exchange(code="abc", state="xyz")
    assert user.user_id == "github:alice"
```

### 7.6 关键 Fixture 列表

| Fixture | 用途 |
|---|---|
| `pipl_excerpt.txt` | 法规检索测试小样本（200 行） |
| `privacy_policy_good.txt` | 风险画像-多数 supported |
| `privacy_policy_bad.txt` | 风险画像-多数 not_disclosed |
| `cross_border_doc.txt` | 触发 F1 gating 路径 |
| `chat_scenarios/*.json` | 8 个剧本覆盖路由/澄清/拒答/多步 |
| `oauth/github_user_{alice,bob}.json` | 多用户隔离测试 |
| `oauth/github_token_{ok,error}.json` | 成功/失败路径 |
| `golden_risk_profile_pipl.json` | 风险报告快照断言 |

### 7.7 conftest.py 全局开关

```python
import os, pytest
os.environ.setdefault("OPENAI_API_KEY", "sk-test-fake")
os.environ.setdefault("LLM_PROVIDER", "api")
os.environ.setdefault("RISK_EVIDENCE_PROVIDER", "mock")
os.environ.setdefault("ENABLE_RERANKER", "false")
os.environ.setdefault("AUTH_PROVIDERS_ENABLED", "github,anonymous")
os.environ.setdefault("GITHUB_CLIENT_ID", "fake_id")
os.environ.setdefault("GITHUB_CLIENT_SECRET", "fake_secret")
os.environ.setdefault("JWT_SECRET", "test_secret_change_me")

@pytest.fixture
def container_with_fakes(tmp_path):
    """构造一个所有外部依赖都用 Fake 的 AppContainer。"""
    s = Settings(...)
    c = AppContainer.__new__(AppContainer)
    c.settings = s
    c.embedder = FakeEmbedder()
    c.chat = FakeChatClient(scenario="default")
    c.evidence = FakeEvidence()
    c.web_search = FakeWebSearch()
    c.user_repo = FakeUserRepo()
    c.task_repo = FakeTaskRepo()
    c.memory = FakeMemory()
    c.auth = AuthService(providers={"github": FakeGitHubOAuth(), "anonymous": AnonymousProvider()},
                         jwt_secret=s.jwt_secret, jwt_ttl_days=30, user_repo=c.user_repo)
    c.vector_store = build_vector_store(s, path=tmp_path/"chroma")
    c.retriever = build_retriever(c.embedder, c.vector_store, s)
    c.risk_profiler = RiskProfiler(c.retriever, c.evidence, legal_top_k=3)
    c.tool_registry = register_default_tools(c)
    c.copilot_agent = ComplianceCopilotAgent(c.chat, c.memory, c.task_repo,
                                              c.tool_registry, max_steps=8)
    c.run_copilot = RunCopilotUseCase(c.copilot_agent, c.task_repo)
    return c

@pytest.fixture
def test_client(container_with_fakes):
    from main import build_app
    app = build_app(container=container_with_fakes)
    return TestClient(app)

@pytest.fixture
def anon_client(test_client):
    """已建立匿名身份的客户端。"""
    r = test_client.post("/api/auth/anonymous")
    test_client.cookies.set("copilot_session", r.json()["jwt"])
    return test_client

@pytest.fixture
def logged_in_client(test_client):
    """通过 FakeOAuth 登录 alice。"""
    r = test_client.get("/api/auth/github/login", follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    test_client.get(f"/api/auth/github/callback?code=fake&state={state}")
    return test_client
```

### 7.8 README 承诺

> 所有测试用例完全离线运行，不依赖任何外部 API（GitHub OAuth 也不需要）：
>
> ```
> pip install -r requirements-dev.txt
> pytest -q
> ```
>
> CI 中无需配置任何 secrets。

---

## 8. API 设计

> **关键变化（v1.1）**：聊天为中心；身份由 cookie 中的 JWT 推断，所有业务请求都注入 `owner_id`。

### 8.1 端点清单

```
[认证]
POST   /api/auth/anonymous              发匿名 user + JWT cookie（首次访问）
GET    /api/auth/github/login           跳转 GitHub authorize（携带 anon_id state）
GET    /api/auth/github/callback        换 token / 拿用户 / 迁移匿名数据 / 发 JWT
GET    /api/auth/me                     当前身份（匿名/登录均返回）
POST   /api/auth/logout                 清 JWT cookie（可选回到匿名）

[Copilot 主入口]
POST   /api/copilot/chat                Agent 一轮回复（同步）
POST   /api/copilot/chat/stream         Agent 流式：思考 / 工具 / 文本 / 完成

[任务（替代 conversations）]
GET    /api/tasks                       owner 的任务列表
GET    /api/tasks/{task_id}             详情：消息 + tool_calls + artifacts
PATCH  /api/tasks/{task_id}             改标题
DELETE /api/tasks/{task_id}             删任务（连带产出物）

[文档（owner 隔离）]
POST   /api/documents                   上传业务文档（risk_profile 用）
GET    /api/documents                   owner 上传过的文档列表
DELETE /api/documents/{doc_id}

[法规知识源（共享 law_corpus，仅管理员）]
POST   /api/sources/ingest              上传法规
POST   /api/sources/ingest/web          抓取法规网页
GET    /api/sources                     法规源列表
DELETE /api/sources/{name}

[底层能力（调试/Agent 内部 — 默认隐藏）]
POST   /api/_debug/retrieve             纯检索
POST   /api/_debug/risk/judge           单 factor 判断
GET    /api/_debug/tools                列出已注册 tools

[记忆]
GET    /api/memory/profile              当前 owner 的 L3 画像
DELETE /api/memory/profile              清除画像

[健康检查]
GET    /health
GET    /health/ready                    含 chroma / evidence service 探活
```

### 8.2 认证中间件

```python
# api/middleware/auth.py
async def auth_middleware(request: Request, call_next):
    token = request.cookies.get("copilot_session")
    user_id = container.auth.verify_jwt(token) if token else None
    if user_id is None:
        # 未携带 / 失效 → 自动签发匿名身份
        user = container.auth.create_anonymous()
        container.user_repo.upsert(user)
        new_token = container.auth.issue_jwt(user.user_id)
        request.state.owner_id = user.user_id
        response = await call_next(request)
        response.set_cookie("copilot_session", new_token, httponly=True,
                            samesite="lax", max_age=60*60*24*30)
        return response
    request.state.owner_id = user_id
    container.user_repo.touch(user_id)
    return await call_next(request)
```

FastAPI 依赖：
```python
def require_owner(request: Request) -> str:
    return request.state.owner_id
```

### 8.3 主要请求/响应

```python
class ChatRequest(BaseModel):
    task_id: str | None = None              # 不传则新建 Task
    message: str
    attachment_doc_ids: list[str] = []

class ChatResponse(BaseModel):
    task_id: str
    events: list[AgentEvent]                 # 同步模式聚合返回
```

流式 SSE：
```
event: task_created      data: {"task_id":"..."}
event: thought           data: {"text":"...","step":1}
event: tool_call         data: {"tool":"search_law","args":{...},"call_id":"..."}
event: tool_result       data: {"call_id":"...","result":{...}}
event: ask_user          data: {"question":"...","missing":["data_volume"]}
event: token             data: {"text":"根据..."}
event: citation          data: {"index":1,"source":"PIPL.txt","snippet":"..."}
event: artifact          data: {"type":"risk_profile","id":"..."}
event: done              data: {"total_tokens":523}
event: error             data: {"code":"...","message":"..."}
```

### 8.4 错误响应统一

```python
class ErrorResponse(BaseModel):
    success: bool = False
    error_code: str
    message: str
    details: dict | None = None
    request_id: str
```

---

## 9. 前端设计

### 9.1 布局：单对话框 + Agent 思考可见 + 产出物面板

```
┌─────────────────────────────────────────────────────────────────┐
│ Top: RagDataOut Copilot         匿名 ⌖ / [Sign in with GitHub] │
├──────────────┬────────────────────────────────┬───────────────────┤
│ 任务列表      │ 主对话区                       │ 产出物面板         │
│ + 新对话      │                                │ (artifacts)       │
│ ─ 任务 A      │ user > 我们的政策合规吗？       │                   │
│ ─ 任务 B      │   📎 privacy.pdf               │ • 风险报告 v1     │
│ ─ ...         │                                │   F1 ⚠ F2 ✓ F3 ⚠│
│               │ Agent ▸ 思考: 用户上传了文档..  │ • 整改清单 v1     │
│               │ Agent ▸ 调用 risk_profile      │ • 检索快照        │
│               │   ↳ 6 项 factor 评估完成        │                   │
│               │ Agent ▸ 调用 generate_checklist │                   │
│               │ Agent ▸ "根据评估，您需要..."   │                   │
│               │                                │                   │
│               │ [输入框] [📎附件] [发送]        │                   │
└──────────────┴────────────────────────────────┴───────────────────┘
```

关键点：
- **思考可见**：每条 `event: thought` 渲染为浅灰 italic 行，可折叠
- **工具调用可见**：`tool_call` + `tool_result` 渲染为可展开卡片，显示参数和返回
- **澄清友好**：`ask_user` 渲染为高亮气泡，输入框 placeholder 提示
- **产出物聚合**：所有 `artifact` 事件汇总到右侧面板，可点击查看详情/导出
- **匿名提示**：顶栏右上提示"匿名模式，登录可跨设备同步"，登录后变为头像

### 9.2 实现选择

- 保持 vanilla JS（已有 `frontend/`），新增模块：
  - `auth.js`：识别匿名/登录，调 `/api/auth/*`
  - `chat.js`：建立 SSE 连接，渲染 events
  - `artifacts.js`：右侧面板
  - `tasks.js`：左侧任务列表
- 中期可迁 Vue3 / React，不在 v1 范围

---

## 10. 配置与部署

### 10.1 配置层级

```
1. 默认值        config/settings.py 内 Settings 类
2. 环境变量      .env 文件（pydantic-settings 自动加载）
3. 启动参数      main.py CLI args（可选）
```

### 10.2 关键配置项（新增）

```bash
# 风险画像
RISK_EVIDENCE_PROVIDER=mock           # mock | http
RISK_EVIDENCE_BASE_URL=http://localhost:8001
RISK_EVIDENCE_TIMEOUT=60.0
RISK_LEGAL_TOP_K=3

# Agent
AGENT_MAX_STEPS=8                     # ReAct 最大步数
AGENT_PLANNING_ENABLE=false           # 是否启用独立 planner

# 记忆系统
MEMORY_SUMMARY_THRESHOLD=20
MEMORY_SUMMARY_KEEP=5
MEMORY_PROFILE_ENABLE=true
MEMORY_SEMANTIC_ENABLE=false

# 认证
AUTH_PROVIDERS_ENABLED=github,anonymous   # 逗号分隔，可加 google / magic_link
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
GITHUB_REDIRECT_URI=http://localhost:8000/api/auth/github/callback
JWT_SECRET=please-change-me-32-bytes
JWT_TTL_DAYS=30
ANON_COOKIE_NAME=copilot_session

# 测试/CI
TEST_MODE=false
```

### 10.3 部署模式

| 模式 | 启动 | 依赖 |
|---|---|---|
| **dev-mock** | `uvicorn main:app --reload` + RISK_EVIDENCE_PROVIDER=mock | 仅 API key |
| **dev-online** | 同上 + RISK_EVIDENCE_BASE_URL 指向远端 | API key + 远端 evidence |
| **prod-local** | docker-compose up | 本机 GPU + vLLM + evidence service |

### 10.4 docker-compose 形态（v1 草图）

```yaml
services:
  ragdataout:
    build: .
    ports: ["8000:8000"]
    env_file: .env
    depends_on: [evidence]
  evidence:
    image: schema-evidence-risk-profiling:latest
    ports: ["8001:8001"]
    deploy:
      resources:
        reservations:
          devices: [{ driver: nvidia, count: 1, capabilities: [gpu] }]
```

---

## 11. 开发过程留痕（docs/process）

### 11.1 目录结构

```
docs/
├── README.md                     # 文档索引
├── architecture/                 # 架构设计
│   ├── overview.md               # 本设计方案的精简版
│   ├── retrieval_pipeline.md
│   ├── agentic_rag_design.md
│   ├── memory_system.md
│   └── risk_profiling_design.md
├── decisions/                    # ADR (Architecture Decision Records)
│   ├── ADR-001-no-langchain.md
│   ├── ADR-002-bm25-rrf-fusion.md
│   ├── ADR-003-evidence-as-external-service.md
│   ├── ADR-004-mock-first-testing.md
│   ├── ADR-005-conversational-copilot-form.md     # 替代旧 ADR-005-no-user
│   ├── ADR-006-4-layer-architecture.md
│   ├── ADR-007-github-oauth-with-anonymous.md     # 🆕
│   └── ADR-008-owner-id-tenancy.md                # 🆕
├── process/                      # 开发过程留痕（每次重大改动一篇）
│   ├── README.md                 # 索引（按日期倒序表格）
│   └── YYYY-MM-DD-<topic>.md
├── evaluations/                  # 引用 evaluations/ 报告的总结
│   └── README.md
└── experiment_v1.md              # 本文件
```

### 11.2 单篇 process 留痕模板

```markdown
# YYYY-MM-DD <topic>

## 背景与动机
为什么要做？解决什么问题？

## 决策
做了什么选择？关键 trade-off
- 决策 1: ... 理由 / 替代方案
- 决策 2: ...

## 实现
关键文件、关键代码片段
- file_a.py: ...
- file_b.py: ...

## 验证
测试结果、运行日志、截图
- 跑通 xxx
- 性能数据 yyy

## 遗留问题
- [ ] todo 1
- [ ] todo 2

## 时长
约 Xh
```

### 11.3 ADR 模板

```markdown
# ADR-NNN: <决策标题>

- 状态: proposed | accepted | superseded by ADR-XXX
- 日期: YYYY-MM-DD

## 背景
当时面临什么问题？

## 决策
我们决定...

## 后果
正面：
- ...

负面/风险：
- ...

## 备选方案
- 方案 A: 否决理由 ...
- 方案 B: 否决理由 ...
```

---

## 12. 当前可优化点清单

按"工程价值 / 工作量"排序：

| # | 优化项 | 收益 | 工作量 | 所属 PR |
|---|---|---|---|---|
| 1 | `requirements-dev.txt` + `pytest.ini` + pre-commit + CI + Makefile | 开源标配 | 小 | PR-1 |
| 2 | `docs/architecture/`、`docs/decisions/`、`docs/process/` 骨架 | 简历亮点 | 小 | PR-1 |
| 3 | `domain/ports.py` + `domain/models.py`（不动现有代码） | 抽象基础 | 小 | PR-2 |
| 4 | infra 实现 Port + `tests/fakes/` + `tests/fixtures/` | 离线测试基础 | 中 | PR-3 |
| 5 | **`infra/auth/` + `AuthPort` + JWT 中间件 + GitHub OAuth** | 用户系统 | 中 | PR-4 🆕 |
| 6 | `KnowledgeService` 拆成 use case + `ComplianceCopilotAgent` + ToolRegistry | 解耦 + Agent 抽象 | 中 | PR-5 |
| 7 | `config.py` 拆 `settings.py` + `factories.py` | 启动逻辑清晰 | 小 | PR-5 |
| 8 | `data/chat_db.py` 移到 `infra/storage/sqlite_repo.py` + 加 `tasks` 表 | 命名规范 + 数据模型 | 小 | PR-5 |
| 9 | `infra/memory/` L1+L2 + 按 owner_id 隔离 | 补能力 | 中 | PR-6 |
| 10 | risk_profile 作为 Tool + 前端聊天 + 思考可见 + 产出物面板 | 接入新能力 | 中 | PR-7 |
| 11 | 日志结构化（JSON + request_id 贯穿） | 调试/面试 | 中 | PR-7 |

---

## 13. 重构实施计划（7 个 PR）

**关键纪律：每个 PR 独立可合并、CI 必须绿、配套一篇 process 留痕。**

### PR-1：基建与文档骨架（不动业务代码）

- [ ] `requirements-dev.txt`（pytest, pytest-asyncio, pytest-cov, ruff, mypy, pre-commit, responses, httpx, pyjwt）
- [ ] `pytest.ini`、`pyproject.toml`（ruff/mypy）、`.pre-commit-config.yaml`
- [ ] `.github/workflows/ci.yml`（lint + test，全离线）
- [ ] `Makefile`：test / lint / serve / docker-build / clean
- [ ] `docs/README.md` 索引
- [ ] `docs/architecture/overview.md`（精简版本文）
- [ ] `docs/decisions/ADR-001~008`（八篇）
- [ ] `docs/process/README.md` + `2026-06-04-design-v1.md`（含 v1.0→v1.1 演进）
- [ ] `experiment_v1.md` 移入 `docs/`（已完成）

**验收**：CI 绿、`make test` 跑通现有测试（API 依赖测试 skip）。

### PR-2：domain 层（纯新增）

- [ ] `domain/models.py`：User、Task、Message、ToolCall、Artifact、Chunk、Citation、SessionProfile、WebResult、EvidenceJudgement
- [ ] `domain/ports.py`：所有 Protocol（含 `AuthPort`、`UserRepoPort`、`TaskRepoPort`）
- [ ] `domain/errors.py`
- [ ] 单元测试
- [ ] `docs/process/2026-06-XX-domain-layer.md`

**验收**：现有功能可运行；domain 可 import 但未被使用。

### PR-3：infra 实现 Port + 离线测试基建

- [ ] `Embedder` / `ChatClient` / `Retriever` / `EvidenceClient` 实现 Port
- [ ] `tests/fakes/`：`FakeEmbedder`、`FakeChatClient`（场景化）、`FakeReranker`、`FakeWebSearch`、`FakeEvidence`
- [ ] `tests/fixtures/chat_scenarios/` ≥ 5 个
- [ ] `tests/conftest.py`：基础 fixture + 离线环境变量
- [ ] 迁移 3-5 个现有测试到 unit/integration
- [ ] 加 e2e 1-2 个
- [ ] CI `pytest --cov`
- [ ] `docs/process/2026-06-XX-offline-testing.md`

**验收**：`pytest -q` 全离线通过，覆盖率 ≥ 60%。

### PR-4：身份层（GitHub OAuth + 匿名）🆕

**目标**：在重构业务前打好身份地基；后续所有数据天然按 `owner_id` 隔离。

- [ ] `infra/auth/auth_service.py`（JWT 签发/验证）
- [ ] `infra/auth/providers/github.py`（OAuth flow，HTTP 走 `httpx`）
- [ ] `infra/auth/providers/anonymous.py`（生成 `anon:{uuid}`）
- [ ] `infra/storage/sqlite_user_repo.py`（含 `merge_owner` 实现）
- [ ] `api/middleware/auth.py`：JWT cookie → owner_id 注入 `request.state`
- [ ] `api/routes/auth.py`：`/anonymous`、`/github/login`、`/github/callback`、`/me`、`/logout`
- [ ] `tests/fakes/fake_oauth.py` + `tests/fixtures/oauth/*.json`
- [ ] integration 测试：用 `responses` 拦截 GitHub HTTP（验证真实 provider）
- [ ] e2e 测试：匿名 → 登录 → 数据合并
- [ ] `docs/decisions/ADR-007-github-oauth-with-anonymous.md`
- [ ] `docs/decisions/ADR-008-owner-id-tenancy.md`
- [ ] `docs/process/2026-06-XX-auth-layer.md`

**验收**：
- 首次访问自动获得匿名 cookie；点 GitHub 登录走完 fake OAuth 后，原匿名 task 出现在登录账户下
- 单元 + integration + e2e 共 ≥ 12 个测试，全离线通过
- 任何业务路由不传 owner_id 时不可用

### PR-5：app 层 use case + 配置工厂 + Agent

**目标**：把现有 `KnowledgeService` 拆成 use case，引入 `AppContainer` 与 `ComplianceCopilotAgent`。

- [ ] `config/settings.py` + `config/factories.py`
- [ ] `app/agent/copilot.py`：`ComplianceCopilotAgent`（ReAct 主循环）
- [ ] `app/agent/tools.py`：`ToolSpec` + `register_default_tools`
- [ ] `app/use_cases/run_copilot.py`、`ingest.py`、`task_management.py`
- [ ] `app/container.py`：`AppContainer`（注入身份层）
- [ ] `api/deps.py`、`api/routes/copilot.py`、`api/routes/tasks.py`、`api/routes/documents.py`
- [ ] `infra/storage/sqlite_task_repo.py`（含 messages/tool_calls/artifacts 表，全部带 task_id → owner_id 关联）
- [ ] `domain/errors.py` + `api/exception_handlers.py`
- [ ] 旧 `service.py` 变薄/标记 deprecated（保留旧路由直至 PR-7）
- [ ] use case + agent 单元测试（用 fake）
- [ ] `docs/process/2026-06-XX-agent-orchestration.md`

**验收**：`/api/copilot/chat` 走通至少 3 个剧本（搜法规 / 风险画像 / 主动追问）；use case 覆盖率 ≥ 80%。

### PR-6：记忆系统 L1+L2（按 owner_id）

- [ ] `infra/memory/short_term.py`（task_id 维度）
- [ ] `infra/memory/summary.py`（LLM 摘要 + 阈值触发）
- [ ] `infra/memory/profile.py`（owner_id 维度，简易 LLM 抽取）
- [ ] `MemoryPort` 接入 `ComplianceCopilotAgent`
- [ ] sqlite 表：`task_summaries`、`user_profiles`（含迁移 SQL）
- [ ] `/api/memory/profile` 端点（owner 隔离）
- [ ] 集成测试：> 20 条消息触发摘要；登录前后画像不混
- [ ] `docs/process/2026-06-XX-memory-l1-l2.md`

**验收**：长任务 prompt 不无限增长；匿名/登录画像隔离。

### PR-7：风险画像 Tool + 前端 Copilot UI + 收尾

- [ ] `risk/` 模块完成接入：`tool_registry["risk_profile"]`、`tool_registry["generate_checklist"]`
- [ ] `api/routes/_debug/risk.py`：`/judge`、`/factors`、`/tools` 仅在 dev 启用
- [ ] 前端重构：单聊天 + 思考流 + 工具调用卡片 + 产出物面板
- [ ] `auth.js`、`chat.js`（SSE）、`artifacts.js`、`tasks.js`
- [ ] 删除/隐藏旧 Tab 入口（`/api/ask`、`/api/research` 路由保留向后兼容 + deprecation 头）
- [ ] integration 测试：上传文档 → Agent 自主调风险画像 → 产出 artifact
- [ ] e2e：完整匿名 → 登录 → 体检 → 清单 路径
- [ ] 日志结构化（JSON + request_id）
- [ ] L4 语义记忆（可选）
- [ ] `docs/architecture/risk_profiling_design.md`
- [ ] `docs/process/2026-06-XX-copilot-ui.md`

**验收**：从前端拖一份隐私政策，单对话内 Agent 自动评估并产出可下载的整改清单。

---

## 14. 验收标准

### 14.1 功能验收

- [ ] 匿名首次访问可直接对话；点击 GitHub 登录后历史无缝继承
- [ ] Agent 自主选择 search_law / search_user_docs / risk_profile / web_search / ask_user / generate_checklist
- [ ] 长对话有摘要，跨任务有用户画像（按 owner_id 隔离）
- [ ] 文档/任务严格按 owner_id 隔离，未传 owner_id 的请求不可用

### 14.2 工程验收

- [ ] `pytest -q` 全离线通过（含 OAuth 路径）
- [ ] 测试覆盖率 ≥ 70%（核心 use case + agent + auth ≥ 80%）
- [ ] GitHub Actions CI 全绿
- [ ] `make lint` 无 ruff 错误；`mypy` 主路径无类型错误
- [ ] README：定位、快速开始、离线测试、OAuth 配置说明、API 文档链接
- [ ] `docs/architecture/` ≥ 3 篇、`docs/decisions/` ≥ 8 篇、`docs/process/` ≥ 7 篇

### 14.3 演示验收

- [ ] `docker-compose up` 一键启动（mock 模式无 GPU、无需配置 OAuth）
- [ ] 演示脚本：匿名提问 → 上传文档 → Agent 调风险画像 → GitHub 登录 → 历史保留

### 14.4 简历验收

- [ ] 一句话定位："对话式数据出境合规 Copilot：自研 evidence-state LoRA 模型 + Tool-Use Agent + Schema-guided 评估"
- [ ] README 截图/动图展示完整对话流
- [ ] 链接 `docs/decisions/`（特别是 ADR-005 / 007 / 008）
- [ ] 链接孪生项目 `schema-evidence-risk-profiling`

---

## 15. 开放问题与待决策项

本表只保留尚未决策的问题。已决策项见 `docs/decisions/`。

| # | 问题 | 默认建议 | 状态 |
|---|---|---|---|
| Q1 | Agent 主循环是否引入显式 planner（独立 plan→act→reflect） | v1 用 ReAct 单循环，PR-7 后视情况补 | 待定 |
| Q2 | 是否做 LangGraph 对照实现 | 单独沙盒分支，不进主项目 | 待定 |
| Q3 | 评估体系是否扩展端到端 Agent 评估（任务成功率） | 加 ≥ 10 条 golden task，PR-7 时入 evaluations/ | 待定 |
| Q4 | 前端是否迁框架 | v1 保持 vanilla JS；v2 视情况 React | 待定 |
| Q5 | OAuth 是否预留 Google / Magic Link | 留 provider 插件位但 v1 不实现 | ✅ 已决（接口位） |
| Q6 | mypy strict 还是 lenient | lenient（仅核心类型） | ✅ 已决 |
| Q7 | 重构期间旧路由是否保留 | 保留至 PR-7，加 deprecation 头 | ✅ 已决 |
| Q8 | docker-compose prod 版何时出 | PR-7 末或 v1.1 后续 | 待定 |
| Q9 | 任务标题如何生成 | Agent 第一轮回复时 LLM 抽取一句 | 待定 |
| Q10 | 是否给匿名身份设置 30 天过期清理 | v1 不清理（除非用户主动）；v2 加定时任务 | 待定 |

---

## 附录 A：关键术语对照

| 术语 | 含义 | 出处 |
|---|---|---|
| Tool | Agent 可调用的具名能力（含 schema + handler） | 本项目 / OpenAI tool-use |
| Tool Registry | 所有 Tool 的声明式注册中心 | 本项目 |
| ReAct | Reason + Act 主循环（思考→工具→观察→...） | Yao et al. 2022 |
| Use Case | 应用层薄入口（Agent 之上 / API 之下） | Clean Architecture |
| Port | 领域层定义的抽象接口（Protocol） | Hexagonal Architecture |
| Adapter | infra 层对 Port 的具体实现 | Hexagonal Architecture |
| owner_id | 跨匿名/登录的统一身份键（`anon:{uuid}` 或 `github:{login}`） | 本项目 |
| Task | 一次完整对话上下文（含 messages / tool_calls / artifacts） | 本项目 |
| Artifact | Agent 中间或最终产出（risk_profile / checklist / ...） | 本项目 |
| Factor | 风险画像中的一个合规因子 | schema-evidence-risk-profiling |
| Evidence State | 文档对某 target 的证据状态（5+1 类） | evidence_v1 schema |
| Gating Factor | 决定其他 factor 是否适用的门控因子（如 F1） | 本项目 |

## 附录 B：技术栈一览

| 层 | 选型 |
|---|---|
| Web 框架 | FastAPI + Uvicorn |
| 配置 | pydantic-settings |
| 向量库 | Chroma |
| 关键词检索 | rank-bm25 + jieba |
| 重排 | sentence-transformers (BAAI/bge-reranker-base) |
| 关系存储 | SQLite |
| LLM 通道 | 智谱 GLM-4-Flash / OpenAI 兼容 / Ollama 本地 |
| Embedding | embedding-3 / nomic-embed |
| Evidence 模型 | Qwen2.5-7B + LoRA（外部 vLLM 服务） |
| 测试 | pytest + responses + httpx |
| 认证 | Authlib / 自实现 OAuth + PyJWT（HS256） |
| CI | GitHub Actions |
| Lint | ruff + mypy + pre-commit |
| 容器化 | Docker + docker-compose |
| 文档 | Markdown + MkDocs（可选） |

---

*本设计文档为活文档，后续每个 PR 完成后回填实际数据与偏离记录。*
