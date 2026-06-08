# S-031b — 记忆与隐私设置（前端 UI）

> 记忆系统第六步（用户控制面 · 前端）。在 S-031a 把每用户记忆双开关 + 注入门控 + 管理读接口
> 落到后端之后，本步补上对应的前端界面：账户菜单新增「⚙ 记忆与隐私」入口，打开一个模态，
> 内含两个 iOS 风开关（参考保存的记忆 / 参考会话上下文）、已保存记忆的只读管理面板
> （用户画像 + 长期事实），以及行使「被遗忘权」的两个清除按钮。纯静态前端，无构建依赖。

## 1. 本步骤目标

把 S-031a 的后端能力暴露给终端用户，让「记忆透明、可控、可删」在 UI 上闭环：

- **双开关**：模态里两个 toggle，乐观切换（先翻 UI 再 `PUT`），失败回滚 + 提示；
  - `use_saved_memory`（参考保存的记忆）；`reference_history`（参考会话上下文）；
- **管理面板（只读）**：展示「系统记住了你什么」——
  - 用户画像：`GET /memory/profile` 的 `facts` 字典逐条 `key: value`；
  - 长期事实：`GET /memory/facts` 的清单 + `count / cap` 容量徽标；
- **被遗忘权**：两个危险按钮，二次 `confirm` 后调 `POST /memory/forget`——
  - 「清空记忆」`scope="memory"`（保留历史对话，仅清派生记忆）；
  - 「清空全部（含对话）」`scope="all"`（连带删历史任务）；清完通知 app 刷新任务列表 + 重置当前对话；
- **登录门控**：仅「真实登录」用户（`provider !== "anonymous"`）可见入口；匿名访客不显示，退登/掉匿名时自动关闭已打开的模态。后端记忆禁用时画像/事实为空，UI 显示空态而非报错。

## 2. 修改 / 新增文件

| 文件 | 说明 |
|---|---|
| `frontend/api.js` | **新增** `memory` 客户端：`profile` / `getSettings` / `updateSettings` / `facts` / `forget` |
| `frontend/settings.js` | **新增**：模态控制器——`mount`（一次性绑事件）/ `open` / `close`、双开关乐观切换、画像+事实加载、清除二次确认；`onMemoryCleared` 供 app 订阅 |
| `frontend/index.html` | 账户菜单加「⚙ 记忆与隐私」项；新增模态骨架（双开关 + 管理面板 + 危险区 + 状态条） |
| `frontend/app.js` | `import * as settings`；绑菜单项 → `settings.open()`；订阅 `onMemoryCleared`（`scope="all"` 时刷新任务 + 重置对话） |
| `frontend/style.css` | **新增** `.modal-overlay/.modal`、iOS 风 `.switch`、`.memory-list/.memory-item`、`.btn-danger(-soft)` 等样式 |

## 3. 设计决策

- **D1（独立模态，不复用视图切换）**：设置是「跨视图的账户级面板」，不属于 chat/kb/audit 三视图之一，
  因此用覆盖式 `.modal-overlay` 而非 `switchView`，点遮罩 / `✕` / `Esc` 关闭，不污染主导航状态。
- **D2（乐观切换 + 回滚）**：开关 `change` 时先信任 UI、再发 `PUT`；失败把 checkbox 翻回去并红字提示。
  体感即时，错误可见可回滚，避免「点了没反应」的等待感。
- **D3（部分更新只传一字段）**：每个 toggle 只 `PUT` 自己那一个字段（`{[field]: value}`），
  另一字段后端保持原值（S-031a `UpdateMemorySettingsRequest` 的 `None` 语义），避免并发覆盖。
- **D4（被遗忘权强二次确认）**：清除不可逆，用原生 `window.confirm` 拦一道；
  两个 scope 文案分别讲清「保留 / 连带删对话」的区别，避免误删历史。
- **D5（清空全部联动 app）**：`scope="all"` 会删历史任务，settings.js 不直接碰任务模块，
  而是 `emitCleared("all")` 让 `app.js` 统一 `setActiveTask(null)+newConversation()+refreshTasks()`，保持单向数据流。
- **D6（仅真实登录可用）**：记忆是长期个人化数据，匿名会话用完即弃，暴露记忆设置意义不大且易误解；
  故入口仅对 `provider !== "anonymous"` 的真实登录用户显示，`app.js` 的 `applyMemoryGate` 在 `onUserChange` 时切换显隐，
  退登/掉回匿名时自动关闭模态。点击处另有防御性二次校验。
- **D7（诚实空态）**：画像/事实为空时显示引导文案（"多聊几句会自动总结…"），
  而非空白或假数据；容量徽标 `N / cap` 让用户对「还能记多少」有预期。
- **D8（零构建依赖）**：沿用项目既有的 vanilla ES module + `:root` CSS 变量约定，
  不引入框架/打包器；新样式全部走现有设计 token（暗色皮肤一致）。

## 4. 配置项

无新增配置。容量上限 `cap` 由后端 `GET /memory/facts` 返回（`memory_fact_cap_per_owner`，默认 500），前端只渲染不写死。

## 5. 接口对接表

| UI 动作 | 调用 | 说明 |
|---|---|---|
| 打开模态 | `GET /memory/settings` + `GET /memory/profile` + `GET /memory/facts` | 并发拉取，填开关 + 画像 + 事实 |
| 翻开关 | `PUT /memory/settings` `{字段}` | 部分更新，回填后端权威值 |
| 点刷新 ↻ | `GET /memory/profile` + `GET /memory/facts` | 重载管理面板 |
| 清空记忆 | `POST /memory/forget {scope:"memory"}` | 二次确认；保留对话 |
| 清空全部 | `POST /memory/forget {scope:"all"}` | 二次确认；联动刷新任务 + 重置对话 |

## 6. 验证结果

浏览器实测（`http://127.0.0.1:8765`，匿名会话）：

- 账户菜单出现「⚙ 记忆与隐私」，点击打开模态；
- 模态渲染正常：两开关默认 ON、用户画像 `0`、长期事实 `0 / 500`、危险区两按钮，暗色皮肤一致；
- 翻「参考会话上下文」为关 → 出现「已保存」；`GET /memory/settings` 实测返回 `reference_history:false`（持久化生效）；翻回 ON 再次「已保存」；
- `✕` / 遮罩 / `Esc` 关闭：`aria-hidden` 与 `.hidden` 同步切换，无残留；
- 无控制台报错。

后端测试不受影响（本步纯前端，未改 Python）：S-031a 基线 **771 passed / 1 skipped**。

## 7. 不在本步范围

- 单条事实 / 单条画像删除（推迟 v1.1，需后端 DELETE 接口）；
- 开关状态在主聊天界面的可视化提示（仅在设置内可见）；
- 跨会话历史召回的前端呈现（后端能力尚未接线）。
