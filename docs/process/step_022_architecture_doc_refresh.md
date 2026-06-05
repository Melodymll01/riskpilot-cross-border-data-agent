# Step 022 — 架构文档刷新（overview 重写 + ADR-009..013 增量）

> 状态：**Done**
> Commit：（本提交）
> 范围：**纯文档**，不动代码 / 测试 / CI
> 测试：**519 passed**（与 Step 021 一致零回归）/ scoped ruff 0

---

## 1. 目标

把"文档跟不上代码"的偏差一次性修齐：

- `docs/architecture/overview.md` 自 Step 001 冻结后未更新，与现状（13 Port / 6 use case / Closure DI / Strangler Fig / 审计副作用）已脱节
- `docs/decisions/ADR-001..008` 只覆盖到设计冻结期；Step 008-021 的关键决策散落在 21 个 process 文档，没有"决策来源"统一索引
- `interview_doc/` 与 README 的全景图缺失（留 Step 023 / 024 单独做）

本步聚焦"工程师视角的架构与决策"，**不**涉及面向求职/演示的对外门面。

---

## 2. 改动文件清单

### 新增 6

| 文件 | 用途 |
|---|---|
| `docs/decisions/ADR-009-closure-router-container-di.md` | Step 008-010 落地的路由与容器绑定方式 |
| `docs/decisions/ADR-010-strangler-fig-v1-v2.md` | Step 010-011 v1/v2 双 API 并存策略 |
| `docs/decisions/ADR-011-react-agent-self-implemented.md` | Step 009 自实现 ReAct + LLM JSON 决策协议 |
| `docs/decisions/ADR-012-admin-rbac-allowlist.md` | Step 013/018/019 admin 白名单 + 401/403 二段守门 |
| `docs/decisions/ADR-013-audit-side-effect-semantics.md` | Step 021 审计副作用语义 + extra_json 自由 dict |
| `docs/process/step_022_architecture_doc_refresh.md` | 本文档 |

### 重写 1

| 文件 | 改动 |
|---|---|
| `docs/architecture/overview.md` | 完全重写：v1.1 冻结版（13 abstract / 4 层口号）→ 现状版（演进时间轴 / 13 Port 表 / 6 use case 表 / 3 个 Mermaid 时序图 / 权限矩阵 / 测试金字塔 / CI 策略 / ADR 全量索引 / 边界 / 路线） |

### 微调 4（仅顶部加 Augmented-by 注脚，主体不动）

| 文件 | 注脚 |
|---|---|
| `docs/decisions/ADR-001-no-langchain.md` | → ADR-011（落地延伸） |
| `docs/decisions/ADR-006-4-layer-architecture.md` | → ADR-009（DI 增强）+ ADR-010（双 API 策略） |
| `docs/decisions/ADR-007-github-oauth-with-anonymous.md` | → ADR-012（admin 二段守门） |
| `docs/decisions/ADR-008-owner-id-tenancy.md` | → ADR-012（admin 白名单是 owner_id 之上的第二轴） |

### 更新 1

| 文件 | 改动 |
|---|---|
| `docs/process/README.md` | 索引表追加 Step 022 行 |

---

## 3. 设计决策

### D1：「文档随实现演进」而非「文档冻结于设计稿」

`docs/experiment_v1.md`（1283 行 v1.1 冻结稿）保留不动作为历史档案；overview 明确声明"本文档随实现演进而非冻结于 Step 001"，并锚定当前版本（Step 021 / commit f1f1824）。

未来每个里程碑级 Step 完成后 overview 至少更新一次"项目演进轨迹"段。

### D2：新 ADR 用「追溯」时间，不伪造日期

ADR-009..013 都是 2026-06-05 追溯整理（实际落地分别在 Step 009 / 010 / 011 / 013 / 021），ADR 顶部明示"追溯 Step XXX 落地决策"。

避免假装"这个决策是 2026-06-04 就写好的"——决策本身是过程中浮现的，文档化是回溯整理。

### D3：老 ADR 用 Augmented-by 而非 Superseded-by

ADR-006 的"4 层架构"在 Step 008-010 没有被推翻，只是补充了「在 4 层基础上怎么做 DI」与「双 API 怎么共存」。所以用 **augmented by** 而不是 superseded by：

- `accepted` 状态保留
- 顶部加"后续补充：ADR-XXX"链接列表
- 主体内容**完全不动**（保留历史决策的原貌）

ADR 演化原则：**只追加，不改写**。

### D4：选 5 个 ADR 而不是 8 个

候选过的 8 个新 ADR 里淘汰 3 个：

| 候选 | 否决理由 |
|---|---|
| Task.mode 三模式链路透传 | 实现细节，写进 overview §5 + §6.2 即可 |
| KbDocumentRepoPort "先删后插"幂等 | 同上，写进 overview §4 即可 |
| Scoped CI 策略（不上 mypy）| 同上，写进 overview §8.3 即可 |

ADR 准入标准：**跨多个 Step / 涉及外部接口契约 / 有否决备选 / 影响后续设计**。仅实现细节走 overview / step 文档。

### D5：ADR 文末必须有「关联」段

每个 ADR 末尾必含三类链接：
- 同主题 ADR（前置 / 后续）
- 实现文件（绝对路径）
- 过程 step（命名约定 step_NNN_*.md）

让"决策 → 代码 → 过程"三角能正反查。

---

## 4. 不做（边界守门）

| 不做 | 留给 |
|---|---|
| 重写 `experiment_v1.md`（v1.1 冻结稿） | 保留历史档案 |
| 重写 `interview_doc/` 5 个面试文档 | Step 024（候选） |
| 重写项目根 README | Step 024（候选） |
| 写「测试策略文档」`docs/testing/strategy.md` | Step 025（候选） |
| 把 ADR 翻成英文 | 项目当前中文为主，不做 |
| 改 ADR 状态枚举或加 `Superseded-by` 字段 | 当前 8 个 accepted ADR 都没被推翻 |
| 在 overview 加 1283 行级别的细节 | overview 是索引，细节在 ADR / step / 代码 |

---

## 5. 文档结构变化对比

### `docs/architecture/overview.md`

| 段落 | 旧版 | 新版 |
|---|---|---|
| 一句话定位 | ✅ | ✅（不变）|
| 项目演进轨迹 | ❌ | ✅ 新增（7 段时间轴） |
| 架构层次图 | ASCII 4 行 | ASCII 详细 + 依赖方向说明 |
| Port 清单 | 8 个 | **13 个**（分 4 类：身份 / 持久化 / LLM-RAG / 加载 / 记忆） |
| Use Case 清单 | ❌ | ✅ 新增 6 个表 |
| 关键运行时序 | ❌ | ✅ 新增 3 个 Mermaid 图（OAuth / Copilot ReAct / 审计） |
| 身份模型 | 3 行 | 完整三段权限层级 + KB 权限矩阵 |
| 测试 / CI | ❌ | ✅ 新增（金字塔 + Fake 原则 + CI 现状） |
| ADR 索引 | 8 个 | **13 个**（标注 augmented / accepted） |
| 技术栈 | 1 段 | 完整 8 行表 |
| 边界与"不做" | ❌ | ✅ 新增 8 条 |
| 后续路线 | ❌ | ✅ 新增 022a-e + 023+ |

### `docs/decisions/`

```
旧:  ADR-001..008   (8 个，设计冻结期)
新:  ADR-001..013   (+5 新增追溯 Step 009-021 决策)
     ADR-001/006/007/008 顶部加 Augmented-by 链接（主体不动）
```

---

## 6. 验证

```powershell
# 文档不影响代码 / 测试 / CI
.venv\Scripts\python.exe -m pytest -q --ignore=tests/eval_ood.py --ignore=tests/smoke_bm25_rrf.py
# → 519 passed（与 Step 021 一致）

.venv\Scripts\python.exe -m ruff check `
    domain app `
    infra/auth infra/kb infra/risk_profile infra/audit `
    api/v2 `
    config.py main.py `
    tests/api tests/app tests/domain tests/infra tests/fakes
# → All checks passed!
```

文档自身校验：

- 所有内部链接相对路径正确（`../decisions/ADR-XXX.md`、`../process/step_NNN_*.md`）
- 5 个新 ADR 都有 6 段标准结构（背景 / 决策 / 后果 / 备选 / 关联 / 可选实证）
- ADR-006 / 001 / 007 / 008 主体内容字节对字节未变（只在顶部加注脚）

---

## 7. 下一步候选

| 候选 | 范围 | 估计 |
|---|---|---|
| 023a：admin 审计 UI（原 022a） | 前端面板 | 中 |
| 024a：interview_doc/ 重写对齐到 Step 021 | 文档（求职用） | 中 |
| 024b：项目根 README 重写为"全景 + 跑通 + 演进" | 文档（GitHub 门面）| 小 |
| 025a：私人 KB owner_id 隔离（原 022b） | 后端工程 | 大 |
| 025b：mypy 复活（原 022c） | 工程纪律 | 中 |
| 025c：登录端点也落 AuditLogPort（原 022e） | 工程 + 隐私决策 | 中 |

建议节奏：**022 (本步, 文档) → 023 (工程：审计 UI) → 024 (文档：interview + README) → 025 (工程：择一)**，工程-文档交替推进。
