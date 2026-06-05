# Step 014 — Frontend 三业务模式 Tab UI（ModeSelector + 模式标签 + 风险画像接口位）

> 对应 commit [`8d7170d`](https://github.com/Melodymll01/riskpilot-cross-border-data-agent/commit/8d7170d)
> 标题：`feat(frontend): 三业务模式 Tab UI + 模式标签 + 风险画像接口位`

## 1. 本步骤目标

把 Step 013 在后端落下来的 `Task.mode = "qa" | "research" | "profile"` 三选一暴露给用户：
顶部一条 3-Tab 模式条 → 切 Tab 改 placeholder/欢迎卡/建议问 → 发问时只在新建 task 那一帧把 mode 塞进 payload。

不新增任何路由或 use case；只是 Step 012 单聊天 UI 的纯前端扩展。

## 2. 修改文件

| 文件 | +/- | 关键改动 |
|---|---|---|
| [frontend/index.html](../../frontend/index.html) | +39 / -... | `chat-header` 拆成 `column`：上一行 `<nav class="mode-tabs" role="tablist">` 三个 `<button class="mode-tab" data-mode="qa\|research\|profile">`，下一行原有 `chat-title + actions`；遵循 ARIA `role="tab" / aria-selected` |
| [frontend/style.css](../../frontend/style.css) | +92 | `.mode-tabs / .mode-tab / .mode-tab.active`（active 用品牌色 underline + 浅背景）；`.task-mode-badge[data-mode="research"]` 琥珀 / `[data-mode="profile"]` 紫色；`.chat-title__badge` 行内徽标样式 |
| [frontend/chat.js](../../frontend/chat.js) | +41 | 新增模块状态 `_currentMode = "qa"` + 三个回调 `_onModeChanged / _onConversationReset`；导出 `getCurrentMode / setCurrentMode / onModeChanged / onConversationReset`；`sendMessage` 仅在 `_currentTaskId === null` 时把 `mode` 写进 payload；`loadTask` 从历史 task 取回 `task.mode` 反向 `setCurrentMode` |
| [frontend/app.js](../../frontend/app.js) | +156 | 新增 `MODE_LABELS` / `MODE_PRESETS`（每 mode：placeholder + welcome.title + welcome.description + 4 个建议问）；`applyMode(mode)` 统一刷高亮 + chat-title badge + placeholder + welcome；Tab 点击 → 若有进行中任务自动 `newConversation()`（三模式不共享上下文）；订阅 `onModeChanged` / `onConversationReset` |
| [frontend/tasks.js](../../frontend/tasks.js) | +11 | 任务条目尾部追加 `<span class="task-mode-badge" data-mode="...">研究 / 画像</span>`；`qa` 不显示徽标避免视觉噪音 |
| [frontend/auth.js](../../frontend/auth.js) | +6 / -6 | `displayLabels` 小幅调整（unrelated 但与本 commit 一起进） |

## 3. 设计决策

| 选择 | 取代方案 | 原因 |
|---|---|---|
| **顶部 3-Tab，不在 sidebar 里** | sidebar 加 `<select>` / 单独菜单页 | Tab 是单聊天主区的"模式开关"，与 sidebar 的"任务列表"职责正交；评审/演示一眼看到三种业务形态 |
| **mode 仅在新建 task 时写进 payload** | 每条消息都带 mode | 后端契约（[Step 013](step_013_admin_modes_risk_profile_port.md) §4）规定首条消息确定 mode，之后由 task 服从既有 mode；前端少传一个字段、避免历史 task 切 Tab 时被覆盖 |
| **切 Tab 时若有进行中任务自动 `newConversation()`** | 提示用户"切 Tab 会丢失对话" | 三种业务形态的提示词、agent 路径、欢迎卡完全不同；强行复用同一 task 的历史会让 agent 困惑（例如把 profile 的命题塞给 qa 的 ReAct） |
| **`loadTask` 反向同步 mode** | 不同步（永远以 UI 为准） | 用户从 sidebar 点了一个老 task，UI 应反映那个 task 的 mode；否则 placeholder 与历史不一致 |
| **`MODE_PRESETS.profile.placeholder` 强调"一句话场景或目标命题，不是表单"** | 表单引导（多输入框） | 与 `schema-evidence-risk-profiling` 仓库定义的 sample-level 输入对齐：`target` 是自然语言命题，不是结构化属性；前端不要做"过早结构化" |
| **profile Tab 标题写"接口预留 · evidence-state 模型训练中"** | 隐藏 / 写"敬请期待" | 评审能看到接口设计已落地、占位是有意为之；同时坦诚告知用户"现在点了不会有真实输出" |
| **task badge 只显示 research / profile，不显示 qa** | 三个都显示 | qa 是默认模式，占任务列表 80%+；徽标只在"非默认"时出现可显著降低视觉噪音 |
| **mode 状态放在 chat.js 模块作用域** | pinia / 全局 store | 单实例 SPA + 一个 mode 字段，模块作用域 + 回调订阅就够；避免引入状态管理库 |
| **`applyMode` 是单一同步函数** | 三个独立函数（setTab / setPlaceholder / setWelcome） | 一致性：所有 mode 相关 UI 一次刷新到位；调用点（Tab 点击 / loadTask 反同步 / 启动 bootstrap）只关心"目标 mode" |
| **未引入 React/Vue 等框架** | 引入框架做 Tab | Step 012 已立场是 vanilla + 零构建；Tab 的状态 + DOM 操作完全在原能力范围内 |
| **`MODE_PRESETS.profile.suggestions` 写成"命题句式"** | 疑问句 | 与 profile 模式的 evidence-state 评估语义对齐："…是否需安全评估" → 给模型一个明确目标命题 |

## 4. 核心契约 / 接口

### chat.js 对外 API（mode 部分新增）

```javascript
// 模式状态
export function getCurrentMode(): "qa" | "research" | "profile";
export function setCurrentMode(mode): boolean;  // 返回是否真的切换了

// 订阅
export function onModeChanged(fn);          // mode 切换时（含 loadTask 反向同步）
export function onConversationReset(fn);    // newConversation 后传当前 mode 让 app 重画 welcome

// sendMessage 内部行为变化（不破坏签名）：
//   if (_currentTaskId === null) payload.mode = _currentMode;
```

### Tab DOM 契约

```html
<nav class="mode-tabs" role="tablist">
  <button class="mode-tab active" role="tab" data-mode="qa"
          aria-selected="true">💬 知识问答</button>
  <button class="mode-tab" role="tab" data-mode="research"
          aria-selected="false">🔬 深度研究</button>
  <button class="mode-tab" role="tab" data-mode="profile"
          aria-selected="false">📊 风险画像</button>
</nav>
```

`applyMode(mode)` 维护 `aria-selected` 与 `.active` class；点击仅依赖 `data-mode` 属性。

### 联动流程

| 触发 | 链路 |
|---|---|
| 启动 bootstrap | `applyMode(getCurrentMode())` 初始化 UI |
| 用户点 Tab | event → `setCurrentMode(m)` → 若有 task 则 `newConversation()` → `onConversationReset(m)` 让 app 重画 welcome → `applyMode(m)` 同步 UI |
| 用户从 sidebar 点老 task | `loadTask(id)` → fetch `/tasks/{id}` 拿 `task.mode` → `setCurrentMode(task.mode)` → `onModeChanged(m)` → `applyMode(m)` |
| 发送首条消息 | `sendMessage()` → `payload = { user_message, mode: _currentMode }` → SSE → 后端 create_task with mode → `task_created` 事件 → 任务列表 refresh 显示徽标 |

## 5. 与外部服务的关系

- **后端 `/api/v2/copilot/chat/stream`** —— 接收新增的 `mode` 字段（来自 [Step 013](step_013_admin_modes_risk_profile_port.md) `ChatRequest.mode`）；如果 mode == "profile"，目前后端仍然走 agent（[Step 015](step_015_profile_mode_wiring.md) 修正这一点）
- **后端 `/api/v2/tasks`** —— `TaskOut.mode` 已在 Step 013 暴露；前端任务列表渲染徽标依赖此字段
- **`marked.min.js`** —— 不变，继续渲染 answer markdown
- 静态资源：[main.py](../../main.py) 的 `app.mount("/static", ...)` 不需要改

## 6. 当前实现范围

✅ 已实现：

- 顶部 3-Tab UI（ARIA 合规：tablist / tab / aria-selected）
- mode 状态管理（chat.js 模块作用域 + 回调订阅）
- 三模式 placeholder / 欢迎卡 / 建议问差异化（MODE_PRESETS）
- 切 Tab 自动新建对话（避免上下文混淆）
- 历史 task 反向同步 mode（loadTask 时）
- 任务列表 mode 徽标（研究琥珀 / 画像紫色 / qa 隐藏）
- profile Tab 标题与文案明确"接口预留 · 训练中"

❌ 未实现（按规划推迟）：

- **profile mode 真分流到 RiskProfilePort** —— Step 015 闭环
- **research mode 真分流到 agentic_rag + report_generator** —— Step 017+（research 当前与 qa 共用 agent 路径）
- **管理员 KB 管理入口** —— Step 016（依赖 Step 013 admin baseline）
- **附件上传按钮** —— `attachment_doc_ids` 后端 ChatRequest 已支持，UI 仍待 Step 018+
- **Tab 切换的过渡动画** —— 当前是瞬间切换，无 CSS transition
- **profile mode 表单变体** —— 故意不做（见 §3 设计决策）

## 7. 暂未实现 / TODO

- `MODE_PRESETS` 写死在 app.js；如果未来 mode 数量增长（如 audit / compliance_check）可拆 `frontend/modes.config.js`
- profile Tab 的"训练中"banner 是 chat-title badge 的形式；考虑后续在欢迎卡里加更醒目的"模型未上线"卡片
- 切 Tab 时 `newConversation()` 不可撤销；如果用户误触会丢上下文，可加 5 秒 toast undo（评审权衡：演示流程不需要）
- 任务列表徽标颜色目前 hardcode 在 style.css；未来如果加颜色主题切换（dark / light）需统一到 CSS variable
- 历史 task 中 `task.mode` 为 `null/undefined` 的兼容（旧 DB 升级前创建的 task）—— `tasks.js` 已隐式归一为不显示徽标，但 `applyMode(undefined)` 会落到 qa 默认值，逻辑上是安全的

## 8. 测试与验证

```bash
# 后端无回归（前端改动不触发后端用例）
pytest -q
# 409 passed  （与 Step 013 持平）
```

### 浏览器手测（uvicorn :8765）

```
进入 /：
- 顶部出现三个 Tab，知识问答 active（underline + 浅背景）
- placeholder = "问点合规问题…  (Enter 发送，Shift+Enter 换行)"
- 欢迎卡：标题"欢迎使用 RiskPilot"+ 4 个 qa 建议问

点 🔬 深度研究：
- Tab active 切换；placeholder 改为"描述你要研究的议题，Agent 会做多轮检索 + 长报告…"
- 欢迎卡换 research 标题/描述/4 个建议问

点 📊 风险画像：
- chat-title 出现 badge "接口预留 · evidence-state 模型训练中"
- placeholder = "输入一句话的场景描述或目标命题，例如…"
- 欢迎卡 4 个建议问改写成命题句式

发任意问题（profile mode）：
- network 看到 POST /api/v2/copilot/chat/stream，body 含 mode: "profile"
- 后端走 agent（仍未分流到 RiskProfilePort，由 Step 015 修正）
- 任务列表新条目尾部出现 "画像" 紫色徽标

切回 💬 知识问答：
- 自动 newConversation()，欢迎卡重新出现，task_id 清空
```

### CSS / DOM 抽样

```bash
# DevTools Console
document.querySelectorAll(".mode-tab").length
// 3
document.querySelector(".mode-tab.active").dataset.mode
// "qa" | "research" | "profile"（取决于当前 Tab）
document.querySelectorAll(".task-mode-badge").length
// 仅 research / profile 任务的数量
```
