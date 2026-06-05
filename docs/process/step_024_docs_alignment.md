# Step 024 — 项目门面文档对齐（README v2 化 + interview_doc 增量补档）

> 配套 Step 022（架构文档刷新，对内）+ Step 023（admin 审计 UI，工程闭环）后，把**对外宣讲**层面的文档也对齐到当前代码状态。
>
> 区别：Step 022 受众=未来开发者（看 ADR / 架构图），Step 024 受众=GitHub 访客 / 面试官 / 准备面试的自己。

## 1. 目标

- 让 GitHub 仓库首页（`README.md`）第一眼能讲清楚当前代码是 **DDD v2 + OAuth + 审计闭环 + CI 绿** 的状态，不再停留在 v1 单体的旧描述
- 让自己面试前快速复习时手边有一份 **「Step 008-023 增量补档」** ——补齐原 5 篇 interview_doc（写于 2026.04）之后所有新概念的标准答案

## 2. 改动清单

### 公开（git tracked）

| 文件 | 改动 |
|---|---|
| [README.md](../../README.md) | **5 大段重写**：(1) 头部 badges + 项目演进时间轴 (2) 系统架构（4 层 DDD Mermaid 图 + Agentic RAG 决策环路两张图） (3) 功能矩阵表（匿名/普通/admin 三角色 × 8 能力） (4) 项目结构（按 DDD 层次重组） (5) API 接口表（v2 17 个 + v1 已删除标注） (6) 技术栈刷新 (7) `.env` 示例追加 OAuth + admin_user_ids (8) 使用说明扩到三模式 + 知识库 + 审计面板 (9) 末尾追加「项目文档」段链到 overview/ADR/process |
| [docs/process/step_024_docs_alignment.md](step_024_docs_alignment.md) | 本文档 |
| [docs/process/README.md](README.md) | 追加 Step 024 行 |

### 本地（git ignored）

> `interview_doc/` 在 `.gitignore:102` 已经被忽略，本步**仅本地更新**面试材料，不入 git：

| 文件 | 改动 |
|---|---|
| `interview_doc/2026-补充-DDD重构与v2API.md`（新） | 一份**总补档**：6 节内容覆盖 Step 008-023 全部新增点——关键数字 / 一句话定位 v2 / DDD 4 层架构与依赖方向 / 13 Port 分类 / 6 Use Case / 10 个新 Q&A（Q11-Q20：DDD / Strangler / OAuth / RBAC / Audit / Closure Router / 自实现 ReAct / 测试涨幅 / CI / ADR）/ v1 旧话术 vs v2 现状映射表 / 面试时必须打开的 GitHub 链接清单 |
| `interview_doc/项目经历-面试用.md` | **3 处精准更新**：(1) 简历版完全重写：加 DDD 重构 / Strangler / OAuth / admin / audit / CI / 215→527 测试涨幅；(2) Q7「不足项」第 5 条「没有用户认证体系」改成「私人 KB 隔离待加 + mypy 待复活」（原说法已被推翻）；(3) 末尾追加「六、v2 重构后新增的 10 个追问」段 + 指引到补档 |

### 不动的文件（理由）

| 不动 | 理由 |
|---|---|
| `interview_doc/面试八股文-RAG系统.md` | 通用 RAG 八股，与 v1/v2 实现无关，话术稳定可保留 |
| `interview_doc/面试高频追问-深度解析.md` | 同上 + 已熟记，避免动那些"嘴上已经熟"的段落 |
| `interview_doc/项目详解-面试备战.md` | 41 KB 长篇详解写于 v1，整篇重写成本太高；与补档配套阅读即可（补档明确说"v1 旧话术怎么衔接 v2 新事实"） |
| `interview_doc/Agent岗位面试-项目包装版.md` | 4 月份补写的 Agent 角度包装版，Agent 部分（ReAct/工具/AgentEvent）核心结论仍成立；细节通过补档 Q17 补齐 |

## 3. 核心决策

### D1：interview_doc 走「补档 + 精修」而非「全部重写」

**选**：新增 1 篇增量补档（覆盖所有 Step 008-023 新概念）+ 精准修 1 篇（STAR 简历文档的过时段落）。

**否**：5 篇全部逐句重写——4 月份的话术已经背熟，全重写会污染已固化的肌肉记忆；且耗时巨大。

**理由**：interview_doc 是工具不是产品，「能快速查到 v2 新答案」比「文档结构完美一致」重要。

### D2：interview_doc 继续 git-ignore

**选**：保持 `.gitignore:102 interview_doc/` 不动，新文件同样不提交。

**否**：把 interview_doc 公开到 GitHub——面试材料涉及个人话术、备战策略、面试技巧（如"必须打开的 GitHub 链接清单"），公开后等于把底牌亮给所有访客 + 招聘方反向利用。

### D3：README 「项目演进」段用 ASCII 图而非 Mermaid

**选**：v1→v2 的演进时间轴用 ASCII，与下面的 Mermaid 架构图差异化展示。

**否**：再加一张 Mermaid——README 已经有 2 张 Mermaid（DDD 架构 + Agentic RAG 环路），第 3 张会让首页加载/渲染显得很重；ASCII 在 GitHub markdown 中保真度更高（不需要 Mermaid 解析）。

### D4：API 表把 v1 已删端点用删除线列出

**选**：`~~POST /api/ingest/file~~ ❌ Step 016d 删除 | 已迁移到 /api/v2/documents/file`

**否**：直接不列——很多人之前可能 fork / star 过项目，留着删除标注让他们知道**为什么 404**、新端点在哪。

### D5：在 README「项目文档」段直接列 9 个 ADR 链接

**选**：把 13 个 ADR 中的 9 个（与 v2 直接相关的）以扁平 inline 列表方式列出，不折叠。

**否**：折叠到 `<details>` 标签——GitHub markdown 在移动端折叠交互不友好；直接列虽然有点长但便于面试官扫读。

## 4. 不做

- ❌ 不动 `docs/architecture/` 任何文件（Step 022 刚做完）
- ❌ 不动任何 ADR（同上，老 ADR 的 Augmented-by 标记是 Step 022 加的，本步不重复劳动）
- ❌ 不翻译 README 成英文（YAGNI；当前面试官 / 面试官 100% 中文）
- ❌ 不动 `docs/experiment_v1.md`（v1.1 冻结稿作为历史档案）
- ❌ 不动 5 篇 interview_doc 中的 4 篇（D1 理由）

## 5. 验证

### 自动

```powershell
# 文档变更不涉及代码，主要校验 README markdown 不破
.\.venv\Scripts\python.exe -m pytest -q --ignore=evaluations/ood/eval_ood.py --ignore=tests/smoke_bm25_rrf.py
# => 期望仍 527 passed（零代码回归）

.\.venv\Scripts\python.exe -m ruff check `
  domain app `
  infra/auth infra/kb infra/risk_profile infra/audit `
  api/v2 config.py main.py `
  tests/api tests/app tests/domain tests/infra tests/fakes
# => 期望 All checks passed
```

### 人工

- [x] README.md 在 VSCode 预览模式下 2 张 Mermaid 渲染正常
- [x] README.md 所有相对链接（`docs/architecture/overview.md` / `docs/decisions/*.md` / `docs/process/README.md`）可点击
- [x] `git status` 不能看到 `interview_doc/*`（gitignore 生效校验）
- [x] `git ls-files interview_doc/` 返回 0（未被跟踪过）

## 6. 结构对比（Step 023 之后）

```
docs/
├── architecture/overview.md         # Step 022 已重写
├── decisions/                       # Step 022 + ADR-009..013（共 13 个）
└── process/
    ├── README.md                    # +Step 024 行
    ├── ...
    └── step_024_docs_alignment.md   # 新（本文）

README.md                            # ★ 全面 v2 化（374 行）

interview_doc/  (git ignored)
├── 面试八股文-RAG系统.md            # 不动
├── 面试高频追问-深度解析.md         # 不动
├── 项目经历-面试用.md               # ★ 简历版 + Q7 + 追加 Q11-Q20 索引
├── 项目详解-面试备战.md             # 不动
├── Agent岗位面试-项目包装版.md      # 不动
└── 2026-补充-DDD重构与v2API.md     # ★ 新增：Step 008-023 全量增量补档
```

## 7. 后续候选

| 编号 | 类型 | 描述 |
|---|---|---|
| 025a | 工程 | 私人 KB `owner_id` 隔离 |
| 025b | 工程 | mypy 复活 |
| 025c | 工程 | 登录端点接入 `AuditLogPort` |
| 026a | 工程 | audit 时间范围过滤 + 导出 CSV |
| 026b | 文档 | `evaluations/` 三套评测的方法论文档化（chunk_params / OOD / benchmark 每个写一篇 method.md） |

**建议节奏**：024（本步 文档）→ 025（工程择一）→ 026（视优先级）。
