import { renderSafeMarkdown } from "./markdown.js?v=20261023-flow7";
/**
 * chat.js — 聊天主流程：消息渲染、SSE 接入、状态管理。
 *
 * 单实例约定：整个页面只有一个 chat session，task_id 维护在内部。
 * 每次发送：先 push 用户消息到 DOM，再起 SSE 消费 agent 事件流。
 */

import { streamChat, streamRunResume } from "./sse.js";
import { copilot as copilotApi, tasks as tasksApi, feedback as feedbackApi } from "./api.js";

const $ = (sel) => document.querySelector(sel);

// ─────────── 状态 ───────────
let _currentTaskId = null;
let _abortCurrent = null;
let _onTaskCreated = null;
let _onTaskUpdated = null;
let _onConversationReset = null;
let _onAttachmentSelectionChanged = null;
let _activeRunId = null;
let _streamGeneration = 0;
let _stopPending = false;

export function setCurrentTaskId(taskId) {
  _currentTaskId = taskId;
}
export function getCurrentTaskId() {
  return _currentTaskId;
}
export function onTaskCreated(fn) { _onTaskCreated = fn; }
export function onTaskUpdated(fn) { _onTaskUpdated = fn; }
export function onConversationReset(fn) { _onConversationReset = fn; }
export function onAttachmentSelectionChanged(fn) { _onAttachmentSelectionChanged = fn; }

export function resumeCompletionState({
  errorStatus = null,
  runStatus = null,
} = {}) {
  if (errorStatus === 409) return { buttonText: "已由其他请求处理", outcome: "conflict" };
  if (errorStatus !== null) return { buttonText: "恢复失败", outcome: "failed" };
  if (runStatus === "completed") return { buttonText: "已恢复", outcome: "completed" };
  if (runStatus === "interrupted") return { buttonText: "仍处于暂停", outcome: "interrupted" };
  if (runStatus === "cancelled") return { buttonText: "已取消", outcome: "cancelled" };
  if (runStatus === "timed_out") return { buttonText: "已超时", outcome: "timed_out" };
  if (runStatus === "budget_exhausted") return { buttonText: "预算已耗尽", outcome: "budget_exhausted" };
  if (runStatus === "failed") return { buttonText: "恢复失败", outcome: "failed" };
  return { buttonText: "恢复未完成", outcome: "failed" };
}

export function isCurrentStream(streamGeneration, currentGeneration) {
  return streamGeneration === currentGeneration;
}

const RUN_STATE_LABELS = {
  idle: "就绪",
  agent: "正在执行",
  tool: "正在检索",
  generation: "正在生成待核验草稿",
  verification: "正在核验",
  cancel_requested: "正在停止",
  interrupted: "已暂停",
  cancelled: "已取消",
  timed_out: "已超时",
  budget_exhausted: "预算已耗尽",
  failed: "失败",
  completed: "已完成",
};
const SETTLED_RUN_STATES = new Set([
  "interrupted",
  "cancelled",
  "timed_out",
  "budget_exhausted",
  "failed",
  "completed",
]);

export function runStateLabel(state) {
  return RUN_STATE_LABELS[state] || state || RUN_STATE_LABELS.idle;
}

export function authoritativeRunStatus(currentStatus, event, data = null) {
  if (event === "run_started" || event === "run_resumed") return "running";
  if (event === "run_interrupted") return "interrupted";
  if (event === "run_status") return data?.status || currentStatus;
  // Message/progress events, including answer, never decide run success.
  return currentStatus;
}

export function shouldDisplayAnswer(runStatus) {
  return runStatus === "completed" || runStatus === "failed";
}

function setRunState(state) {
  const label = $("#run-status");
  if (label) label.textContent = runStateLabel(state);
  const stop = $("#btn-stop");
  if (stop) {
    const active = ["agent", "tool", "generation", "verification", "cancel_requested"].includes(state);
    stop.classList.toggle("hidden", !active);
    stop.disabled = !_activeRunId || _stopPending || state === "cancel_requested";
  }
}

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
      <svg aria-hidden="true" class="tool-icon" fill="none" viewBox="0 0 24 24"><path d="M12 3.5 14.2 8l4.8.7-3.5 3.4.8 4.8-4.3-2.3-4.3 2.3.8-4.8L5 8.7 9.8 8 12 3.5Z"/></svg>
      <span class="tool-name">${escapeHtml(payload.tool_name || "tool")}</span>
      <span class="tool-status">运行中…</span>
    </div>
    <div class="tool-args"></div>
  `;
  card.querySelector(".tool-args").textContent = payload.summary || "已提交受控工具请求";
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
  const value = payload.result;
  result.textContent = value?.evidence_count != null
    ? `找到 ${value.evidence_count} 条候选证据`
    : value?.control_type === "clarification"
      ? "正在确认缺失信息"
      : payload.is_error
        ? String(value || "工具执行失败")
        : "工具执行完成";
  card.appendChild(result);
  scrollToBottom();
}

function renderDraftDelta(body, payload) {
  let draft = body.querySelector(":scope > .answer-draft");
  if (!draft) {
    draft = document.createElement("div");
    draft.className = "answer-draft";
    draft.innerHTML = '<div class="answer-draft-label">待核验草稿</div><div class="answer-draft-text"></div>';
    body.appendChild(draft);
  }
  draft.querySelector(".answer-draft-text").textContent += payload.text || "";
  setRunState("generation");
  scrollToBottom();
}

function discardDraft(body) {
  body.querySelector(":scope > .answer-draft")?.remove();
}

function renderAnswer(body, payload) {
  // answer 落地 → 折叠推理过程卡片
  finalizeProcess(body);
  discardDraft(body);

  const wrap = document.createElement("div");
  wrap.className = "answer";
  const md = (payload.text || "").trim();
  renderSafeMarkdown(wrap, md);
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
  copyBtn.innerHTML = `<span class="aa-ico"><svg aria-hidden="true" fill="none" viewBox="0 0 24 24"><rect height="12" rx="2" width="12" x="8" y="8"></rect><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"></path></svg></span><span class="aa-label">复制</span>`;
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
  upBtn.innerHTML = `<span class="aa-ico"><svg aria-hidden="true" fill="none" viewBox="0 0 24 24"><path d="M7 10v10H4V10h3Zm0 8h10.2a2 2 0 0 0 2-1.7l.8-5A2 2 0 0 0 18 9h-4l.6-3.1A1.6 1.6 0 0 0 13 4l-1 3.2L7 12"></path></svg></span>`;

  const downBtn = document.createElement("button");
  downBtn.type = "button";
  downBtn.className = "answer-action answer-action-down";
  downBtn.title = "没帮助";
  downBtn.innerHTML = `<span class="aa-ico"><svg aria-hidden="true" fill="none" viewBox="0 0 24 24"><path d="M7 14V4H4v10h3Zm0-8h10.2a2 2 0 0 1 2 1.7l.8 5A2 2 0 0 1 18 15h-4l.6 3.1A1.6 1.6 0 0 1 13 20l-1-3.2L7 12"></path></svg></span>`;

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

function renderRunInterrupted(body, payload) {
  finalizeProcess(body);
  const box = document.createElement("div");
  box.className = "notice";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "btn btn-primary";
  button.textContent = "继续本轮执行";
  box.append("执行已安全暂停。", button);
  body.appendChild(box);
  button.addEventListener("click", () => {
    if (_abortCurrent) return;
    const streamGeneration = ++_streamGeneration;
    _activeRunId = payload.run_id;
    button.disabled = true;
    button.textContent = "恢复中…";
    setRunState("agent");
    let terminalStatus = null;
    _abortCurrent = streamRunResume(payload.run_id, {
      onEvent: (frame) => {
        if (!isCurrentStream(streamGeneration, _streamGeneration)) return;
        if (frame.event === "run_status" && frame.data?.status !== "running") {
          terminalStatus = frame.data?.status || null;
        } else if (frame.event === "run_interrupted") {
          terminalStatus = "interrupted";
        }
        handleEvent(frame, body);
      },
      onError: (error) => {
        if (!isCurrentStream(streamGeneration, _streamGeneration)) return;
        finalizeProcess(body);
        const knownStatus = terminalStatus && SETTLED_RUN_STATES.has(terminalStatus)
          ? terminalStatus
          : null;
        button.textContent = resumeCompletionState({
          errorStatus: knownStatus ? null : error.status,
          runStatus: knownStatus,
        }).buttonText;
        setRunState(knownStatus || (error.status === 409 ? "idle" : "failed"));
        _activeRunId = null;
        _abortCurrent = null;
        appendNotice(`恢复失败：${error.message}`);
        _onTaskUpdated?.(_currentTaskId);
      },
      onDone: () => {
        if (!isCurrentStream(streamGeneration, _streamGeneration)) return;
        finalizeProcess(body);
        button.textContent = resumeCompletionState({ runStatus: terminalStatus }).buttonText;
        if (_activeRunId) setRunState("failed");
        _activeRunId = null;
        _abortCurrent = null;
        _onTaskUpdated?.(_currentTaskId);
      },
    });
  });
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
    const location = [
      c.citation_id || "",
      c.source_version ? `版本 ${c.source_version}` : "",
      c.page_number ? `第 ${c.page_number} 页` : "",
    ].filter(Boolean).join(" · ");

    const item = document.createElement("div");
    item.className = "citation";
    const titleHtml = url
      ? `<a class="citation-link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(title)}</a>`
      : escapeHtml(title);
    item.innerHTML = `
      <div class="citation-source">${titleHtml}</div>
      ${location ? `<div class="citation-location">${escapeHtml(location)}</div>` : ""}
      <div class="citation-snippet">${escapeHtml(snippet)}</div>
    `;
    box.appendChild(item);
  }
  return box;
}

// ─────────── 公开：发送一条用户消息 ───────────
export function sendMessage(text, attachmentDocIds = undefined) {
  if (!text || !text.trim()) return;
  if (_abortCurrent) {
    _abortCurrent.abort();
    _abortCurrent = null;
  }
  const streamGeneration = ++_streamGeneration;
  _activeRunId = null;
  setRunState("agent");

  // 用户气泡
  const userBody = appendMsg("user");
  userBody.textContent = text;

  // 助手占位
  const assistantBody = appendMsg("assistant");
  const typing = appendTyping(assistantBody);

  const payload = { message: text };
  if (attachmentDocIds !== undefined) payload.attachment_doc_ids = attachmentDocIds;
  if (_currentTaskId) {
    payload.task_id = _currentTaskId;
  }

  _abortCurrent = streamChat(payload, {
    onEvent: (frame) => {
      if (!isCurrentStream(streamGeneration, _streamGeneration)) return;
      // 收到第一个真实事件就清掉 typing
      if (typing.parentNode) typing.remove();
      handleEvent(frame, assistantBody);
    },
    onError: (err) => {
      if (!isCurrentStream(streamGeneration, _streamGeneration)) return;
      if (typing.parentNode) typing.remove();
      finalizeProcess(assistantBody);
      const knownStatus = assistantBody.dataset.runStatus;
      setRunState(SETTLED_RUN_STATES.has(knownStatus) ? knownStatus : "failed");
      _activeRunId = null;
      _abortCurrent = null;
      appendNotice(`流式中断：${err.message}`);
    },
    onDone: () => {
      if (!isCurrentStream(streamGeneration, _streamGeneration)) return;
      // 免防后端只发 thought 不发 answer 时 spinner 永久转。
      finalizeProcess(assistantBody);
      _abortCurrent = null;
      if (_activeRunId) setRunState("failed");
      _activeRunId = null;
      _onTaskUpdated?.(_currentTaskId);
    },
  });
}

function handleEvent(frame, body) {
  const { event, data } = frame;
  const trackedStatus = authoritativeRunStatus(body.dataset.runStatus || null, event, data);
  if (trackedStatus) body.dataset.runStatus = trackedStatus;
  switch (event) {
    case "task_created":
      _currentTaskId = data?.task_id || _currentTaskId;
      if (_currentTaskId) _onTaskCreated?.(_currentTaskId, data?.title || "新对话");
      break;
    case "run_started":
    case "run_resumed":
      _activeRunId = data?.run_id || _activeRunId;
      setRunState("agent");
      break;
    case "run_status":
      setRunState(data?.status === "running" ? data?.stage : data?.status);
      if (["completed", "cancelled", "timed_out", "budget_exhausted", "failed"].includes(data?.status)) {
        _activeRunId = null;
        finalizeProcess(body);
        if (data?.status !== "completed" && !body.dataset.runEnded) {
          body.dataset.runEnded = "true";
          discardDraft(body);
          const messages = {
            cancelled: "本轮已取消，未保存未核验草稿。",
            timed_out: "本轮执行超时，未保存未核验草稿。",
            budget_exhausted: "本轮调用预算已耗尽，未保存未核验草稿。",
            failed: "本轮执行失败。",
          };
          const notice = document.createElement("div");
          notice.className = "notice";
          notice.textContent = messages[data.status] || "本轮未完成。";
          body.appendChild(notice);
        }
      }
      break;
    case "run_interrupted":
      _activeRunId = null;
      setRunState("interrupted");
      renderRunInterrupted(body, data || {});
      break;
    case "thought":
      renderThought(body, data?.text || "");
      break;
    case "tool_call":
      setRunState("tool");
      renderToolCall(body, data || {});
      break;
    case "tool_result":
      renderToolResult(body, data || {});
      break;
    case "tool_error":
      renderToolResult(body, {
        tool_name: data?.tool_name || "tool",
        result: data?.error || "工具执行失败",
        is_error: true,
      });
      break;
    case "draft_delta":
      renderDraftDelta(body, data || {});
      break;
    case "draft_discarded":
      discardDraft(body);
      break;
    case "answer":
      // answer 只负责展示消息；运行是否成功由服务端 run_status 决定。
      // 技术失败也可能携带一条安全说明，不能因此改写为 completed。
      if (!shouldDisplayAnswer(body.dataset.runStatus)) {
        discardDraft(body);
        break;
      }
      renderAnswer(body, data || {});
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
  _streamGeneration += 1;
  if (_abortCurrent) {
    _abortCurrent.abort();
    _abortCurrent = null;
  }
  _currentTaskId = taskId;
  _activeRunId = null;
  setRunState("idle");
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
  _onAttachmentSelectionChanged?.(detail.attachment_doc_ids || []);
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
  if (detail.latest_run) {
    const latest = detail.latest_run;
    setRunState(latest.status === "running" ? latest.stage : latest.status);
    if (["running", "cancel_requested"].includes(latest.status)) {
      _activeRunId = latest.run_id;
    }
  }
  if (detail.latest_run?.status === "interrupted") {
    const body = appendMsg("assistant");
    renderRunInterrupted(body, detail.latest_run);
  }
}

export function newConversation() {
  _streamGeneration += 1;
  if (_abortCurrent) { _abortCurrent.abort(); _abortCurrent = null; }
  _currentTaskId = null;
  _activeRunId = null;
  setRunState("idle");
  $("#chat-title").textContent = "新对话";
  messagesEl().innerHTML = `<div class="welcome" id="welcome"></div>`;
  _onAttachmentSelectionChanged?.([]);
  _onConversationReset?.();
}

export async function stopGeneration() {
  if (!_activeRunId || _stopPending) return;
  const runId = _activeRunId;
  _stopPending = true;
  setRunState("cancel_requested");
  try {
    const run = await copilotApi.cancelRun(runId);
    if (runId !== _activeRunId) return;
    setRunState(run.status);
    if (run.status !== "cancel_requested") _activeRunId = null;
  } catch (error) {
    if (runId !== _activeRunId) return;
    appendNotice(`停止失败：${error.message}`);
    _activeRunId = null;
    setRunState("failed");
  } finally {
    _stopPending = false;
    const current = _activeRunId ? "cancel_requested" : "idle";
    if (_activeRunId) setRunState(current);
  }
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
