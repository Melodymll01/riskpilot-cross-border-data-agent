# Phase 0 实施复盘：冻结基线与收敛产品主线

- 状态：已完成
- 日期：2026-08-17
- 基线提交：`50272310957467bb21eee8d7ca525e4b3ac71c6c`
- 行为变化：无

## 1. 本阶段目标

在不修改运行时代码的前提下，回答四个问题：

1. 当前仓库真实处于什么状态；
2. 秋招展示时哪条业务链路是主线；
3. 后续基础设施为什么这样选；
4. 每次代码修改如何沉淀为可复习材料。

## 2. 为什么这样设计

RiskPilot 已有较多 AI 能力。如果直接开始接 PostgreSQL、Celery 或 pgvector，会出现：

- 每个组件都能工作，但无法说明它服务于哪条业务闭环；
- README 继续平铺 Copilot、Memory、Visual、Research，核心 Agent 不突出；
- 技术选择只能回答“流行”，不能回答职责、替代方案和代价；
- 代码改完后再补文档，容易遗漏关键决策。

因此 Phase 0 只冻结事实、建立路线和 ADR。它不修改核心行为，避免把架构决策和功能
回归混在同一阶段。

## 3. 修改文件与实现方式

### `.gitignore`

**为什么改**

原规则只允许 `docs/architecture`、`docs/design`、`docs/decisions` 和 `docs/guides`。
生产化路线和逐阶段复盘会被 `docs/*` 忽略，无法和代码一起版本化。

**怎么实现**

- 放行 `docs/roadmap/**`；
- 放行 `docs/implementation/**`；
- 在显式跟踪说明中写清两个目录的职责。

### `docs/roadmap/autumn-recruitment-production-plan.md`

**为什么新增**

需要一个跨 Phase 的 SSOT，统一产品定位、当前/目标架构、P0/P1/P2、非目标和验收门禁。

**怎么实现**

- 记录 Git、Port、Use Case、路由、测试文件和离线回归基线；
- 明确 Case Assessment Agent 是唯一主线；
- 把 Copilot、Memory、Visual、Research 降为辅助模块；
- 绘制当前架构和目标架构 Mermaid 图；
- 为 Phase 0～10 定义最小交付和下一阶段门禁；
- 固定每个 Phase 的复盘模板。

### `docs/decisions/ADR-017-LangGraph与Celery职责分离.md`

**为什么新增**

LangGraph 和 Celery 都能“跑长任务”，但失败语义和职责不同，需要提前冻结边界。

**怎么实现**

- LangGraph 负责决策、状态和 HITL；
- Celery 负责耗时任务、重试、超时和 Worker；
- 通过业务数据库中的 `run_id/job_id` 关联；
- 对比只用 LangGraph、只用 Celery 和 Temporal 三种方案。

### `docs/decisions/ADR-018-LLM与确定性规则引擎分离.md`

**为什么新增**

正式合规门槛必须稳定复现，不能由模型自由判断。

**怎么实现**

- 列出 LLM 可做的规划、提取、解释和草拟；
- 列出代码必须做的权限、门槛、状态、引用和审批；
- 要求 Pydantic Schema 和服务端原文复核；
- 定义模型不能覆盖 `PolicyRuleEngine`。

### `docs/decisions/ADR-019-保留DDD与Port-Adapter边界.md`

**为什么新增**

后续会加入大量基础设施，必须防止 ORM、Celery 和框架对象污染 domain。

**怎么实现**

- 冻结 `api → app → domain`、`infra → domain ports`；
- 要求 SQLAlchemy Model 只在 infra；
- 要求 SQLite/PostgreSQL、Local/S3、Chroma/pgvector 共用 Port 语义；
- 定义 Repository contract 测试作为验收证据。

### `docs/decisions/ADR-020-PostgreSQL与pgvector生产存储.md`

**为什么新增**

需要说明为什么从 SQLite/Chroma 走向 PostgreSQL/pgvector，以及为什么暂不增加独立
搜索集群。

**怎么实现**

- PostgreSQL 承担事务、约束、乐观锁和核心查询；
- pgvector 与租户范围过滤在同一 SQL 层下推；
- SQLite/Chroma 保留为 local profile；
- 优先迁移核心链路，不一次重写全部 Repository。

### `docs/decisions/ADR-021-单核心Agent与受限子图.md`

**为什么新增**

多个 Agent 互相对话不等于更强的业务 Agent，且会增加成本和权限风险。

**怎么实现**

- 只保留 Case Assessment Agent 为核心；
- Deep Research 是有预算和权限边界的子图；
- Copilot、Memory、Visual、Evidence QA 是辅助能力；
- 明确只有独立上下文、工具权限、指标或生命周期时才允许拆 Agent。

### `README.md`

**为什么改**

让读者从 README 能进入生产化路线和本阶段复盘，而不是只能看到功能清单。

**怎么实现**

- 文档索引新增生产化路线；
- 文档索引新增实施复盘目录；
- 不在 Phase 0 改动现有功能指标。

### `docs/architecture/overview.md`

**为什么改**

当前架构文档需要显式声明“Case Assessment 是主线”，避免辅助能力继续占据同等叙事。

**怎么实现**

- 在架构开头增加产品主线与辅助能力边界；
- 链接生产化路线和 Phase 0 复盘；
- 不修改现有运行架构描述。

## 4. 数据模型变化

无。

本阶段只记录未来 PostgreSQL 表设计原则，没有修改任何 Pydantic Domain Model、SQLite
schema 或 Repository。

## 5. API 变化

无。

现有 `/api/v2`、`/api/v3` 路由和响应保持不变。

## 6. Agent 状态变化

无。

当前 Case Assessment Graph 仍为：

```text
load_case
→ authorize
→ validate_documents
→ detect_missing_facts
→ select_policy_snapshot
→ evaluate_policy_rules
→ draft_assessment
→ human_review
→ complete
```

本阶段只确定 Phase 5 的目标状态，不提前修改 Graph。

## 7. 测试结果

### 完整离线基线

命令：

```bash
OPENAI_API_KEY=sk-test-fake \
OPENAI_API_BASE=http://127.0.0.1:9/v1 \
CHAT_API_KEY=sk-test-fake \
CHAT_API_BASE=http://127.0.0.1:9/v1 \
LLM_PROVIDER=api \
ENABLE_RERANKER=false \
HF_HUB_OFFLINE=1 \
TRANSFORMERS_OFFLINE=1 \
AUTH_PROVIDERS_ENABLED=github,anonymous \
GITHUB_CLIENT_ID=fake_ci_client_id \
GITHUB_CLIENT_SECRET=fake_ci_client_secret \
JWT_SECRET=ci-test-jwt-secret-do-not-use-in-prod-32chars-min \
ADMIN_USER_IDS='' \
uv run pytest -q --ignore=tests/smoke_bm25_rrf.py
```

结果：

```text
1208 passed, 1 skipped, 68 warnings in 41.13s
```

### 零密钥探针

命令：

```bash
env -u OPENAI_API_KEY -u CHAT_API_KEY \
  -u LLM_PROVIDER -u EMBED_PROVIDER \
  .venv/bin/python -c 'import config'
```

结果：

```text
RuntimeError: OPENAI_API_KEY 未配置
```

该失败是 Phase 1 的已知输入，不属于 Phase 0 回归。

## 8. 尚未解决的风险

1. `config.py` import 阶段校验真实 Key，裸 pytest 失败；
2. CI 仍是 scoped Ruff，format 和 mypy 未成为强门禁；
3. 当前有 68 条 warning，需要分类处理；
4. README 首屏仍平铺较多辅助能力，Phase 1 会先收敛主线；
5. 当前 Compose 只有 app，不能证明多进程生产拓扑；
6. 当前本地 `main` 比远端领先 2 个提交，尚未 push。

## 9. 下一阶段建议

进入 Phase 1，严格按以下顺序：

1. 删除 `config.py` import-time 真实密钥校验；
2. 把校验移动到具体模型 Adapter 或应用 startup readiness；
3. 让裸 `pytest` 零密钥运行；
4. 再处理 Ruff/format/mypy 全量门禁和 warning；
5. 最后补 `/health/live` 与 `/health/ready`。

## 10. 验收标准

| 验收项 | 结果 | 证据 |
| --- | --- | --- |
| 有可执行路线图 | 满足 | `docs/roadmap/autumn-recruitment-production-plan.md` |
| 有当前与目标架构图 | 满足 | 路线图第 3、4 节 |
| 有明确非目标 | 满足 | 路线图第 8 节 |
| 有 5 份关键 ADR | 满足 | ADR-017～ADR-021 |
| 没有修改核心行为 | 满足 | 只修改 `.gitignore` 和 Markdown |
| 原有测试保持不变 | 满足 | `1208 passed, 1 skipped` |

Phase 0 验收通过。
