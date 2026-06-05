/**
 * chat.js — 聊天主流程：消息渲染、SSE 接入、状态管理。
 *
 * 单实例约定：整个页面只有一个 chat session，task_id 维护在内部。
 * 每次发送：先 push 用户消息到 DOM，再起 SSE 消费 agent 事件流。
 */

import { streamChat } from "./sse.js";
import { tasks as tasksApi } from "./api.js";

const $ = (sel) => document.querySelector(sel);

// ─────────── 状态 ───────────
let _currentTaskId = null;
let _abortCurrent = null;
let _onTaskCreated = null;
let _onTaskUpdated = null;

export function setCurrentTaskId(taskId) {
  _currentTaskId = taskId;
}
export function getCurrentTaskId() {
  return _currentTaskId;
}

export function onTaskCreated(fn) { _onTaskCreated = fn; }
export function onTaskUpdated(fn) { _onTaskUpdated = fn; }

// ─────────── DOM 渲染 ───────────
const messagesEl = () => $("#messages");
const welcomeEl = () => $("#welcome");

function hideWelcome() {
  const w = welcomeEl();
  if (w) w.remove();
}

function scrollToBottom() {
  const el = messagesEl();
  el.scrollTop = el.scrollHeight;
}

function appendMsg(role) {
  hideWelcome();
  const wrap = document.createElement("div");
  wrap.className = `msg msg-${role}`;
  const roleLabel = role === "user" ? "你" : "合规副驾";
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

// ─────────── 事件 → DOM ───────────
function renderThought(body, text) {
  const el = document.createElement("div");
  el.className = "trace thought";
  el.textContent = text;
  body.appendChild(el);
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
  body.appendChild(card);
  scrollToBottom();
  return card;
}

function renderToolResult(body, payload) {
  // 找到匹配 tool_name 的最后一张未完成卡片
  const cards = body.querySelectorAll(`.tool-card[data-tool-name="${cssEscape(payload.tool_name)}"]`);
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
  scrollToBottom();
}

function renderAskUser(body, payload) {
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
    const item = document.createElement("div");
    item.className = "citation";
    item.innerHTML = `
      <div class="citation-source">${escapeHtml(c.source_title || c.source || "未命名来源")}</div>
      <div class="citation-snippet">${escapeHtml(c.snippet || "")}</div>
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
  if (_currentTaskId) payload.task_id = _currentTaskId;

  _abortCurrent = streamChat(payload, {
    onEvent: (frame) => {
      // 收到第一个真实事件就清掉 typing
      if (typing.parentNode) typing.remove();
      handleEvent(frame, assistantBody);
    },
    onError: (err) => {
      if (typing.parentNode) typing.remove();
      appendNotice(`流式中断：${err.message}`);
    },
    onDone: () => {
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

  for (const m of detail.messages || []) {
    const body = appendMsg(m.role === "user" ? "user" : "assistant");
    if (m.role === "user") {
      body.textContent = m.content || "";
    } else {
      renderAnswer(body, { text: m.content || "", citations: m.citations || [] });
    }
  }
}

export function newConversation() {
  if (_abortCurrent) { _abortCurrent.abort(); _abortCurrent = null; }
  _currentTaskId = null;
  $("#chat-title").textContent = "新对话";
  messagesEl().innerHTML = `
    <div class="welcome" id="welcome">
      <h1>新对话已就绪</h1>
      <p>问点合规问题吧 ——</p>
    </div>
  `;
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
