# Step 012 — 前端 Copilot UI 改造（vanilla JS + SSE）

## 1. 本步骤目标

把老的 "Dashboard / 知识库 / 智能问答 / 深度研究" 四 Tab 表单式 UI，
改成**单聊天对话界面**：左侧任务列表 + 右侧消息流 + SSE 实时渲染 agent 思考与工具调用。

只接 `/api/v2/*` 新 API，不再调用任何 `/api/ask` `/api/research` 老路径——
Step 010-011 把后端的 `/api/v2` 路由 + main.py 装配做完了，本步骤让它们终于"被人用"。

## 2. 修改文件

### 重命名为 `.legacy.*` 保留参考（git mv，历史可追）

| 老文件 | 新名 | 大小 |
|---|---|---|
| frontend/index.html | frontend/index.legacy.html | 26 KB |
| frontend/app.js | frontend/app.legacy.js | 39 KB |
| frontend/style.css | frontend/style.legacy.css | 44 KB |

### 新增（全部 vanilla JS / 零构建依赖）

| 文件 | 行数 | 说明 |
|---|---|---|
| [frontend/index.html](../../frontend/index.html) | 87 | HTML 骨架：sidebar 任务栏 + 聊天主区 + 输入栏；`<script type="module">` 入口 |
| [frontend/style.css](../../frontend/style.css) | 460 | 全套样式：暗色主题 / 消息气泡 / 思考流 trace / 工具卡片 / 引用块 / 滚动条美化 / 800px 响应式断点 |
| [frontend/api.js](../../frontend/api.js) | 60 | `/api/v2/*` 的 thin REST 客户端，统一抛 `ApiError(status, errorCode, message)` |
| [frontend/sse.js](../../frontend/sse.js) | 80 | `streamChat(body, {onEvent,onError,onDone})`：fetch + ReadableStream + 帧解析 + AbortController |
| [frontend/auth.js](../../frontend/auth.js) | 75 | `ensureSession`（GET /me → 失败时自动 POST /anonymous）+ `startGithubLogin` + `logout` + 用户状态发布订阅 |
| [frontend/chat.js](../../frontend/chat.js) | 250 | 消息渲染主流程：`sendMessage` 起 SSE → 9 类事件 dispatch 到 DOM；`loadTask` 回放历史；`newConversation` 重置 |
| [frontend/tasks.js](../../frontend/tasks.js) | 65 | 左侧任务列表：`refresh` / `setActive` / 删除确认；onSelect 回调 |
| [frontend/app.js](../../frontend/app.js) | 110 | 入口：bootstrap session → health 自检 → tasks 拉取 → UI 绑定（输入框自增高、Enter 发送、用户菜单、欢迎卡建议问） |

### 未改动

- `frontend/marked.min.js`（Markdown 渲染，继续用）
- `main.py`（GET / 仍 `FileResponse(FRONTEND_DIR / "index.html")`，新文件接管）
- 任何后端代码

## 3. 设计决策

| 选择 | 取代方案 | 原因 |
|---|---|---|
| **删干净老 Tab，前端只对接 `/api/v2`** | 保留隐藏入口 | Step 011 已证明老 `/api/*` 在后端仍通；前端不再调用即可。简历价值更高（"完整重构"）；混着用 4 个 Tab 反而是设计债 |
| **vanilla JS + ES module** | Vite + lit-html / Vue 3 | 项目体量没到要框架；零构建工具链；ES module 浏览器原生支持 import 链；可直接以 `python -m http.server` debug |
| **`fetch + ReadableStream`** | `EventSource` | EventSource 只支持 GET；后端 `/api/v2/copilot/chat/stream` 是 POST + JSON。fetch+stream 是 SSE over POST 的标准做法 |
| **按 `\n\n` 切帧 + `:` 开头当心跳** | 引入 sse.js 库 | 60 行就实现了。心跳帧 `: keepalive` 和 [api/v2/sse.py](../../api/v2/sse.py) 完全对齐 |
| **session = cookie 单一真相源**（前端不存 token） | localStorage | XSS 安全 + 后端已经 httponly + samesite=lax；前端只关心 user 对象内存态 |
| **启动期自动匿名兜底** | 强制弹登录框 | 设计文档（[ADR-007](../decisions/ADR-007-github-oauth-with-anonymous.md)）就要求"匿名先用，登录是升级路径" |
| **用 `<script type="module">`** | 编译成单文件 IIFE | 浏览器原生支持；模块边界清晰；调试时每个文件独立可加断点 |
| **task_id 维护在 chat.js 内部** | 全局状态管理库（pinia/redux） | 单实例 + 7 个文件足够；维护成本最低 |
| **不接 typing 流（answer 一次性渲染）** | 字符级 streaming | 后端 `answer` 事件本来就是一次性 emit；agent 思考过程已经分多帧（thought/tool_call/tool_result）展示了"渐进感" |
| **每次 SSE 中断都 abort 上一个 controller** | 排队 / 拒绝并发 | 用户改主意发新问题时旧流要立刻停；AbortController 是浏览器原生 API，无需自己管状态 |
| **无 playwright 自动化测试** | 加 e2e 套件 | 拖慢 CI；UI 改动靠 [tests/api/test_copilot_sse.py](../../tests/api/test_copilot_sse.py) 已覆盖 SSE 契约；视觉/手感留给手测 |

## 4. 核心契约

### 模块依赖图

```
index.html
  └─ <script type="module" src="/static/app.js">
       app.js  ──→ auth.js   ──→ api.js  (fetch wrapper)
                 ↘ chat.js   ──→ sse.js  (ReadableStream parser)
                 ↘ tasks.js  ──→ api.js
                 ↘ api.js    (health.ready)
       (marked.min.js 全局 UMD，chat.js window.marked.parse)
```

### SSE 帧解析协议（和 [api/v2/sse.py](../../api/v2/sse.py) 镜像）

```
event: thought           → renderThought()
event: tool_call         → renderToolCall()    创建工具卡片，状态"运行中…"
event: tool_result       → renderToolResult()  按 tool_name 找对应卡片，附结果 + 改状态 ok/err
event: answer            → renderAnswer()      marked.parse() + 引用列表
event: ask_user          → renderAskUser()
event: citations         → renderCitations()
event: task_created      → 更新 _currentTaskId + 触发任务列表刷新
event: error             → 红色 notice 横幅
: keepalive              → 静默忽略
```

### 鉴权流（auth.js）

```
ensureSession() {
  try { user = await GET /api/v2/auth/me }
  catch { /* 网络错误也尝试匿名 */ }

  if (user.authenticated) return user
  return await POST /api/v2/auth/anonymous   // 自动兜底
}
```

GitHub 登录走标准 OAuth 重定向：`GET /auth/github/login` → `window.location = authorize_url` → 用户授权 → GitHub 回调到后端 `/auth/github/callback` → set cookie → 302 回前端首页。

### 任务列表 ↔ 聊天联动

| 用户动作 | 触发链 |
|---|---|
| 点 ＋ 新建任务 | `setActiveTask(null)` → `chat.newConversation()` → 清空消息区 + 显欢迎卡 |
| 点左侧任务 | `tasks.onSelect(id)` → `setActiveTask(id)` → `chat.loadTask(id)` → GET /tasks/{id} 回放历史 |
| 发送首条消息（new conversation） | SSE `task_created` → `onTaskCreated(taskId, title)` → `tasks.refresh()` + `setActiveTask(taskId)` + 顶栏标题更新 |
| 发送后续消息（已有 task） | SSE 流 → `onTaskUpdated()` → `tasks.refresh()`（更新最近活跃顺序） |

## 5. 与外部服务的关系

- 全部静态资源走 [main.py](../../main.py) 的 `app.mount("/static", StaticFiles(...))`
- 后端冒烟（TestClient）确认 9 个文件均 200：`/`, `/static/{style.css, app.js, auth.js, sse.js, chat.js, tasks.js, api.js, marked.min.js}`
- 浏览器手测（uvicorn :8765）：进入 `/` 后立刻打出 4 个请求 `me → 200`、`anonymous → 201`、`ready → 200`、`tasks → 200`，状态条显示 "就绪 · 4 工具"

## 6. 当前实现范围

✅ 已实现：

- 单聊天主流程：发问 → SSE → 思考流 + 工具卡片 + 答案 + 引用全部可视
- 任务列表：自动刷新 / 点击切换 / 删除确认
- 鉴权：自动匿名兜底 / GitHub 登录跳转 / 退出（退出后再次自动匿名，避免界面卡死）
- 健康自检：`/health/ready` 失败时状态点变红
- 输入区：自动增高 / Enter 发送 / Shift+Enter 换行 / 中断上一条 SSE 后再发新流
- 响应式：< 800px 自动隐藏 sidebar
- 9 个静态资源全部正确装配

❌ 未实现（按规划推迟）：

- **附件上传**（`attachment_doc_ids`）—— 输入框暂未挂文件选择器；后端 `ChatRequest` 已支持，留给 Step 013/014
- **artifacts 面板**（产出物下载）—— 等 Step 014 risk 模块产出实际 artifact 时再做
- **"运行中"取消按钮** —— `_abortCurrent` 内部已存在；UI 上还没暴露给用户
- **流式 markdown 渲染**（边输出边解析）—— 后端 answer 是一次性 emit，差异暂可忽略
- **Toast 提示**（替换 alert/confirm）—— 第一版用浏览器原生对话框，简单可靠
- **playwright e2e** —— 范围之外

## 7. 暂未实现 / TODO

- 后端 `MessageOut` 的 `citations` 字段格式跟前端 `renderCitations` 假设的 `c.source_title / c.snippet` 对齐过；但历史消息回放时如果 sqlite 老 message 有不同 schema，可能渲染空白。等真有数据后做兼容
- 桌面端窄宽度（801~1000px）sidebar 占比 28% 偏大；未来加 collapse 切换
- `chat.js` 的 `escapeHtml` 是手写的；如果未来引 DOMPurify 之类库可换掉
- `marked.parse()` 没设 `breaks: true`，单换行不变 `<br>`——按需调
- 站点 `/docs` 链接（FastAPI 自动 swagger）暂未在 sidebar 暴露入口；如要演示给评审看可加一个

## 8. 测试与验证

```bash
# 后端无回归
pytest -q
# 380 passed, 16 warnings in 48s   （与 Step 011 持平）

# 静态资源烟雾（TestClient 起 main.app）
python -c "
import os
os.environ.setdefault('LLM_PROVIDER','local'); os.environ.setdefault('EMBED_PROVIDER','local')
from fastapi.testclient import TestClient; import main
with TestClient(main.app) as c:
    for p in ['/', '/static/style.css', '/static/app.js', '/static/auth.js',
              '/static/sse.js', '/static/chat.js', '/static/tasks.js',
              '/static/api.js', '/static/marked.min.js']:
        r = c.get(p); print(r.status_code, p)
"
# 全部 200，新 index.html 4630B、style.css 13937B、5 个 JS 模块共 ~21KB
```

### 浏览器手测（uvicorn :8765）

打开 `http://127.0.0.1:8765/` 后日志：

```
GET /api/v2/auth/me              → 200 (2ms)
POST /api/v2/auth/anonymous      → 201 (7ms)
GET /api/v2/health/ready          → 200 (1ms)
GET /api/v2/tasks                 → 200 (9ms)
```

UI：欢迎卡 + 4 张建议卡 2×2 + 输入框 + 顶部状态条"就绪 · 4 工具"，匿名头像出现在左下角。
