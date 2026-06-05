# Step 019 — 知识库权限拆分：读端点公开 / 写端点 admin-only

## 1. 本步骤目标

把 Step 016c 收尾时拍下的"6 个 KB 端点全部 admin-only"决定收口：
**读端点（GET list / stats / detail）下放到任意登录用户**，
写端点（POST file / web、DELETE）保持 admin-only。

这是用户在体验 Step 017 KB 面板后提出的合理诉求："其他登录用户应该
能看到知识库的语料元数据，不能改而已"——这是公共合规知识库的标准
权限模型，而不是私人后台。

为后续做：

- 让非 admin 用户也能直观看到 Agent 在引用什么资料，提升回答可信度
- 给 admin 标签真实意义（之前 admin 标志只决定能否看到 KB 入口本身；
  现在 admin 决定能否看到"上传 / 删除"动作）
- 为后续可能的"用户私人知识库"分级权限模型留下"login-only / admin-only"
  二级框架

## 2. 修改文件

| 路径 | 说明 |
|---|---|
| `api/v2/documents.py` | GET 三端点 `_admin_id: str = Depends(require_admin)` → `_owner_id: str = Depends(require_owner)`；导入新增 `make_require_owner`；模块 docstring 与 `build_documents_routes` docstring 同步成"读 login-only / 写 admin-only" |
| `frontend/index.html` | 左侧 `#nav-kb` 从"admin 可见"改"登录可见"；admin 标签 `.side-nav-tag#nav-kb-tag` 单独控制；KB 标题去"管理"二字；新增 `#kb-readonly-banner` 提示非 admin 当前为只读模式 |
| `frontend/app.js` | `switchView` 守门改成"未登录禁入 KB"（admin 不再必需）；`applyAdminGate(isAdmin)` → `applyKbGate(isLoggedIn, isAdmin)`：分别控制导航入口可见性 / admin 标签 / `#kb-actions` 上传区 / 只读 banner 可见性；未登录踢回 chat |
| `frontend/kb.js` | `import { getUser } from "./auth.js"`；`renderList` 读 `getUser()?.is_admin`：非 admin 不渲染删除按钮，操作列显示 `—`；空 KB 提示文案按角色分（admin "请上传文件" / 普通用户 "需管理员入库"） |
| `frontend/style.css` | 新增 `.kb-readonly-banner`（左侧黄色细边 + 暗色背景 + 12.5px）、`.kb-action-muted`（操作列占位"—"的弱化样式） |
| `tests/api/test_documents.py` | `TestAuthGating` 整类改写：原 `test_list_non_admin_forbidden`（403）→ `test_list_non_admin_allowed`（200）；新增 `test_stats_non_admin_allowed` / `test_delete_non_admin_forbidden` / `test_ingest_file_non_admin_forbidden` / `test_ingest_web_non_admin_forbidden`；模块 docstring 同步 |

## 3. 设计决策

### D1：权限粒度选择 —— 「login-only 读 / admin-only 写」二级模型

候选方案：

- A：保持"全 admin-only"（现状）
- B（采用）：读 login-only / 写 admin-only
- C：读完全公开（无需登录）/ 写 admin-only
- D：按 owner_id 隔离的私人 KB

理由：
1. KB 内容是**业务知识库**（合规法规 / 政策 / 指南），不属于私人数据
   → 排除 D
2. 但 KB 是 admin 维护的语料库 → 完全匿名公开会被爬 → 排除 C
3. login-only 读既能"让 Agent 用户看到引用源头"，又能用 cookie session
   做基础流控 / 审计追踪
4. 写操作可能扣 LLM token（embedder）、可能引入垃圾源，必须管控

### D2：复用既有 `make_require_owner`，不引入新的 dep

`api/v2/deps.py` Step 010 就有 `make_require_owner`（401 if no session）。
原本只在 `tasks.py` / `copilot.py` 用。直接复用而不是写个 `require_login`
别名——保持依赖层一个名字一个语义。

### D3：前端"双层 gate"而不是"在每个写按钮上判 admin"

```js
function applyKbGate(isLoggedIn, isAdmin) {
  navKb.hidden = !isLoggedIn;
  navKbTag.hidden = !isAdmin;            // admin 标签
  kbActions.classList.toggle("hidden", !isAdmin);  // 整个上传区
  banner.classList.toggle("hidden", isAdmin || !isLoggedIn);
  if (!isLoggedIn && _currentView === "kb") switchView("chat");
}
```

**两层 gate** = 入口可见性 + 操作区可见性。删除按钮在表格行渲染时判，
不放在 `applyKbGate` 里——因为 `renderList` 每次 refresh 都重渲染，
直接读 `getUser()?.is_admin` 即可。

→ 全局状态变化（登录/登出）走 `onUserChange` → `applyKbGate`；
   表格内动态内容走 `renderList` 读最新 user。两条路径各管一半，不交叉污染。

### D4：UX 文案 —— 非 admin 不要"看起来像缺失功能"

- 顶部 banner：`🔒 当前以只读模式查看知识库；上传 / 删除需管理员权限。`
  （直接告知原因，避免用户去找"上传"按钮在哪）
- 操作列 `—`（而不是 `[disabled] 删除` 灰色按钮）：disabled 状态在视觉上
  仍然吸引点击，不如直接占位
- 空 KB 文案分角色：admin 看到 "请上传文件或采集网页"；普通用户看到
  "需管理员入库后才有内容可查"

### D5：测试改写而不是新增类

`TestAuthGating` 整类的语义就是"权限模型"，模型变了应该原地改而不是
开新类（开新类会让测试列表里出现两个版本"哪个是真"的歧义）。
保留 `test_list_requires_auth` 等"未登录 401"的部分；改写"非 admin"
那部分：list/stats → 改成 allowed；delete/ingest_* → 维持 forbidden。

## 4. 核心契约 / 接口

权限矩阵：

| 端点 | 未登录 | 匿名登录 | 普通 GitHub 登录 | admin |
|---|---|---|---|---|
| `GET /api/v2/documents` | 401 | ✅ | ✅ | ✅ |
| `GET /api/v2/documents/stats` | 401 | ✅ | ✅ | ✅ |
| `GET /api/v2/documents/{src}` | 401 | ✅ | ✅ | ✅ |
| `POST /api/v2/documents/file` | 401 | 403 | 403 | ✅ |
| `POST /api/v2/documents/web` | 401 | 403 | 403 | ✅ |
| `DELETE /api/v2/documents/{src}` | 401 | 403 | 403 | ✅ |

非 admin 失败响应保持原 `ADMIN_REQUIRED` error_code 不变；前端继续按
`error_code` 处理（不会变成"未知错误"）。

## 5. 与外部服务的关系

- **零变化**。仅 API 层 dep 切换；业务编排仍走 `container.kb_management`。

## 6. 当前实现范围

### 已实现

- [x] `api/v2/documents.py` 3 读端点切到 `require_owner`
- [x] 3 写端点保持 `require_admin`
- [x] 前端 KB 入口对所有登录用户可见（admin 加金色标签）
- [x] 前端 KB 视图按 admin 隐藏 / 显示上传区 + 删除按钮 + 只读 banner
- [x] `pytest -q` 483 passed（+4 新增非 admin 用例 / -1 失效 + 1 改写）
- [x] `test_documents.py` 模块 docstring 同步新权限模型

### 未实现（按设计跳过）

- 端点级 audit log：当前只有 `main.py` 的 request 日志，没有专门标记
  "admin 操作 / 非 admin 读取"分桶；走通用观测体系即可
- 速率限制按角色分级（admin 高 / 非 admin 低）：当前流控 Step 011 是
  全局 IP-based，分级超出本步骤范围
- 文档详情侧抽屉（按 Step 017 TODO）

## 7. 暂未实现 / TODO

- 私人知识库（每个 user 自己的 owner_id 隔离 KB）：需在 `KbChunk`
  domain 加 `owner_id` + repo 层加 owner 过滤 + use case 端口扩展，
  与本步骤"公共 KB 二级权限"是正交主题
- 文档级 ACL（部分文档仅 admin 可见）：当前所有读端点是"全或无"

## 8. 测试与验证

```powershell
cd d:\py\RagDataOut

# 仅 documents 路由
.venv\Scripts\python.exe -m pytest tests/api/test_documents.py -q
# → 29 passed in 3.23s（原 25，+4 新增 / -1 原 forbidden / +1 改写 allowed）

# 全量
.venv\Scripts\python.exe -m pytest -q --ignore=tests/eval_ood.py --ignore=tests/smoke_bm25_rrf.py
# → 483 passed, 16 warnings

# 浏览器手测
# 1. 匿名登录 → 侧栏出现"📚 知识库"（无 admin 标签）→ 点进去看到列表 + 顶部黄色只读 banner + 操作区不见
# 2. admin 登录 → 侧栏出现"📚 知识库 admin"（金标签）→ 点进去看到完整操作区 + 表格行有红色删除按钮
# 3. 未登录 → 侧栏完全无 KB 入口
```

变更行数：6 文件，+119 / -32（commit `4ad14ad`）。
