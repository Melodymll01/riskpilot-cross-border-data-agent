import { getUser, onUserChange } from "./auth.js";
import { documents as documentsApi } from "./api.js";

const entry = document.querySelector("#btn-knowledge-base");
const view = document.querySelector("#knowledge-base-view");
const messages = document.querySelector("#messages");
const composer = document.querySelector(".composer");
const chatEyebrow = document.querySelector(".chat-eyebrow");
const chatTitle = document.querySelector("#chat-title");
const list = document.querySelector("#knowledge-base-list");
const status = document.querySelector("#knowledge-base-status");
const fileInput = document.querySelector("#knowledge-base-file");
const refreshButton = document.querySelector("#knowledge-base-refresh");
const addButton = document.querySelector("#knowledge-base-add");
const addCloseButton = document.querySelector("#knowledge-base-add-close");
const ingestPanel = document.querySelector("#knowledge-base-ingest-panel");
const categoryInput = document.querySelector("#knowledge-base-category");
const urlInput = document.querySelector("#knowledge-base-url");
const webSubmit = document.querySelector("#knowledge-base-web-submit");
let sourceMode = "file";
const stats = {
  documents: document.querySelector('[data-kb-stat="documents"]'),
  chunks: document.querySelector('[data-kb-stat="chunks"]'),
};
let generation = 0;

function isAdmin(user = getUser()) {
  return Boolean(user?.is_admin);
}

function setEntryVisibility(user) {
  if (entry) entry.classList.toggle("hidden", !isAdmin(user));
  if (!isAdmin(user)) closeKnowledgeBase();
}

export function closeKnowledgeBase() {
  view?.classList.add("hidden");
  messages?.classList.remove("hidden");
  composer?.classList.remove("hidden");
  if (chatEyebrow) chatEyebrow.textContent = "合规研判会话";
  if (chatTitle) chatTitle.textContent = "新对话";
  entry?.removeAttribute("aria-current");
}

function openKnowledgeBase() {
  if (!isAdmin()) return;
  document.body.classList.remove("sidebar-open");
  view?.classList.remove("hidden");
  messages?.classList.add("hidden");
  composer?.classList.add("hidden");
  if (chatEyebrow) chatEyebrow.textContent = "管理员工作台";
  if (chatTitle) chatTitle.textContent = "知识库";
  entry?.setAttribute("aria-current", "page");
  void refresh();
}

function setStatus(message = "", state = "") {
  if (!status) return;
  status.textContent = message;
  status.dataset.state = state;
}

function formatOwner(ownerId) {
  return ownerId ? "私有材料" : "公共知识库";
}

function renderDocuments(items) {
  list.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "kb-empty";
    empty.innerHTML = '<svg aria-hidden="true" fill="none" viewBox="0 0 24 24"><path d="M4 6.5A2.5 2.5 0 0 1 6.5 4H20v14H6.5A2.5 2.5 0 0 0 4 20.5v-14Z"/><path d="M8 8h8M8 11.5h6"/></svg><strong>公共知识库还是空的</strong><span>上传官方法规或经过审核的合规材料后，它们才会参与公共检索。</span>';
    list.append(empty);
    return;
  }

  const header = document.createElement("div");
  header.className = "kb-document-header";
  header.innerHTML = "<span>材料</span><span>类型</span><span>片段</span><span>范围</span><span></span>";
  list.append(header);
  for (const item of items) {
    const row = document.createElement("article");
    row.className = "kb-document-row";
    const title = item.title || item.source_name;
    const source = safeSourceLink(item.source_url);
    row.innerHTML = `
      <div class="kb-document-name"><div class="kb-document-title"><strong>${escapeHtml(title)}</strong><span class="kb-category-tag">${escapeHtml(item.category || "未分类")}</span></div><small>${escapeHtml(item.source_name)} · ${escapeHtml(source)}</small></div>
      <span class="kb-type">${item.source_type === "web" ? "网页" : "文件"}</span>
      <strong class="kb-chunks">${item.chunk_count}</strong>
      <span class="kb-owner">${formatOwner(item.owner_id)}</span>
      <button class="btn btn-ghost kb-delete" type="button">删除</button>
    `;
    // source is rendered as a text fallback except for the explicitly validated URL anchor.
    const sourceSmall = row.querySelector(".kb-document-name small");
    sourceSmall.innerHTML = `${escapeHtml(item.source_name)} · ${source}`;
    row.querySelector(".kb-delete").addEventListener("click", () => removeDocument(item));
    list.append(row);
  }
}

async function refresh() {
  if (!isAdmin()) return;
  const current = ++generation;
  setStatus("正在读取公共知识库…", "busy");
  try {
    const [result, summary] = await Promise.all([
      documentsApi.list("public"),
      documentsApi.stats("public"),
    ]);
    if (current !== generation) return;
    stats.documents.textContent = String(summary.document_count);
    stats.chunks.textContent = String(summary.chunk_count);
    renderDocuments(result.documents || []);
    setStatus(`已加载 ${result.documents?.length || 0} 份公共材料`, "ok");
  } catch (error) {
    if (current !== generation) return;
    stats.documents.textContent = "—";
    stats.chunks.textContent = "—";
    list.replaceChildren();
    setStatus(`知识库读取失败：${error.message}`, "error");
  }
}

async function removeDocument(document) {
  if (!window.confirm(`确认从公共知识库删除“${document.title || document.source_name}”？删除后将不再参与检索。`)) return;
  setStatus("正在删除…", "busy");
  try {
    await documentsApi.remove(document.source_name);
    await refresh();
  } catch (error) {
    setStatus(`删除失败：${error.message}`, "error");
  }
}

async function uploadPublicFile(file) {
  setStatus(`正在处理“${file.name}”…`, "busy");
  try {
    const result = await documentsApi.uploadPublicFile(file, categoryInput.value);
    if (!result.success) throw new Error(result.message || "入库失败");
    fileInput.value = "";
    setStatus(`已入库：${file.name}，生成 ${result.chunk_count} 个文本片段`, "ok");
    await refresh();
  } catch (error) {
    setStatus(`入库失败：${error.message}`, "error");
    fileInput.value = "";
  }
}

async function uploadPublicWeb() {
  const url = urlInput.value.trim();
  if (!url) {
    setStatus("请先输入网页 URL", "error");
    return;
  }
  setStatus(`正在采集网页…`, "busy");
  webSubmit.disabled = true;
  try {
    const result = await documentsApi.uploadPublicWeb(url, categoryInput.value);
    if (!result.success) throw new Error(result.message || "网页入库失败");
    urlInput.value = "";
    setStatus(`已入库网页，生成 ${result.chunk_count} 个文本片段`, "ok");
    await refresh();
  } catch (error) {
    setStatus(`网页入库失败：${error.message}`, "error");
  } finally {
    webSubmit.disabled = false;
  }
}

function escapeHtml(value) {
  return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function escapeAttribute(value) {
  return escapeHtml(value).replace(/'/g, "&#39;");
}

function safeSourceLink(value) {
  if (!value) return "无来源链接";
  try {
    const url = new URL(value, window.location.origin);
    if (!["http:", "https:"].includes(url.protocol)) return "无来源链接";
    return `<a href="${escapeAttribute(url.href)}" target="_blank" rel="noopener noreferrer">来源链接</a>`;
  } catch {
    return "无来源链接";
  }
}

entry?.addEventListener("click", () => {
  openKnowledgeBase();
});
document.querySelector("#knowledge-base-close")?.addEventListener("click", closeKnowledgeBase);
refreshButton?.addEventListener("click", () => void refresh());
addButton?.addEventListener("click", () => ingestPanel.classList.toggle("hidden"));
addCloseButton?.addEventListener("click", () => ingestPanel.classList.add("hidden"));
document.querySelectorAll(".kb-ingest-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    sourceMode = tab.dataset.source;
    document.querySelectorAll(".kb-ingest-tab").forEach((item) => item.classList.toggle("active", item === tab));
    document.querySelectorAll("[data-source-panel]").forEach((panel) => panel.classList.toggle("hidden", panel.dataset.sourcePanel !== sourceMode));
  });
});
fileInput?.addEventListener("change", (event) => {
  const file = event.target.files?.[0];
  if (file) void uploadPublicFile(file);
});
webSubmit?.addEventListener("click", () => void uploadPublicWeb());
onUserChange((user) => {
  generation += 1;
  setEntryVisibility(user);
});
setEntryVisibility(getUser());
