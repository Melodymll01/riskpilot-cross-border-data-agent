/**
 * DataGuard 数据出境合规知识库 — 前端应用
 */

// ==================== Markdown 渲染器检查 ====================
if (typeof marked === 'undefined') {
  console.error('[DataGuard] marked.js 未加载，Markdown 渲染将使用纯文本回退');
} else {
  console.log('[DataGuard] marked.js 已就绪');
}

// ==================== 状态管理 ====================

const CHAT_STORAGE_KEY = "dataguard_chat_history";

const state = {
  currentView: "dashboard",
  sources: [],
  chatHistory: [],
  isLoading: false,
  totalChunks: 0,
  // 对话历史
  currentConversationId: null,
  conversations: [],
  historyPanelOpen: true,
};

// ==================== 会话持久化（服务端） ====================

function saveChatHistory() {
  // 旧的 localStorage 存储仍作为降级备份
  try {
    const data = state.chatHistory.slice(-100);
    localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(data));
  } catch { /* quota exceeded — 静默忽略 */ }
}

function loadChatHistory() {
  // 初始化时从服务器加载对话列表（异步）
  loadConversationList();
}

// ==================== 对话历史管理 ====================

async function loadConversationList() {
  const list = $("#chat-history-list");
  try {
    const res = await fetch("/api/conversations?limit=50");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    state.conversations = data.conversations || [];
    renderConversationList();
  } catch (e) {
    console.error("加载对话列表失败", e);
    // 失败时也要清除"加载中"
    if (list) {
      list.innerHTML = `
        <div class="chat-history-empty">
          <div class="empty-icon">💬</div>
          <p>暂无历史对话</p>
          <p style="font-size:11px;margin-top:4px">开始提问即自动创建对话</p>
        </div>`;
    }
  }
}

function renderConversationList() {
  const list = $("#chat-history-list");
  if (!list) return;

  if (state.conversations.length === 0) {
    list.innerHTML = `
      <div class="chat-history-empty">
        <div class="empty-icon">💬</div>
        <p>暂无历史对话</p>
        <p style="font-size:11px;margin-top:4px">开始提问即自动创建对话</p>
      </div>`;
    return;
  }

  // 按日期分组
  const groups = groupConversationsByDate(state.conversations);
  let html = "";
  for (const [label, convs] of groups) {
    html += `<div class="conv-date-label">${escapeHtml(label)}</div>`;
    for (const c of convs) {
      const isActive = c.id === state.currentConversationId;
      html += `
        <div class="conv-item${isActive ? " active" : ""}" data-conv-id="${escapeAttr(c.id)}" onclick="switchConversation('${escapeAttr(c.id)}')">
          <div class="conv-item-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 11.5a8.38 8.38 0 01-.9 3.8 8.5 8.5 0 01-7.6 4.7 8.38 8.38 0 01-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 01-.9-3.8 8.5 8.5 0 014.7-7.6 8.38 8.38 0 013.8-.9h.5a8.48 8.48 0 018 8v.5z"/>
            </svg>
          </div>
          <div class="conv-item-body">
            <div class="conv-item-title">${escapeHtml(c.title)}</div>
            <div class="conv-item-meta">${c.message_count || 0} 条消息</div>
          </div>
          <div class="conv-item-actions">
            <button class="btn-icon" onclick="event.stopPropagation();deleteConversation('${escapeAttr(c.id)}')" title="删除">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/></svg>
            </button>
          </div>
        </div>`;
    }
  }
  list.innerHTML = html;
}

function groupConversationsByDate(convs) {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
  const yesterday = today - 86400;
  const weekAgo = today - 86400 * 7;
  const monthAgo = today - 86400 * 30;

  const groups = new Map();
  for (const c of convs) {
    let label;
    if (c.updated_at >= today) label = "今天";
    else if (c.updated_at >= yesterday) label = "昨天";
    else if (c.updated_at >= weekAgo) label = "最近 7 天";
    else if (c.updated_at >= monthAgo) label = "最近 30 天";
    else label = "更早";
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(c);
  }
  return groups;
}

async function createNewConversation() {
  // GPT 模式：只重置状态，不提前创建对话
  // 用户发第一条消息时 sendMessage() 会自动创建
  state.currentConversationId = null;
  state.chatHistory = [];
  resetChatUI();
  renderConversationList();
  focusChatInput();
}

async function switchConversation(convId) {
  if (convId === state.currentConversationId) return;
  try {
    const res = await fetch(`/api/conversations/${encodeURIComponent(convId)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    state.currentConversationId = data.id;
    state.chatHistory = (data.messages || []).map(m => ({
      role: m.role,
      content: m.content,
      citations: m.citations || [],
    }));

    // 重新渲染聊天界面
    const messages = $("#chat-messages");
    messages.innerHTML = "";
    if (state.chatHistory.length === 0) {
      messages.innerHTML = getWelcomeHTML();
    } else {
      for (const msg of state.chatHistory) {
        renderMessage(msg.role === "user" ? "user" : "ai", msg.content, msg.citations || []);
      }
    }
    renderConversationList();
    focusChatInput();
  } catch (e) {
    toast(`加载对话失败: ${e.message}`, "error");
  }
}

async function deleteConversation(convId) {
  if (!confirm("确认删除该对话？")) return;
  try {
    const res = await fetch(`/api/conversations/${encodeURIComponent(convId)}`, { method: "DELETE" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    if (state.currentConversationId === convId) {
      state.currentConversationId = null;
      state.chatHistory = [];
      resetChatUI();
    }
    await loadConversationList();
    toast("对话已删除", "success");
  } catch (e) {
    toast(`删除失败: ${e.message}`, "error");
  }
}

async function autoTitleConversation(convId, question) {
  // 用第一条消息的前 30 个字符作为标题
  const title = question.slice(0, 30) + (question.length > 30 ? "..." : "");
  try {
    await fetch(`/api/conversations/${encodeURIComponent(convId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    // 更新本地列表
    const conv = state.conversations.find(c => c.id === convId);
    if (conv) conv.title = title;
    renderConversationList();
  } catch { /* 标题更新失败不影响主流程 */ }
}

async function saveMessageToServer(convId, role, content, citations) {
  try {
    await fetch(`/api/conversations/${encodeURIComponent(convId)}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role, content, citations: citations || [] }),
    });
  } catch (e) {
    console.error("保存消息到服务端失败", e);
  }
}

function toggleHistoryPanel() {
  const panel = $("#chat-history-panel");
  if (!panel) return;
  state.historyPanelOpen = !state.historyPanelOpen;
  panel.classList.toggle("collapsed", !state.historyPanelOpen);
}

function resetChatUI() {
  $("#chat-messages").innerHTML = getWelcomeHTML();
}

function getWelcomeHTML() {
  return `
    <div class="chat-welcome">
      <div class="welcome-glow"></div>
      <div class="welcome-icon-wrap">
        <svg viewBox="0 0 48 48" fill="none" style="width:56px;height:56px">
          <defs>
            <linearGradient id="chatGrad" x1="0" y1="0" x2="48" y2="48">
              <stop offset="0%" stop-color="#6366f1"/>
              <stop offset="100%" stop-color="#3b82f6"/>
            </linearGradient>
          </defs>
          <rect width="48" height="48" rx="16" fill="url(#chatGrad)" opacity="0.12"/>
          <path d="M33 23a7.5 7.5 0 01-.7 3.2A7 7 0 0126 30a7.5 7.5 0 01-3.2-.7L18 31l1.7-4.8A7.5 7.5 0 0119 23 7 7 0 0124.5 17a7.5 7.5 0 013.2.7h.3A7 7 0 0133 23z" stroke="url(#chatGrad)" stroke-width="2" fill="none"/>
        </svg>
      </div>
      <h3>数据出境合规智能问答</h3>
      <p>基于知识库内容，为您提供专业的数据出境法规、政策、指南等合规咨询服务</p>
      <div class="welcome-hints">
        <button class="hint-chip" onclick="setQuestion('数据出境安全评估的适用情形有哪些？')">
          数据出境安全评估的适用情形
        </button>
        <button class="hint-chip" onclick="setQuestion('个人信息跨境传输需要满足什么条件？')">
          个人信息跨境传输条件
        </button>
        <button class="hint-chip" onclick="setQuestion('标准合同条款的签订要求是什么？')">
          标准合同条款签订要求
        </button>
      </div>
    </div>`;
}

// ==================== DOM 工具 ====================

const $ = (sel, ctx = document) => ctx.querySelector(sel);
const $$ = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];

// ==================== 视图路由 ====================

function navigate(view) {
  state.currentView = view;

  $$(".nav-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.view === view);
  });

  $$(".view").forEach((el) => {
    el.classList.toggle("active", el.id === `view-${view}`);
  });

  const labels = { dashboard: "仪表盘", knowledge: "知识库管理", chat: "智能问答", research: "深度研究" };
  const titleEl = $("#topbar-title");
  if (labels[view] && titleEl) titleEl.textContent = labels[view];

  if (view === "dashboard") loadDashboard();
  if (view === "knowledge") loadSources();
  if (view === "chat") focusChatInput();
  if (view === "research") focusResearchInput();
}

// ==================== Toast 通知 ====================

function toast(message, type = "info") {
  const container = $("#toast-container");
  const el = document.createElement("div");
  el.className = `toast toast-${type}`;
  const icons = { success: "✓", error: "✕", info: "ℹ" };
  el.innerHTML = `
    <span class="toast-icon">${icons[type] || icons.info}</span>
    <span class="toast-msg">${escapeHtml(message)}</span>
  `;
  container.appendChild(el);
  requestAnimationFrame(() => el.classList.add("show"));
  setTimeout(() => {
    el.classList.remove("show");
    el.addEventListener("transitionend", () => el.remove());
  }, 3500);
}

// ==================== API 封装 ====================

async function apiFetch(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || `HTTP ${res.status}`);
  }
  return data;
}

// ==================== 仪表盘 ====================

async function loadDashboard() {
  try {
    const health = await apiFetch("/health");
    state.totalChunks = health.chunks || 0;

    $("#stat-chunks").textContent = state.totalChunks.toLocaleString();
    const statusEl = $("#stat-status");

    const dot = $("#status-dot");
    const statusText = $("#sidebar-status-text");

    if (health.status === "initializing") {
      statusEl.textContent = "初始化中...";
      dot.className = "status-indicator checking";
      statusText.textContent = "正在初始化...";
      // 2 秒后重试
      setTimeout(() => { if (state.currentView === "dashboard") loadDashboard(); }, 2000);
      return;
    }

    statusEl.textContent = health.status === "ok" ? "正常运行" : "异常";

    if (health.status === "ok") {
      dot.className = "status-indicator online";
      statusText.textContent = "服务正常";
    } else {
      dot.className = "status-indicator offline";
      statusText.textContent = "服务异常";
    }

    await loadSourcesData();
    $("#stat-sources").textContent = state.sources.length;
  } catch (e) {
    console.error("仪表盘加载失败", e);
    const dot = $("#status-dot");
    const statusText = $("#sidebar-status-text");
    dot.className = "status-indicator offline";
    statusText.textContent = "连接失败";
  }
}

// ==================== 知识库管理 ====================

async function loadSourcesData() {
  try {
    const data = await apiFetch("/api/sources");
    state.sources = data.sources || [];
    state.totalChunks = data.total_chunks || 0;
    return state.sources;
  } catch (e) {
    console.error("加载知识源失败", e);
    return [];
  }
}

async function loadSources() {
  const list = $("#source-list");
  list.innerHTML = `<div class="loading-row"><div class="loading-spinner"></div><span>加载中...</span></div>`;

  await loadSourcesData();
  renderSourceList();

  $("#stat-kb-sources").textContent = state.sources.length;
  $("#stat-kb-chunks").textContent = state.totalChunks.toLocaleString();
}

function renderSourceList() {
  const list = $("#source-list");
  if (state.sources.length === 0) {
    list.innerHTML = `<div class="empty-state">
      <div class="empty-icon">📂</div>
      <p>知识库为空</p>
      <p style="font-size:12px;margin-top:4px">请上传文档或采集网页来构建知识库</p>
    </div>`;
    return;
  }

  list.innerHTML = state.sources
    .map(
      (s) => `
    <div class="source-item" data-name="${escapeAttr(s.source_name)}">
      <div class="source-icon">${sourceIcon(s.source_type)}</div>
      <div class="source-info">
        <div class="source-name" title="${escapeAttr(s.source_name)}">${escapeHtml(s.title || s.source_name)}</div>
        <div class="source-meta">
          <span class="badge badge-${s.source_type}">${s.source_type === "file" ? "文件" : "网页"}</span>
          ${s.category ? `<span class="badge badge-category">${escapeHtml(s.category)}</span>` : ""}
          <span class="chunk-count">${s.chunk_count} 片段</span>
          ${s.source_url ? `<a class="source-link" href="${escapeAttr(s.source_url)}" target="_blank" rel="noopener">查看原文 ↗</a>` : ""}
        </div>
      </div>
      <button class="btn-icon btn-delete" onclick="deleteSource('${escapeAttr(s.source_name)}')" title="删除">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/></svg>
      </button>
    </div>
  `
    )
    .join("");
}

function sourceIcon(type) {
  return type === "file"
    ? `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`
    : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 010 20M12 2a15.3 15.3 0 000 20"/></svg>`;
}

async function deleteSource(sourceName) {
  if (!confirm(`确认删除「${sourceName}」的所有知识片段？`)) return;

  try {
    const res = await apiFetch(`/api/sources/${encodeURIComponent(sourceName)}`, {
      method: "DELETE",
      headers: {},
    });
    toast(res.message || "删除成功", "success");
    await loadSources();
  } catch (e) {
    toast(`删除失败: ${e.message}`, "error");
  }
}

// ==================== 文件上传 ====================

function initUploadZone() {
  const zone = $("#upload-zone");
  const input = $("#file-input");

  zone.addEventListener("click", () => input.click());

  zone.addEventListener("dragover", (e) => {
    e.preventDefault();
    zone.classList.add("drag-over");
  });

  zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));

  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("drag-over");
    const file = e.dataTransfer.files[0];
    if (file) handleFileUpload(file);
  });

  input.addEventListener("change", () => {
    if (input.files[0]) handleFileUpload(input.files[0]);
    input.value = "";
  });
}

async function handleFileUpload(file) {
  const allowedExts = [".pdf", ".txt", ".docx"];
  const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
  if (!allowedExts.includes(ext)) {
    toast(`不支持的格式: ${ext}，请上传 PDF/TXT/DOCX`, "error");
    return;
  }

  const category = $("#upload-category").value.trim();
  const formData = new FormData();
  formData.append("file", file);

  const btn = $("#btn-upload");
  btn.disabled = true;
  btn.innerHTML = `<div class="loading-spinner" style="width:16px;height:16px;border-width:2px"></div> 上传中...`;
  setUploadProgress(true);

  try {
    const url = category
      ? `/api/ingest/file?category=${encodeURIComponent(category)}`
      : "/api/ingest/file";

    const res = await fetch(url, { method: "POST", body: formData });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

    toast(`已导入 ${data.chunk_count} 个片段：${file.name}`, "success");
    await loadSources();
  } catch (e) {
    toast(`上传失败: ${e.message}`, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px;height:16px"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg> 上传文档`;
    setUploadProgress(false);
  }
}

function setUploadProgress(active) {
  const zone = $("#upload-zone");
  zone.classList.toggle("uploading", active);
}

// ==================== 网页采集 ====================

async function ingestWeb() {
  const urlInput = $("#web-url");
  const categoryInput = $("#web-category");
  const url = urlInput.value.trim();

  if (!url) {
    toast("请输入网页 URL", "error");
    urlInput.focus();
    return;
  }

  const btn = $("#btn-ingest-web");
  btn.disabled = true;
  btn.innerHTML = `<div class="loading-spinner" style="width:16px;height:16px;border-width:2px"></div> 采集中...`;

  try {
    const data = await apiFetch("/api/ingest/web", {
      method: "POST",
      body: JSON.stringify({ url, category: categoryInput.value.trim() }),
    });
    toast(`采集成功，导入 ${data.chunk_count} 个片段`, "success");
    urlInput.value = "";
    await loadSources();
  } catch (e) {
    toast(`采集失败: ${e.message}`, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px;height:16px"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z"/></svg> 采集网页`;
  }
}

// ==================== 聊天功能 ====================

function focusChatInput() {
  setTimeout(() => $("#chat-input")?.focus(), 100);
}

function renderMessage(role, content, citations = []) {
  const messages = $("#chat-messages");
  const div = document.createElement("div");
  div.className = `message message-${role}`;

  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.textContent = role === "ai" ? "AI" : "我";
  div.appendChild(avatar);

  const body = document.createElement("div");
  body.className = "message-body";

  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.innerHTML = formatAnswer(content);
  body.appendChild(bubble);

  if (citations && citations.length > 0) {
    const citeDiv = document.createElement("div");
    citeDiv.className = "citations";
    citeDiv.innerHTML = `
      <div class="citations-title">参考来源</div>
      ${citations
        .map(
          (c, i) => `
        <div class="citation-item">
          <span class="citation-num">[${i + 1}]</span>
          <div class="citation-body">
            <div class="citation-title">${escapeHtml(c.title || c.source_name)}</div>
            ${c.source_url ? `<a href="${escapeAttr(c.source_url)}" target="_blank" rel="noopener" class="citation-link">${escapeHtml(c.source_url)}</a>` : ""}
            ${c.text_snippet ? `<div class="citation-snippet">${escapeHtml(c.text_snippet.slice(0, 150))}...</div>` : ""}
          </div>
        </div>
      `
        )
        .join("")}
    `;
    body.appendChild(citeDiv);
  }

  div.appendChild(body);
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
  return div;
}

function showTypingIndicator() {
  const messages = $("#chat-messages");
  const div = document.createElement("div");
  div.id = "typing-indicator";
  div.className = "message message-ai";

  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.textContent = "AI";
  div.appendChild(avatar);

  const body = document.createElement("div");
  body.className = "message-body";
  body.innerHTML = `<div class="message-bubble typing-bubble"><span></span><span></span><span></span></div>`;
  div.appendChild(body);

  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
}

function removeTypingIndicator() {
  $("#typing-indicator")?.remove();
}

function formatAnswer(text) {
  if (typeof marked !== 'undefined') {
    const html = marked.parse(text);
    return html
      .replace(/\[来源(\d+)\]/g, '<span class="ref-tag">[来源$1]</span>')
      .replace(/\[(\d+)\]/g, '<span class="ref-tag">[$1]</span>');
  }
  // fallback: plain text
  return '<p>' + escapeHtml(text).replace(/\n/g, '<br>') + '</p>';
}

async function sendMessage() {
  const input = $("#chat-input");
  const question = input.value.trim();
  if (!question || state.isLoading) return;

  state.isLoading = true;
  input.value = "";
  autoResizeTextarea(input);
  $("#btn-send").disabled = true;

  // 如果没有当前对话，自动创建一个
  if (!state.currentConversationId) {
    try {
      const res = await fetch("/api/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: question.slice(0, 30) + (question.length > 30 ? "..." : "") }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      state.currentConversationId = data.id;
      loadConversationList();  // 不 await，后台刷新侧栏
    } catch (e) {
      toast(`创建对话失败: ${e.message}`, "error");
      state.isLoading = false;
      $("#btn-send").disabled = false;
      return;
    }
  }

  const convId = state.currentConversationId;
  const isFirstMessage = state.chatHistory.length === 0;

  // 清除欢迎屏
  const welcome = $(".chat-welcome");
  if (welcome) welcome.remove();

  renderMessage("user", question);
  showTypingIndicator();

  // 保存用户消息到服务端
  saveMessageToServer(convId, "user", question, []);
  if (isFirstMessage) {
    autoTitleConversation(convId, question);
  }

  const category = $("#chat-category").value.trim();
  const topK = parseInt($("#chat-topk").value) || 5;

  try {
    const res = await fetch("/api/ask/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, category, top_k: topK }),
    });

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `HTTP ${res.status}`);
    }

    removeTypingIndicator();

    // 创建 AI 消息气泡用于流式填充
    const aiMsg = renderStreamMessage();
    let fullAnswer = "";
    let citations = [];
    let hasEnoughContext = true;

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split("\n");
      buffer = lines.pop(); // 保留未完成的行

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const jsonStr = line.slice(6);
        let evt;
        try { evt = JSON.parse(jsonStr); } catch { continue; }

        if (evt.type === "token") {
          fullAnswer += evt.content;
          updateStreamBubble(aiMsg, fullAnswer);
        } else if (evt.type === "citations") {
          citations = evt.data || [];
          hasEnoughContext = evt.has_enough_context !== false;
        } else if (evt.type === "error") {
          throw new Error(evt.message);
        }
        // "done" — 流结束
      }
    }

    // 流结束后，用完整内容（含引用）重新渲染
    finalizeStreamMessage(aiMsg, fullAnswer, citations);

    state.chatHistory.push({ role: "user", content: question });
    state.chatHistory.push({ role: "ai", content: fullAnswer, citations });
    saveChatHistory();

    // 保存 AI 回复到服务端
    saveMessageToServer(convId, "ai", fullAnswer, citations);
    // 刷新对话列表以更新排序
    loadConversationList();

    if (!hasEnoughContext) {
      toast("知识库中未找到足够相关内容，回答可能不完整", "info");
    }
  } catch (e) {
    removeTypingIndicator();
    renderMessage("ai", `抱歉，查询出现问题：${e.message}`);
    toast(`问答失败: ${e.message}`, "error");
  } finally {
    state.isLoading = false;
    $("#btn-send").disabled = false;
  }
}

/** 创建一个空的 AI 流式消息气泡，返回容器 DOM */
function renderStreamMessage() {
  const messages = $("#chat-messages");
  const div = document.createElement("div");
  div.className = "message message-ai";

  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.textContent = "AI";
  div.appendChild(avatar);

  const body = document.createElement("div");
  body.className = "message-body";

  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.innerHTML = '<span class="stream-cursor">▍</span>';
  body.appendChild(bubble);

  div.appendChild(body);
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
  return div;
}

/** 更新流式气泡内容 */
function updateStreamBubble(msgDiv, text) {
  const bubble = $(".message-bubble", msgDiv);
  bubble.innerHTML = formatAnswer(text) + '<span class="stream-cursor">▍</span>';
  const messages = $("#chat-messages");
  messages.scrollTop = messages.scrollHeight;
}

/** 流结束后，去掉光标，追加引用 */
function finalizeStreamMessage(msgDiv, text, citations) {
  const bubble = $(".message-bubble", msgDiv);
  bubble.innerHTML = formatAnswer(text);

  if (citations && citations.length > 0) {
    const body = $(".message-body", msgDiv);
    const citeDiv = document.createElement("div");
    citeDiv.className = "citations";
    citeDiv.innerHTML = `
      <div class="citations-title">参考来源</div>
      ${citations
        .map(
          (c, i) => `
        <div class="citation-item">
          <span class="citation-num">[${i + 1}]</span>
          <div class="citation-body">
            <div class="citation-title">${escapeHtml(c.title || c.source_name)}</div>
            ${c.source_url ? `<a href="${escapeAttr(c.source_url)}" target="_blank" rel="noopener" class="citation-link">${escapeHtml(c.source_url)}</a>` : ""}
            ${c.text_snippet ? `<div class="citation-snippet">${escapeHtml(c.text_snippet.slice(0, 150))}...</div>` : ""}
          </div>
        </div>
      `
        )
        .join("")}
    `;
    body.appendChild(citeDiv);
  }
}

function clearChat() {
  // "清空对话" 变为 "新建对话"
  state.currentConversationId = null;
  state.chatHistory = [];
  resetChatUI();
  renderConversationList();
  saveChatHistory();
  focusChatInput();
}

function autoResizeTextarea(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 140) + "px";
}

/** 快捷提问模板：点击 hint-chip 时填入问题并自动发送 */
function setQuestion(text) {
  const input = $("#chat-input");
  if (!input || state.isLoading) return;
  input.value = text;
  autoResizeTextarea(input);
  sendMessage();
}

// ==================== 深度研究功能 (Agentic RAG) ====================

function focusResearchInput() {
  setTimeout(() => $("#research-input")?.focus(), 100);
}

const STEP_ICONS = {
  classify: "🧠",
  transform: "🔍",
  retrieve_1: "📚",
  retrieve_2: "📚",
  retrieve_3: "📚",
  evidence_1: "🔬",
  evidence_2: "🔬",
  evidence_3: "🔬",
  evidence_check_1: "🔬",
  evidence_check_2: "🔬",
  evidence_check_3: "🔬",
  web_search: "🌐",
  refuse: "⚠️",
  generate: "✍️",
};

function renderResearchStep(step) {
  const stepsEl = $("#research-steps");
  const existingStep = $(`[data-step="${step.step}"]`, stepsEl);

  const icon = STEP_ICONS[step.step] || "⚙️";
  const statusClass = step.status === "done" ? "step-done" : "step-running";
  const statusIcon = step.status === "done" ? "✓" : '<div class="step-spinner"></div>';

  const html = `
    <div class="research-step ${statusClass}" data-step="${step.step}">
      <span class="step-icon">${icon}</span>
      <span class="step-text">${escapeHtml(step.description)}</span>
      <span class="step-status">${statusIcon}</span>
    </div>
  `;

  if (existingStep) {
    existingStep.outerHTML = html;
  } else {
    stepsEl.insertAdjacentHTML("beforeend", html);
  }
}

function renderMarkdown(text) {
  if (typeof marked !== 'undefined') {
    return marked.parse(text)
      .replace(/\[来源(\d+)\]/g, '<span class="ref-tag">[来源$1]</span>')
      .replace(/\[(\d+)\]/g, '<span class="ref-tag">[$1]</span>');
  }
  return '<p>' + escapeHtml(text).replace(/\n/g, '<br>') + '</p>';
}

async function startResearch() {
  const input = $("#research-input");
  const query = input.value.trim();
  if (!query || state.isLoading) return;

  state.isLoading = true;
  const btn = $("#btn-research");
  btn.disabled = true;
  btn.innerHTML = '<div class="loading-spinner" style="width:16px;height:16px;border-width:2px"></div> 研究中...';

  const progressEl = $("#research-progress");
  const resultEl = $("#research-result");
  progressEl.style.display = "block";
  resultEl.style.display = "none";
  $("#research-steps").innerHTML = "";
  $("#research-content").innerHTML = "";
  $("#research-citations").innerHTML = "";

  const mode = $("#research-mode").value;
  const topK = parseInt($("#research-topk").value) || 8;
  const enableWeb = $("#research-web").checked;

  try {
    const res = await fetch("/api/research/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        mode,
        top_k: topK,
        enable_web_search: enableWeb,
      }),
    });

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `HTTP ${res.status}`);
    }

    let fullContent = "";
    let resultData = null;

    resultEl.style.display = "block";
    const contentEl = $("#research-content");
    contentEl.innerHTML = '<span class="stream-cursor">▍</span>';

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split("\n");
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const jsonStr = line.slice(6);
        let evt;
        try { evt = JSON.parse(jsonStr); } catch { continue; }

        if (evt.type === "step") {
          renderResearchStep(evt.data);
        } else if (evt.type === "token") {
          fullContent += evt.content;
          contentEl.innerHTML = renderMarkdown(fullContent) + '<span class="stream-cursor">▍</span>';
          resultEl.scrollIntoView({ behavior: "smooth", block: "end" });
        } else if (evt.type === "result") {
          resultData = evt.data;
        } else if (evt.type === "error") {
          throw new Error(evt.message);
        }
      }
    }

    contentEl.innerHTML = renderMarkdown(fullContent);

    if (resultData) {
      const metaEl = $("#research-meta");
      const metaParts = [];
      if (resultData.retrieval_rounds) metaParts.push(`${resultData.retrieval_rounds} 轮检索`);
      if (resultData.total_docs) metaParts.push(`${resultData.total_docs} 篇参考`);
      if (resultData.web_search_used) metaParts.push("含联网搜索");
      if (resultData.word_count) metaParts.push(`${resultData.word_count} 字`);
      metaEl.innerHTML = metaParts
        .map((p) => `<span class="meta-badge">${escapeHtml(p)}</span>`)
        .join("");

      if (resultData.transformed_queries && resultData.transformed_queries.length > 1) {
        const queryInfo = document.createElement("div");
        queryInfo.className = "research-queries-info";
        queryInfo.innerHTML = `
          <div class="queries-title">Agent 生成的检索查询</div>
          <div class="queries-list">
            ${resultData.transformed_queries.map((q) => `<span class="query-chip">${escapeHtml(q)}</span>`).join("")}
          </div>
        `;
        contentEl.insertBefore(queryInfo, contentEl.firstChild);
      }

      if (resultData.citations && resultData.citations.length > 0) {
        const citationsEl = $("#research-citations");
        citationsEl.innerHTML = `
          <div class="citations-title">参考来源</div>
          ${resultData.citations
            .map(
              (c, i) => `
            <div class="citation-item">
              <span class="citation-num">[${c.index || i + 1}]</span>
              <div class="citation-body">
                <div class="citation-title">${escapeHtml(c.title || c.source_name)}</div>
                <span class="badge badge-${c.source_type}">${c.source_type === "file" ? "文件" : c.source_type === "web_search" ? "联网" : "网页"}</span>
                ${c.source_url ? `<a href="${escapeAttr(c.source_url)}" target="_blank" rel="noopener" class="citation-link">${escapeHtml(c.source_url)}</a>` : ""}
                ${c.text_snippet ? `<div class="citation-snippet">${escapeHtml(c.text_snippet.slice(0, 200))}...</div>` : ""}
              </div>
            </div>
          `
            )
            .join("")}
        `;
      }
    }

    toast("深度研究完成", "success");
  } catch (e) {
    toast(`研究失败: ${e.message}`, "error");
    const contentEl = $("#research-content");
    contentEl.innerHTML = `<p style="color: var(--color-error)">研究过程中出现错误：${escapeHtml(e.message)}</p>`;
  } finally {
    state.isLoading = false;
    btn.disabled = false;
    btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px;height:16px"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg> 开始研究`;
  }
}

// ==================== 工具函数 ====================

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapeAttr(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// ==================== 事件绑定 ====================

function bindEvents() {
  // 导航
  $$(".nav-item").forEach((el) => {
    if (el.dataset.view) {
      el.addEventListener("click", () => navigate(el.dataset.view));
    }
  });

  // 聊天输入
  const chatInput = $("#chat-input");
  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  chatInput.addEventListener("input", () => autoResizeTextarea(chatInput));

  $("#btn-send").addEventListener("click", sendMessage);
  const btnClearChat = $("#btn-clear-chat");
  if (btnClearChat) btnClearChat.addEventListener("click", clearChat);

  // 对话历史面板
  const btnNewChat = $("#btn-new-chat");
  if (btnNewChat) btnNewChat.addEventListener("click", createNewConversation);
  const btnToggleHistory = $("#btn-toggle-history");
  if (btnToggleHistory) btnToggleHistory.addEventListener("click", toggleHistoryPanel);

  // 网页采集
  $("#btn-ingest-web").addEventListener("click", ingestWeb);
  $("#web-url").addEventListener("keydown", (e) => {
    if (e.key === "Enter") ingestWeb();
  });

  // 文件上传
  initUploadZone();

  // 深度研究
  const researchBtn = $("#btn-research");
  if (researchBtn) researchBtn.addEventListener("click", startResearch);
  const researchInput = $("#research-input");
  if (researchInput) {
    researchInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        startResearch();
      }
    });
  }

  // 移动端侧边栏
  const menuBtn = $("#menu-toggle");
  const sidebar = $("#sidebar");
  if (menuBtn) {
    menuBtn.addEventListener("click", () => sidebar.classList.toggle("open"));
    document.addEventListener("click", (e) => {
      if (!sidebar.contains(e.target) && !menuBtn.contains(e.target)) {
        sidebar.classList.remove("open");
      }
    });
  }
}

// ==================== 初始化 ====================

async function init() {
  // 设置初始状态
  const dot = $("#status-dot");
  if (dot) dot.className = "status-indicator checking";

  bindEvents();
  loadChatHistory();
  navigate("dashboard");
}

document.addEventListener("DOMContentLoaded", init);
