# Step 023 — admin 审计日志前端面板（offset 分页 + 三段式权限可视化）

> 配套实现：Step 021 落地的 `AuditLogPort` + `/api/v2/audit/logs` 端点，本步把"读路径"延伸到前端，让 admin 在浏览器里就能翻看自己 / 其他 admin 在 KB 上的写操作流水。
>
> 同时给 `list_recent` 加了 `offset` 分页（先前只有 `limit + 过滤`），不破坏老 client 签名。

## 1. 目标

把 Step 021 "写完就睡在 SQLite 里"的审计数据搬到 UI 上，形成 **写入 → 落库 → 后台可读 → 前端可视** 的完整闭环；并补齐分页能力以应对多 admin 长期使用后的增长。

## 2. 改动清单

### 后端（4 文件，纯加 `offset` 形参）

| 文件 | 改动 |
|---|---|
| [domain/ports.py](../../domain/ports.py) | `AuditLogPort.list_recent` 加 `offset: int = 0`（kw-only）+ docstring 补"offset 用于分页"说明 |
| [infra/audit/sqlite_audit_repo.py](../../infra/audit/sqlite_audit_repo.py) | `list_recent` 实现加 `params.append(offset)` + SQL 改 `LIMIT ? OFFSET ?` |
| [tests/fakes/fake_audit_log.py](../../tests/fakes/fake_audit_log.py) | 重写 `list_recent`：先全量过滤再 `filtered[offset : offset + limit]` 切片，与 SQL 同语义 |
| [api/v2/audit.py](../../api/v2/audit.py) | `list_audit_logs` 新增 `offset: int = Query(0, ge=0)`；未来扩展注释删 `offset` 项保留 `cursor` |

### 测试（2 文件，+8 用例）

| 文件 | 新增 |
|---|---|
| [tests/infra/test_sqlite_audit_repo.py](../../tests/infra/test_sqlite_audit_repo.py) | `TestPagination` × 4：`test_offset_skips_n` / `test_offset_with_limit_window` / `test_offset_beyond_total_returns_empty` / `test_offset_respects_filters` |
| [tests/api/test_audit.py](../../tests/api/test_audit.py) | `TestPagination` × 4：`test_offset_default_is_zero` / `test_offset_paginates` / `test_offset_beyond_total_returns_empty` / `test_offset_negative_rejected`（422） |

### 前端（5 文件）

| 文件 | 改动 |
|---|---|
| [frontend/api.js](../../frontend/api.js) | 新增 `audit.list({limit,offset,action,actor_id})` 命名空间；`URLSearchParams` 构造查询串，空字符串过滤参数不带出去 |
| [frontend/index.html](../../frontend/index.html) | 侧栏新增 `<button id="nav-audit" data-view="audit">` admin-only 入口 + `<main id="audit-pane">` 主区（header toolbar / table / footer pager 三段） |
| [frontend/admin-audit.js](../../frontend/admin-audit.js) | **新文件 ~180 行**：mount 幂等绑定 / refresh 拉当前页 / 渲染时间(本地时区)+actor+action+resource+状态徽章+extra_json 摘要 / 翻页按钮按 `lastCount < PAGE_SIZE` 判到底 / actor 输入框 300ms debounce / 全字段 escapeHtml 防 XSS |
| [frontend/app.js](../../frontend/app.js) | `_currentView` 扩 `"chat" \| "kb" \| "audit"` 三态；`switchView` 加 `view === "audit" && !getUser()?.is_admin` 守门；`applyKbGate` 内追加 audit 入口的 `hidden` 切换 + admin 失效时踢回 chat |
| [frontend/style.css](../../frontend/style.css) | +180 行 `.audit-pane` `.audit-toolbar` `.audit-table` `.audit-pager` `.audit-badge-ok/err` `.audit-extra`；色板复用 `--brand` / `--ok` / `--err`，与 KB 面板视觉一致 |

## 3. 核心决策

### D1：offset 而非 cursor 分页

**选**：URL `?offset=N&limit=50` 简单整数分页。

**否**：cursor（如 `?after=<id>`）虽然能避免"翻页中数据变化导致跳条"，但：
- audit 数据只增不删（D6 of ADR-013），不存在变化导致顺序乱跳
- admin 翻看场景每页 50 条够用，目录式翻页比 cursor 更符合直觉
- offset 实现 = SQL `LIMIT ? OFFSET ?`，复杂度 O(1) 改动

未来若进入"千万条审计"规模可换 cursor，目前不投入。

### D2：响应不返回 `total`

**选**：`AuditLogListResponse` 只含 `entries[]` + `count`（= `len(entries)`），不暴露全表总数。

**否**：返回 `total` 看似友好，但需要额外 `SELECT COUNT(*) FROM audit_log WHERE ...`，在过滤组合下要二次扫描；admin 翻页判到底用「当前页 `count < limit` ⇒ 末页」就够了，前端 `renderPager()` 据此 disable 下一页按钮。

### D3：不引入 `ListAuditLogsUseCase`

**选**：API 路由继续直调 `container.audit_log.list_recent(...)`，与 Step 021 风格一致。

**否**：DDD 教条派会要求"端口的所有调用都走 use case"，但本 case：
- 没有业务编排（不需要把多个 port 组合）
- 没有事务边界（纯只读单端口）
- 没有可复用价值（除 admin UI 不会有其他调用方）

引入贫血 use case 反而增加一层 indirection，与 Step 021 决策一致。

### D4：前端 admin 入口 = 双层 gate（CSS hide + JS 守门）

**选**：同时做两件事：
1. UI：`#nav-audit` 默认 `hidden`，仅在 `is_admin` 为 true 时 `applyKbGate` 解除隐藏
2. 路由：`switchView("audit")` 先检查 `getUser()?.is_admin`，false 时直接 return

**否**：只做 1 不做 2：用户即使没看到入口，仍可手工调 `switchView` 或 hash 路由 hack 进面板（虽然端点也是 admin-only 会 403，但 UI 上的"半开半闭"体验差）。

**否**：只做 2 不做 1：入口对非 admin 可见但点击无效，引起困惑。

API 端点的 admin-only（401/403 二段守门）保持 ADR-012 不变，UI 是**第三层防线**而非唯一防线。

### D5：extra_json 在表格里用"摘要 + 完整 title"展示

**选**：每行只显示前 3 个 key=value（每个 value 截到 28 字符），完整 JSON 用 `<span title="...">` 鼠标悬停可看。

**否**：折叠展开按钮——审计场景是"扫一眼看出谁干了啥"，hover 即可比点击更快。

**否**：把 JSON 拍平到表格列——`extra_json` 各 action 的 schema 不同（如 `kb.delete` 有 `deleted_count`，`kb.ingest_file` 有 `category`/`bytes`），强行展开会让表格列数爆炸。

## 4. 不做（明确边界）

| 否决项 | 理由 |
|---|---|
| 导出 CSV / Excel | 暂无下游 BI 需求，可独立 Step 加 `?format=csv` |
| 时间范围 `since` / `until` 过滤 | offset 分页 + action/actor 已能定位绝大多数场景；时间过滤需配 DatePicker 控件，留候选 Step |
| 实时推送（SSE / WebSocket） | audit 不是高频事件，手动刷新即可；引入 SSE 增加连接管理复杂度 |
| `actor_id` 反查到 `users` 表展示用户名 | 保持 audit 表单表读取的纯粹性；`actor_id` 本身已含 provider 前缀（`github:Melodymll01`）信息量够 |
| Use Case 化 + 单测 use case | 见 D3 |
| 登录端点也落 audit | 单独 Step 处理（候选 025c），与 KB 写操作的副作用语义略有不同（成功登录是否记 audit？匿名是否记？） |

## 5. 验证

### 自动

```powershell
.\.venv\Scripts\python.exe -m pytest -q --ignore=evaluations/ood/eval_ood.py --ignore=tests/smoke_bm25_rrf.py
# => 527 passed (+8 净增)

.\.venv\Scripts\python.exe -m ruff check `
  domain app `
  infra/auth infra/kb infra/risk_profile infra/audit `
  api/v2 config.py main.py `
  tests/api tests/app tests/domain tests/infra tests/fakes
# => All checks passed!
```

### 手测验证矩阵

| 角色 | 侧栏入口可见？ | `/api/v2/audit/logs` 直访 | 翻页 | 过滤 |
|---|---|---|---|---|
| 匿名 | ❌ | 401 | — | — |
| 普通登录 | ❌ | 403 | — | — |
| admin | ✅ + 金色 "admin" 标签 | 200 | 上/下页按钮按 lastCount 自动 disable | action 下拉 + actor_id 输入（300ms debounce） |

## 6. 数据流时序

```
[admin 浏览器]
    │ click "审计日志" 侧栏
    ▼
app.js: switchView("audit") → adminAudit.mount() + refresh()
    │
    ▼
admin-audit.js: audit.list({limit:50, offset:0, action:"", actor_id:""})
    │
    ▼
api.js: fetch GET /api/v2/audit/logs?limit=50&offset=0  (credentials: include)
    │
    ▼
[FastAPI v2]
api/v2/audit.py list_audit_logs (Depends require_admin) ──401/403 守门
    │ admin 通过
    ▼
container.audit_log.list_recent(limit=50, offset=0)
    │
    ▼
SqliteAuditLogRepo: SELECT ... ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?
    │
    ▼ AuditLogListResponse {entries: [...], count: N}
    │
[admin 浏览器]
renderRows(entries) + renderPager() ─── 下一页按 count<50 判到底
```

## 7. 结构对比（Step 022 之后的层级图增量）

Step 022 overview 的 13 Port 表无变化（`AuditLogPort` 仍是第 13 个），但：

- **API 层**：`/v2/audit/logs` 端点签名扩展 `+ offset` 形参
- **前端层**：第一次在 UI 中暴露 `audit-*` 命名空间；侧栏现在是 3 个入口（chat / kb / audit）而非 Step 017 留下的 2 个

未触发 ADR 增量（offset 分页是 ADR-013 之内的"读路径具体化"，不构成新决策；前端 UI 决策属于实现细节）。

## 8. 后续候选

| 编号 | 类型 | 描述 |
|---|---|---|
| 024a | 文档 | `interview_doc/` 五篇对齐 Step 008-023 现状（八股 / 项目经历 / 详解 / 高频追问 / Agent 岗位） |
| 024b | 文档 | 项目根 README 重写门面（架构图 / quick start / 演进路线） |
| 025a | 工程 | 私人 KB `owner_id` 隔离（结合 KbDocumentRepoPort 加 `owner_id` 列） |
| 025b | 工程 | mypy 复活（当前 ~46 错与基线持平，分批清零） |
| 025c | 工程 | 登录端点也落 `AuditLogPort`（auth.login / auth.callback 成功失败均记） |
| 026a | 工程 | audit 时间范围过滤 + 导出 CSV |

**建议节奏**：023（本步 工程）→ 024（文档对齐：interview + README）→ 025（工程择一）。
