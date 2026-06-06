# Step 026c — Chat 推理过程折叠 UI（GPT 风格）

> 上一步把 chat provider 拆开能跑了。但实际跑起来发现 UX 问题：
> assistant 消息里 thought / tool_call / tool_result 一股脑平铺在 markdown
> 答案前面，长 trace 直接把答案挤出 viewport，用户得自己滚找答案。
> 本步把"过程"折成一张可折叠卡片，answer 落地后自动收起，但保留点击展开复看的能力——
> 跟 ChatGPT / Claude 的 "Thinking…" 卡片同构。

## 1. 目标

- 流式期间 thought / tool_call / tool_result 走单独的 process 容器，**默认展开**
  并显示 spinner + 累计步数（"推理中… 3 步"）
- `answer` 事件到达 → **自动折叠**（`<details open=false>`），显示"推理过程 [3 步] ▾"
  + answer markdown 在下面铺开
- 用户随时可点 summary **重新展开**复看推理链
- 异常路径（流式中断 / `ask_user` / `onDone` 但未产 answer）也要 finalize 卡片，
  避免 spinner 永久转
- answer / citations 仍在 assistant body 直挂，**不**进 process 容器（否则
  折叠时把答案也藏掉了）

## 2. 改动清单

| 文件 | 状态 | 说明 |
| --- | --- | --- |
| `frontend/chat.js` | 修改 | +`ensureProcess` / `processBodyOf` / `bumpProcessCount` / `finalizeProcess`；`renderThought` / `renderToolCall` / `renderToolResult` 改 append 到 process body；`renderAnswer` / `renderAskUser` / `onError` / `onDone` 触发 finalize；末尾挂 `window.__chatDevHooks__` 供 Playwright 注入合成事件 |
| `frontend/style.css` | 修改 | +`.process` / `.process-summary` / `.process-spinner` / `.process-body` 等 12 条规则；运行中 spinner 旋转、完成态变绿色圆点；隐藏 `<details>` 默认三角，自定义 chevron 跟随 `[open]` 旋转 180° |
| `docs/process/step_026c_chat_process_collapse_ui.md` | 新建 | 本文 |

## 3. 关键决策

### D1 — 用原生 `<details>/<summary>` 而非手撸 click handler

| 候选 | 优 | 劣 |
| --- | --- | --- |
| **A. `<details>/<summary>`** ✅ | 浏览器原生 toggle；键盘 a11y 免费；JS 只需读写 `el.open` | 默认有三角 marker（CSS 隐藏一行解决）；样式 reset 略繁 |
| B. div + `.collapsed` class + onclick | 完全可控样式 | 自己实现键盘 / ARIA / focus；代码量增 30+ 行 |
| C. 第三方折叠组件 | 现成 | 引入依赖；项目原则尽量零依赖前端 |

`<details>` 一次性解决了交互 + 可访问性 + 状态语义，CSS 自定义 chevron 即可。

### D2 — process 容器**懒**创建（首个 process 事件触发）

```js
function ensureProcess(body) {
  let proc = body.querySelector(":scope > .process");
  if (proc) return proc;
  proc = document.createElement("details");
  // ...
  body.appendChild(proc);
  return proc;
}
```

理由：

- Agent 路径未必每条都有 thought / tool_call（简单问答 LLM 可能直接 answer
  跳过推理事件）。无脑预创建会留一个空"推理过程 0 步"的卡，更糟。
- 懒创建 + 计数从 1 开始 → 没有过程就没有卡，符合"无过程不显示"的最小信息原则。

### D3 — answer / citations 不进 process 容器

```js
function renderAnswer(body, payload) {
  finalizeProcess(body);    // 先折过程
  // ...
  body.appendChild(wrap);   // answer 直挂 body，与 process 同级
  if (payload.citations?.length) body.appendChild(renderCitations(...));
}
```

如果 answer 也进 process，折叠时答案被一起藏起来，目标完全反转。
**process = 过程档案；answer = 最终输出**，DOM 层级显式区分。

### D4 — 异常路径也要 finalize，否则 spinner 永久转

3 个收尾点：

1. `renderAnswer` 开头 — 正常路径
2. `renderAskUser` 开头 — Agent 抛球给用户等回复时，当前推理段落结束
3. `streamChat` 的 `onError` / `onDone` — 流被中断或后端只发 thought 不发 answer

任一触发即 `proc.open = false`、移除 `.running`、加 `.done`、标题变"推理过程"、
spinner 静态绿圆点。

### D5 — Dev hook 暴露到 `window.__chatDevHooks__`

```js
if (typeof window !== "undefined") {
  window.__chatDevHooks__ = { appendMsg, handleEvent, finalizeProcess, ensureProcess };
}
```

理由：

- 手动测推理过程 UI 需要构造完整 `task_created → thought → tool_call →
  tool_result → answer` 事件序列，端到端走真实 LLM 调用又慢又贵。
- 这 4 个函数都是纯 DOM 渲染、无副作用、不持有 module 内部状态（`_currentTaskId`
  等通过参数传入），暴露安全。
- 命名前后双下划线 + Dev 字样，运行时即可识别"非生产 API"。生产路径完全不依赖它们。

替代方案：写独立 demo HTML 拷一份渲染函数 → 双轨实现易漂移，否决。

### D6 — process 步数计数只算可见 step

```js
const steps = proc.querySelectorAll(":scope > .process-body > .trace, :scope > .process-body > .tool-card").length;
```

直接选择器锁死两类：`.trace`（thought）+ `.tool-card`（tool_call/result 同卡）。
`tool_result` 不算新 step，因为它把状态写回已存在的 tool-card，不新增 DOM。
所以 `tool_call + tool_result` 合算 1 步，符合"一次工具调用 = 1 个步骤"的直觉。

## 4. 验收

### 4.1 Playwright 三段断言（手动 evaluate 注入合成事件流）

经过 `__chatDevHooks__.handleEvent` 注入 `thought → tool_call → tool_result → thought → answer` 5 个事件：

| 状态点 | 断言 | 结果 |
| --- | --- | --- |
| **首个 thought 后** | `process.open=true`、`hasSpinner=true`、`title="推理中…"`、`count="1 步"` | ✅ |
| **3 个事件后** | `count="3 步"`、`stepCount=3`、`hasToolResult=true`（tool_result 写回 tool-card 不增计） | ✅ |
| **answer 落地后** | `open=false`、`hasDoneClass=true`、`noRunningClass=true`、`title="推理过程"`、`answer` 在 body 直挂、`citation-link` href 正确 | ✅ |
| **点击 summary 重新展开** | `open=true`、chevron 旋转、3 步内容（thought + tool-card + thought）全可见、`tool-status=完成` | ✅ |

截图存档（视觉）：

- 折叠态：单行卡片 `• 推理过程 [3 步] ▾` + 下方 markdown 答案 + 引用
- 展开态：spinner 变绿色圆点 + 思考行 + tool_call 卡（含工具名 / 完成徽章 / args JSON / result JSON）

### 4.2 回归

- 既有 615 + 4 (SSE keepalive) 单测不受影响（本步仅前端渲染层改动，无 Python 改动）
- 既有功能：用户消息气泡、welcome 卡、欢迎建议、SSE 中断 notice 不变

## 5. 未做 / 后续

- **流式 markdown 逐 token 刷新**：当前 `answer` 是一次性 markdown 渲染。若后端把
  `answer.text` 拆成 delta 流（每 token 一个事件），前端需要切换到累计 buffer + 增量
  `marked.parse` 重渲染。本步不动 —— 当前后端 `answer` 是整段下发。
- **process 长度阈值**：步数超过 N（比如 8）时是否默认折叠中间段、只展示首尾？
  目前不做，等真碰到长 trace 卡顿再说。
- **过程内 tool_result 可折叠**：长 JSON result 现在最高 240px 滚动，够用。
  独立每条 result 加折叠暂不必要。
- **键盘快捷键**：Esc / Space 切换 process 折叠 —— `<details>` 原生支持 Space，
  Esc 暂不加。
