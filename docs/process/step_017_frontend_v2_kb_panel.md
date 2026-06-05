# Step 017 — v2 前端知识库管理面板（admin-only）

## 1. 本步骤目标

补齐 Step 016c 收尾时刻意推迟的"v2 前端 KB UI 独立 PR"。把已就绪的 6 个
`/api/v2/documents/*` 端点接通到 admin 用户的浏览器侧边栏：登录后侧边出现
"📚 知识库"入口，点进去就能看到文档列表、统计、上传文件、采集网页、删除四件套。

为后续做：

- 让 admin 用户不必再用 `curl` 直接打 `/api/v2/documents/*`，完成 Step 016
  整体重构方案 B 在 UX 层的最后一公里
- 给后续"权限模型再细化"（Step 019 读公开/写受限）提供 UI 抓手——
  侧栏入口可见性 + 操作区可见性两层 toggle 在这里一次到位
- 验证 v2 前端单聊天框架（Step 012）能否承载第二个"主视图"：
  本步骤通过 grid 同栏 toggle `.hidden` 的方式扩出 `#chat-pane` / `#kb-pane`
  两个 `<main>`，证明无需引入路由库即可承载多视图

## 2. 修改文件

| 路径 | 说明 |
|---|---|
| `frontend/kb.js`（新建，~165 行） | KB 视图所有交互：`mount()` 幂等绑定 + `refresh()` 拼 `Promise.all([list, stats])` 双请求；文件上传走 multipart（隐藏 `<input type=file>` + 按钮触发 click）；网页采集走 JSON POST + 前端再做一次轻量 URL 校验；删除走 `confirm` 二次确认；`setStatus(state, text)` 5 态状态条（idle/loading/ok/warn/err）；操作完成自动 `refresh()` |
| `frontend/api.js` | 新增 `documents` 命名空间 6 方法：`list / stats / get / remove / ingestWeb / ingestFile`。`ingestFile` 不走通用 `request()`（multipart 无法 JSON 序列化），单独走 `fetch + FormData` + `BASE + "/documents/file?category=..."`，复用 `ApiError` 错误契约 |
| `frontend/index.html` | sidebar 加 `<nav class="side-nav">`（💬 对话 / 📚 知识库 admin-only）；`#nav-kb` 默认 `hidden` + class `hidden`，登录后由 `applyAdminGate` 控制可见性。`.app` grid 新增第二个 `<main class="kb-pane hidden" id="kb-pane">`，与 `#chat-pane` 共占第 2 列；切视图通过 toggle `.hidden`。KB 视图结构：header（标题 + 双指标 + 刷新）+ 操作区（上传文件卡 / 采集网页卡）+ 状态条 + 文档表格 |
| `frontend/app.js` | 顶部模块状态 `_currentView` + `switchView(v)`：toggle 两个 main 的 `.hidden`、高亮主导航 `.active`、首次切到 KB 调 `kb.mount() + kb.refresh()`。`onUserChange` 末尾调 `applyAdminGate(user.is_admin)`：admin → 显示 `#nav-kb`；非 admin → 隐藏，且若停在 KB 视图则踢回 chat。`bindUI()` 加 `.side-nav-item` 点击事件 → `switchView`；新建任务按钮：若当前在 KB 视图则先切回 chat 再 `newConversation` |
| `frontend/style.css`（+231 行） | `.side-nav` / `.side-nav-item` / `.side-nav-tag`：admin 标签淡金色徽标。`.kb-pane` / `.chat-pane` 显式 `grid-column:2 grid-row:1` 共占网格第二列。KB header / stats / 操作卡 / 状态条 / 表格全套暗色风。表格 sticky thead；分类用 pill；删除按钮红色。`@media (max-width: 900px)` 操作卡纵向堆叠 |

## 3. 设计决策

### D1：KB 视图与 chat 视图**共占网格第二列 + toggle .hidden**

候选方案：

- A：引入 history API + 简易路由（`/chat` `/kb`）+ pushState
- B（采用）：两个 `<main>` 共占 `grid-column:2 grid-row:1`，`switchView` 仅 toggle `.hidden`
- C：把 KB 做成 modal 弹窗

理由：
1. 业务上只有两个一级视图，引入路由库（哪怕只用 history API）会让
   `app.js` 多一层抽象，跟 Step 012 "vanilla JS + 0 构建依赖"基调冲突
2. modal 不适合做表格 + 表单的"工作区"语义
3. grid 同栏 toggle 让两 view 共享同一份 sidebar / 同一份 user state，
   切换毫秒级，无需重渲染 sidebar

### D2：`kb.js` 完全独立模块（不复用 chat.js 任何函数）

KB 跟 chat 在概念上正交（一个是任务/SSE 流，一个是 CRUD 表格），
没有理由复用同一份 DOM 助手。`kb.js` 只 `import { documents } from "./api.js"`，
`mount()` 幂等（`_mounted` 标志位），不会被 `switchView` 多次切换重复绑定事件。

### D3：上传 / 采集 / 删除三个写操作的前端校验深度

- **文件上传**：只做后缀粗校验（隐藏 input 的 `accept=".pdf,.txt,.docx"` 触发系统选择器），
  大小 / 真实类型校验全交给后端 413 / 400 兜底
- **网页采集**：用 `<input type="url" required>` 做浏览器原生校验，
  实际 http/https 协议白名单仍走 `WebIngestRequest` validator
- **删除**：单层 `window.confirm()`；不做"输入文件名再次确认"等多步交互

→ "前端做体验，后端做真理"，避免规则两边漂

### D4：操作完成的反馈走"状态条 + 自动 refresh"，不弹 toast

```js
setStatus("loading", "上传中…");
await documents.ingestFile(...);
setStatus("ok", `✓ 入库 ${r.chunk_count} 条 chunk`);
await refresh();
```

理由：toast 组件需要单独维护生命周期 / 动画 / 堆叠层级；
状态条+表格自刷新已经足够传达"动作成功 + 副作用已可见"，UX 上更克制。

## 4. 核心契约 / 接口

`frontend/api.js` 新增 6 个方法：

```js
documents.list()                 // GET    /api/v2/documents
documents.stats()                // GET    /api/v2/documents/stats
documents.get(sourceName)        // GET    /api/v2/documents/{src}
documents.remove(sourceName)     // DELETE /api/v2/documents/{src}
documents.ingestWeb({url, category})  // POST /api/v2/documents/web   (JSON)
documents.ingestFile(file, category)  // POST /api/v2/documents/file (multipart)
```

`kb.js` 对外只导出两个函数：

```js
export function mount()    // 首次进入 KB 视图调用；幂等
export async function refresh()  // 并发拉 list + stats，渲染
```

## 5. 与外部服务的关系

- **零变化**。前端纯静态资源由 `main.py` 的 `StaticFiles` 服务，
  与后端跑同一个进程；不引入新外部依赖（没有 npm，没有构建链）
- 6 个 KB 请求都走 cookie session（`credentials: "include"` 通过 `api.js`
  统一注入），与 Step 010 PR-6 cookie 会话方案对齐

## 6. 当前实现范围

### 已实现

- [x] `frontend/kb.js` 新建，6 个交互链路全通
- [x] `frontend/api.js` `documents` 命名空间 6 方法
- [x] `frontend/index.html` 侧栏导航 + KB 视图骨架
- [x] `frontend/app.js` `_currentView` 状态 + `switchView` + `applyAdminGate`
- [x] `frontend/style.css` ~222 行 KB 面板样式
- [x] TestClient 静态资源烟雾：`/static/{kb,app,api,style}.*` 全部 200
- [x] `pytest -q` 全绿 479 passed（前端改动不影响后端用例）

### 未实现（按设计跳过）

- 上传进度条 / 大文件分片：50 MB 上限内一次性传完，进度条价值低
- 文档详情侧抽屉（点行展开看 chunk 预览）：超出本步骤"接通 6 端点"目标
- 知识库搜索 / 分页：当前 list 端点不分页，待 KB 文档数超过 100 再说

## 7. 暂未实现 / TODO

- **权限模型再细化**：本步骤侧栏入口 admin-only 是粗粒度；
  普通登录用户其实有"看 Agent 在引用什么资料"的合理诉求，
  需要后续把 GET 端点对所有登录用户开放（→ 见 Step 019）
- **响应式适配 ≤900px**：操作卡已能纵向堆叠，但表格列宽未做精细化收缩
- **i18n**：当前全部中文硬编码

## 8. 测试与验证

```powershell
cd d:\py\RagDataOut

# 全量回归
.venv\Scripts\python.exe -m pytest -q
# → 479 passed, 16 warnings

# 静态资源 200 烟雾
.venv\Scripts\python.exe -c "from fastapi.testclient import TestClient; from main import app; c = TestClient(app); print([(p, c.get(p).status_code) for p in ['/static/kb.js','/static/app.js','/static/api.js','/static/style.css','/static/index.html']])"
# → 全部 200

# 浏览器手测（uvicorn :8765）
# 1. 匿名访问 /：侧栏只有"对话"，无 KB
# 2. GitHub admin 登录后回到 /：侧栏出现"📚 知识库 admin"
# 3. 点 KB → 表格展示 0 文档，点上传选 a.pdf 入库成功，自动 refresh 看到一行
# 4. 点删除 → confirm → 表格刷新空
# 5. 退出登录 → 自动从 KB 踢回 chat
```

变更行数：5 文件，+562 / -1（commit `438deae`）。
