/**
 * chat.js — 聊天主流程：消息渲染、SSE 接入、状态管理。
 *
 * 单实例约定：整个页面只有一个 chat session，task_id 维护在内部。
 * 每次发送：先 push 用户消息到 DOM，再起 SSE 消费 agent 事件流。
 */

import { streamChat } from "./sse.js";
import { tasks as tasksApi, feedback as feedbackApi } from "./api.js";

const $ = (sel) => document.querySelector(sel);

// ─────────── 状态 ───────────
let _currentTaskId = null;
let _currentMode = "qa";  // qa | research | profile；仅在创建新 task 时生效
let _abortCurrent = null;
let _onTaskCreated = null;
let _onTaskUpdated = null;
let _onModeChanged = null;
let _onConversationReset = null;

export function setCurrentTaskId(taskId) {
  _currentTaskId = taskId;
}
export function getCurrentTaskId() {
  return _currentTaskId;
}
export function getCurrentMode() {
  return _currentMode;
}
/** 设置当前模式；仅下一条新 task 生效。返回是否变更。*/
export function setCurrentMode(mode) {
  if (mode !== "qa" && mode !== "research" && mode !== "profile") return false;
  if (_currentMode === mode) return false;
  _currentMode = mode;
  _onModeChanged?.(mode);
  return true;
}

export function onTaskCreated(fn) { _onTaskCreated = fn; }
export function onTaskUpdated(fn) { _onTaskUpdated = fn; }
export function onModeChanged(fn) { _onModeChanged = fn; }
/** 新对话剧本重置后触发，供上层渲染当前 mode 的欢迎卡。*/
export function onConversationReset(fn) { _onConversationReset = fn; }

// ─────────── DOM 渲染 ───────────
const messagesEl = () => $("#messages");
const welcomeEl = () => $("#welcome");

function hideWelcome() {
  const w = welcomeEl();
  if (w) w.remove();
}

function scrollToBottom() {
  const el = messagesEl();
  // 仅当用户已贴近底部时才 auto-follow，避免他往上读历史时被流式事件拽回底部。
  // 阈值 80px：留出一行的容忍度，鼠标轻微往上滚就算"脱离底部"。
  const STICK_THRESHOLD = 80;
  const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
  if (distanceFromBottom <= STICK_THRESHOLD) {
    el.scrollTop = el.scrollHeight;
  }
}

function appendMsg(role) {
  hideWelcome();
  const wrap = document.createElement("div");
  wrap.className = `msg msg-${role}`;
  const roleLabel = role === "user" ? "你" : "数智合规";
  wrap.innerHTML = `
    <div class="msg-role">${roleLabel}</div>
    <div class="msg-body"></div>
  `;
  messagesEl().appendChild(wrap);
  scrollToBottom();
  return wrap.querySelector(".msg-body");
}

function appendNotice(text) {
  hideWelcome();
  const div = document.createElement("div");
  div.className = "notice";
  div.textContent = text;
  messagesEl().appendChild(div);
  scrollToBottom();
}

function appendTyping(parent) {
  const t = document.createElement("div");
  t.className = "typing";
  t.innerHTML = `<div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>`;
  parent.appendChild(t);
  scrollToBottom();
  return t;
}

// ─────────── 过程容器（thought / tool_call / tool_result 的折叠卡片）───────────
/**
 * 懒创建当前 assistant body 内的 process 容器。answer 出现时折叠。
 * 使用 <details> 让浏览器原生处理展开/收起 + 键盘可访问。
 */
function ensureProcess(body) {
  let proc = body.querySelector(":scope > .process");
  if (proc) return proc;
  proc = document.createElement("details");
  proc.className = "process running";
  proc.open = true;
  proc.innerHTML = `
    <summary class="process-summary">
      <span class="process-spinner" aria-hidden="true"></span>
      <span class="process-title">推理中…</span>
      <span class="process-count"></span>
      <span class="process-chevron" aria-hidden="true">▾</span>
    </summary>
    <div class="process-body"></div>
  `;
  body.appendChild(proc);
  return proc;
}

function processBodyOf(body) {
  return ensureProcess(body).querySelector(".process-body");
}

function bumpProcessCount(body) {
  const proc = body.querySelector(":scope > .process");
  if (!proc) return;
  const steps = proc.querySelectorAll(":scope > .process-body > .trace, :scope > .process-body > .tool-card").length;
  proc.querySelector(".process-count").textContent = `${steps} 步`;
}

/**
 * answer 落地后调用：把 process 卡片折叠并标记为已完成。
 * 不删 DOM——用户仍可点 summary 展开复看推理过程。
 */
function finalizeProcess(body) {
  const proc = body.querySelector(":scope > .process");
  if (!proc) return;
  proc.open = false;
  proc.classList.remove("running");
  proc.classList.add("done");
  const title = proc.querySelector(".process-title");
  if (title) title.textContent = "推理过程";
}

// ─────────── 事件 → DOM ───────────
function renderThought(body, text) {
  const el = document.createElement("div");
  el.className = "trace thought";
  el.textContent = text;
  processBodyOf(body).appendChild(el);
  bumpProcessCount(body);
  scrollToBottom();
}

function renderToolCall(body, payload) {
  const card = document.createElement("div");
  card.className = "tool-card";
  card.dataset.toolName = payload.tool_name;
  card.innerHTML = `
    <div class="tool-card-header">
      🛠 <span class="tool-name">${escapeHtml(payload.tool_name || "tool")}</span>
      <span class="tool-status">运行中…</span>
    </div>
    <div class="tool-args"></div>
  `;
  card.querySelector(".tool-args").textContent =
    JSON.stringify(payload.tool_args || {}, null, 2);
  processBodyOf(body).appendChild(card);
  bumpProcessCount(body);
  scrollToBottom();
  return card;
}

function renderToolResult(body, payload) {
  // 在 process 容器内找匹配 tool_name 的最后一张未完成卡片
  const scope = body.querySelector(":scope > .process .process-body") || body;
  const cards = scope.querySelectorAll(`.tool-card[data-tool-name="${cssEscape(payload.tool_name)}"]`);
  let card = null;
  for (const c of cards) {
    if (!c.querySelector(".tool-result")) card = c;
  }
  if (!card) {
    // 没有对应的 call（罕见，可能是历史回放），独立创建
    card = renderToolCall(body, payload);
  }
  const ok = !payload.is_error;
  const status = card.querySelector(".tool-status");
  status.textContent = ok ? "完成" : "失败";
  status.classList.add(ok ? "ok" : "err");

  const result = document.createElement("div");
  result.className = "tool-result";
  result.textContent = formatToolResult(payload.result);
  card.appendChild(result);
  scrollToBottom();
}

function renderAnswer(body, payload) {
  // answer 落地 → 折叠推理过程卡片
  finalizeProcess(body);

  const wrap = document.createElement("div");
  wrap.className = "answer";
  const md = (payload.text || "").trim();
  // marked 是全局 UMD（index.html 已加载）
  // eslint-disable-next-line no-undef
  wrap.innerHTML = window.marked ? window.marked.parse(md) : `<p>${escapeHtml(md)}</p>`;
  body.appendChild(wrap);

  if (payload.citations?.length) {
    body.appendChild(renderCitations(payload.citations));
  }

  // 回答下方操作条：复制 / 点赞 / 点踩 / 时间
  body.appendChild(
    renderAnswerActions({
      rawText: md,
      msgId: payload.msg_id || null,
      taskId: payload.task_id || _currentTaskId,
      createdAt: payload.created_at,
      rating: payload.rating || null,
    })
  );
  scrollToBottom();
}

/**
 * 渲染单条回答下方的操作条：复制、点赞、点踩、生成时间。
 * 点赞/点踩通过 /api/v2/feedback 落库供后台统计；需要 msgId 才启用。
 */
function renderAnswerActions({ rawText, msgId, taskId, createdAt, rating }) {
  const bar = document.createElement("div");
  bar.className = "answer-actions";
  if (msgId) bar.dataset.msgId = msgId;

  // ── 复制 ──
  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.className = "answer-action";
  copyBtn.title = "复制回答";
  copyBtn.innerHTML = `<span class="aa-ico">⧉</span><span class="aa-label">复制</span>`;
  copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(rawText || "");
      copyBtn.classList.add("done");
      copyBtn.querySelector(".aa-label").textContent = "已复制";
      setTimeout(() => {
        copyBtn.classList.remove("done");
        copyBtn.querySelector(".aa-label").textContent = "复制";
      }, 1500);
    } catch {
      copyBtn.querySelector(".aa-label").textContent = "复制失败";
    }
  });
  bar.appendChild(copyBtn);

  // ── 点赞 / 点踩 ──
  const upBtn = document.createElement("button");
  upBtn.type = "button";
  upBtn.className = "answer-action answer-action-up";
  upBtn.title = "有帮助";
  upBtn.innerHTML = `<span class="aa-ico">👍</span>`;

  const downBtn = document.createElement("button");
  downBtn.type = "button";
  downBtn.className = "answer-action answer-action-down";
  downBtn.title = "没帮助";
  downBtn.innerHTML = `<span class="aa-ico">👎</span>`;

  if (!msgId) {
    // 没有 msg_id（理论上不该发生）→ 反馈不可用
    upBtn.disabled = true;
    downBtn.disabled = true;
  } else {
    const applyState = (r) => {
      upBtn.classList.toggle("active", r === "up");
      downBtn.classList.toggle("active", r === "down");
      bar.dataset.rating = r || "";
    };
    applyState(rating);

    const vote = async (value) => {
      const current = bar.dataset.rating || "";
      // 再次点击同一选项 = 撤销
      const next = current === value ? "none" : value;
      applyState(next === "none" ? null : next);
      try {
        await feedbackApi.submit({ msg_id: msgId, task_id: taskId, rating: next });
      } catch (err) {
        // 失败回滚到原状态
        applyState(current || null);
        appendNotice(`反馈提交失败：${err.message}`);
      }
    };
    upBtn.addEventListener("click", () => vote("up"));
    downBtn.addEventListener("click", () => vote("down"));
  }
  bar.appendChild(upBtn);
  bar.appendChild(downBtn);

  // ── 时间 ──
  const timeEl = document.createElement("span");
  timeEl.className = "answer-time";
  const ts = typeof createdAt === "number" ? createdAt * 1000 : Date.now();
  const d = new Date(ts);
  timeEl.textContent = formatTime(d);
  timeEl.title = d.toLocaleString();
  bar.appendChild(timeEl);

  return bar;
}

function formatTime(d) {
  const pad = (n) => String(n).padStart(2, "0");
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (sameDay) return hm;
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hm}`;
}

function renderAskUser(body, payload) {
  // 需要用户确认 → 推理告一段落，折叠过程卡片
  finalizeProcess(body);
  const el = document.createElement("div");
  el.className = "trace";
  el.innerHTML = `🙋 <strong>需要你确认</strong>：${escapeHtml(payload.question || "")}`;
  body.appendChild(el);
  scrollToBottom();
}

function renderCitations(citations) {
  const box = document.createElement("div");
  box.className = "citations";
  const label = document.createElement("div");
  label.className = "citations-label";
  label.textContent = `引用 · ${citations.length}`;
  box.appendChild(label);
  for (const c of citations) {
    // 后端规范字段：source_name / title / source_url / text_snippet / source_type；
    // 为兼容 LLM 不遵 prompt 的变体填法，允许 source/source_title/name + snippet/text 作为回退。
    const title = c.title || c.source_name || c.source_title || c.source || c.name || "未命名来源";
    const snippet = c.text_snippet || c.snippet || c.text || "";
    const url = typeof c.source_url === "string" && c.source_url.startsWith("http") ? c.source_url : null;

    const item = document.createElement("div");
    item.className = "citation";
    const titleHtml = url
      ? `<a class="citation-link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(title)}</a>`
      : escapeHtml(title);
    item.innerHTML = `
      <div class="citation-source">${titleHtml}</div>
      <div class="citation-snippet">${escapeHtml(snippet)}</div>
    `;
    box.appendChild(item);
  }
  return box;
}

// ─────────── 公开：发送一条用户消息 ───────────
export function sendMessage(text) {
  if (!text || !text.trim()) return;
  if (_abortCurrent) {
    _abortCurrent.abort();
    _abortCurrent = null;
  }

  // 用户气泡
  const userBody = appendMsg("user");
  userBody.textContent = text;

  // 助手占位
  const assistantBody = appendMsg("assistant");
  const typing = appendTyping(assistantBody);

  const payload = { message: text };
  if (_currentTaskId) {
    payload.task_id = _currentTaskId;
  } else {
    // 仅在创建新 task 时顺手携上 mode；service 会持久化到 task.mode
    payload.mode = _currentMode;
  }

  _abortCurrent = streamChat(payload, {
    onEvent: (frame) => {
      // 收到第一个真实事件就清掉 typing
      if (typing.parentNode) typing.remove();
      handleEvent(frame, assistantBody);
    },
    onError: (err) => {
      if (typing.parentNode) typing.remove();
      finalizeProcess(assistantBody);
      appendNotice(`流式中断：${err.message}`);
    },
    onDone: () => {
      // 免防后端只发 thought 不发 answer 时 spinner 永久转。
      finalizeProcess(assistantBody);
      _abortCurrent = null;
      _onTaskUpdated?.(_currentTaskId);
    },
  });
}

function handleEvent(frame, body) {
  const { event, data } = frame;
  switch (event) {
    case "task_created":
      _currentTaskId = data?.task_id || _currentTaskId;
      if (_currentTaskId) _onTaskCreated?.(_currentTaskId, data?.title || "新对话");
      break;
    case "thought":
      renderThought(body, data?.text || "");
      break;
    case "tool_call":
      renderToolCall(body, data || {});
      break;
    case "tool_result":
      renderToolResult(body, data || {});
      break;
    case "answer":
      renderAnswer(body, data || {});
      break;
    case "ask_user":
      renderAskUser(body, data || {});
      break;
    case "citations":
      body.appendChild(renderCitations(data?.items || data || []));
      scrollToBottom();
      break;
    case "error":
      appendNotice(`Agent 错误 [${data?.error_code || "?"}]：${data?.message || "未知"}`);
      break;
    default:
      // 未知事件：debug 输出，不阻塞渲染
      console.debug("[sse] unknown event", event, data);
  }
}

// ─────────── 切换/重置对话 ───────────
/**
 * 进入某个历史 task：清空 message 区域，从 /tasks/{id} 拉历史并回放。
 */
export async function loadTask(taskId) {
  if (_abortCurrent) {
    _abortCurrent.abort();
    _abortCurrent = null;
  }
  _currentTaskId = taskId;
  messagesEl().innerHTML = "";

  let detail;
  try {
    detail = await tasksApi.get(taskId);
  } catch (err) {
    appendNotice(`加载历史失败：${err.message}`);
    return;
  }

  const title = detail.task?.title || "对话";
  $("#chat-title").textContent = title;
  // 历史 task 的 mode 是唯一真相 → 同步 UI Tab
  const taskMode = detail.task?.mode || "qa";
  if (taskMode !== _currentMode) {
    _currentMode = taskMode;
    _onModeChanged?.(taskMode);
  }

  // 反馈状态（点赞/点踩）一次性拉取，用于回显按钮高亮；失败不阻塞历史渲染。
  let ratings = {};
  try {
    const fb = await feedbackApi.forTask(taskId);
    ratings = fb?.ratings || {};
  } catch {
    ratings = {};
  }

  for (const m of detail.messages || []) {
    const body = appendMsg(m.role === "user" ? "user" : "assistant");
    if (m.role === "user") {
      body.textContent = m.content || "";
    } else {
      renderAnswer(body, {
        text: m.content || "",
        citations: m.citations || [],
        msg_id: m.msg_id,
        task_id: taskId,
        created_at: m.created_at,
        rating: ratings[m.msg_id] || null,
      });
    }
  }
}

export function newConversation() {
  if (_abortCurrent) { _abortCurrent.abort(); _abortCurrent = null; }
  _currentTaskId = null;
  $("#chat-title").textContent = "新对话";
  // 空容器 + 上层按 mode 填充
  messagesEl().innerHTML = `<div class="welcome" id="welcome"></div>`;
  _onConversationReset?.(_currentMode);
}

// ─────────── 工具函数 ───────────
function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function cssEscape(s) {
  // 仅对工具名场景兜底，工具名不含特殊字符
  return String(s ?? "").replace(/"/g, "\\\"");
}

function formatToolResult(r) {
  if (r == null) return "(no result)";
  if (typeof r === "string") return r;
  try { return JSON.stringify(r, null, 2); } catch { return String(r); }
}

// ─────────── Dev 钩子（仅用于 Playwright / 手动验证 process UI）───────────
// 生产路径不依赖这些函数；挂到 window 便于在浏览器里注入合成事件序列回放。
if (typeof window !== "undefined") {
  window.__chatDevHooks__ = {
    appendMsg,
    handleEvent,
    finalizeProcess,
    ensureProcess,
  };
}
