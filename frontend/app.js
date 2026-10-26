/** Conversation entry point: identity, history, composer and attachments. */
import "./memory.js?v=20261026-v2";
import { closeKnowledgeBase } from "./knowledge_base.js?v=20261019-kb4";
import { displayLabels, ensureSession, logout, onUserChange } from "./auth.js";
import { sendMessage, stopGeneration, newConversation, loadTask, onTaskCreated, onTaskUpdated, onConversationReset, onAttachmentSelectionChanged, getCurrentTaskId } from "./chat.js?v=20261023-flow7";
import { refresh as refreshTasks, onSelect as onTaskSelect, setActive as setActiveTask } from "./tasks.js";
import { health } from "./api.js";
const $ = (selector) => document.querySelector(selector);
const welcome = $("#welcome").innerHTML;
let selectedAttachmentIds = [];
let attachmentSelectionDirty = false;
let uploadGeneration = 0;
let uploading = false;
let riskMode = false;
function taskIdFromUrl() {
  return new URL(window.location.href).searchParams.get("task_id");
}
function setTaskUrl(taskId) {
  const url = new URL(window.location.href);
  if (taskId) url.searchParams.set("task_id", taskId);
  else url.searchParams.delete("task_id");
  window.history.replaceState({}, "", url);
}
function setSidebarOpen(open) {
  document.body.classList.toggle("sidebar-open", open);
  $("#btn-sidebar-toggle").setAttribute("aria-expanded", String(open));
}
function clearAttachment({ dirty = true } = {}) {
  uploadGeneration += 1;
  selectedAttachmentIds = [];
  attachmentSelectionDirty = dirty;
  uploading = false;
  $("#attachment-input").value = "";
  $("#attachment-status").textContent = "";
  $("#attachment-clear").hidden = true;
}
function setRiskMode(enabled) {
  riskMode = enabled;
  const button = $("#btn-risk-analysis");
  button.classList.toggle("active", enabled);
  button.setAttribute("aria-pressed", String(enabled));
  $("#composer-input").placeholder = enabled
    ? "输入要评估的合规问题，或描述需要分析的企业材料…"
    : "描述你的合规问题，或针对已添加的材料提问…";
}
onUserChange((user) => {
  const labels = displayLabels(user);
  $("#user-avatar").textContent = labels.initial;
  $("#user-name").textContent = labels.name;
  $("#user-provider").textContent = labels.provider;
  const githubLogin = $("#btn-github-login");
  githubLogin.hidden = user?.provider === "github";
  githubLogin.disabled = false;
  githubLogin.textContent = "使用 GitHub 登录";
});
onTaskSelect(async (id) => {
  closeKnowledgeBase();
  setRiskMode(false);
  setSidebarOpen(false);
  clearAttachment({ dirty: false });
  setTaskUrl(id);
  setActiveTask(id);
  await loadTask(id);
});
onTaskCreated(async (id) => { setTaskUrl(id); await refreshTasks(); setActiveTask(id); });
onTaskUpdated(async () => { await refreshTasks(); setActiveTask(getCurrentTaskId()); });
onConversationReset(() => { $("#welcome").innerHTML = welcome; });
onAttachmentSelectionChanged(async (documentIds) => {
  selectedAttachmentIds = [...documentIds];
  attachmentSelectionDirty = false;
  if (!documentIds.length) {
    $("#attachment-status").textContent = "";
    $("#attachment-clear").hidden = true;
    return;
  }
  try {
    const responses = await Promise.all(documentIds.map(id => fetch(`/api/v2/files/${encodeURIComponent(id)}`, {credentials: "include"})));
    const details = await Promise.all(responses.map(response => response.json()));
    $("#attachment-status").textContent = details.map(detail => `${detail.document.logical_name} · ${detail.document.status === "ready" ? "可用" : detail.document.status}`).join("；");
    $("#attachment-clear").hidden = false;
  } catch {
    $("#attachment-status").textContent = "附件状态恢复失败";
  }
});
$("#btn-new-task").addEventListener("click", () => {
  closeKnowledgeBase();
  setRiskMode(false);
  setSidebarOpen(false);
  clearAttachment({ dirty: false });
  setTaskUrl(null);
  newConversation();
  setActiveTask(null);
});
$("#btn-sidebar-toggle").addEventListener("click", () => setSidebarOpen(true));
$("#btn-sidebar-close").addEventListener("click", () => setSidebarOpen(false));
$("#sidebar-backdrop").addEventListener("click", () => setSidebarOpen(false));
$("#btn-user-menu").addEventListener("click", () => $("#user-menu").classList.toggle("hidden"));
$("#btn-github-login").addEventListener("click", async () => {
  $("#btn-github-login").textContent = "正在跳转 GitHub…";
});
$("#btn-logout").addEventListener("click", async () => {
  clearAttachment({ dirty: false }); setTaskUrl(null); await logout(); newConversation(); await refreshTasks();
});
$("#attachment-clear").addEventListener("click", clearAttachment);
$("#attachment-input").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  const generation = ++uploadGeneration;
  uploading = true;
  const status = $("#attachment-status");
  $("#attachment-clear").hidden = true;
  status.textContent = `${file.name} · 正在准备为问题材料…`;
  try {
    const form = new FormData(); form.append("file", file);
    let response = await fetch("/api/v2/files", {method: "POST", credentials: "include", body: form});
    let result = await response.json();
    if (!response.ok) throw new Error(result.message || "上传失败");
    const id = result.document.document_id;
    for (let poll = 0; poll < 300; poll++) {
      if (generation !== uploadGeneration) return;
      if (result.document.status === "ready") {
        selectedAttachmentIds = [id];
        attachmentSelectionDirty = true;
        status.textContent = `${file.name} · 已作为当前对话材料`;
        $("#attachment-clear").hidden = false;
        return;
      }
      if (["failed", "cancelled"].includes(result.document.status)) throw new Error(result.job?.error_message || "处理失败");
      status.textContent = `${file.name} · 处理中 ${Math.round((result.job?.progress || 0) * 100)}%`;
      await new Promise(resolve => setTimeout(resolve, 1000));
      response = await fetch(`/api/v2/files/${encodeURIComponent(id)}`, {credentials: "include"});
      result = await response.json();
      if (!response.ok) throw new Error(result.message || "状态查询失败");
    }
    throw new Error("处理状态等待超时，请稍后查询附件");
  } catch (error) {
    if (generation === uploadGeneration) status.textContent = error.message;
  } finally {
    if (generation === uploadGeneration) uploading = false;
  }
});
function submit(text) {
  if (uploading || (!text.trim() && !riskMode)) return;
  const attachmentOverride = attachmentSelectionDirty ? [...selectedAttachmentIds] : undefined;
  const question = text.trim();
  const message = riskMode
    ? question || "请评估所选材料的数据出境风险，列出风险项、依据和需要补充的信息。"
    : question;
  sendMessage(riskMode ? `请进行风险评估：${message}` : message, attachmentOverride);
  setRiskMode(false);
  attachmentSelectionDirty = false;
  $("#composer-input").value = "";
  $("#composer-input").style.height = "auto";
}
$("#composer-form").addEventListener("submit", (event) => { event.preventDefault(); submit($("#composer-input").value); });
$("#btn-stop").addEventListener("click", () => { void stopGeneration(); });
$("#btn-risk-analysis").addEventListener("click", () => setRiskMode(!riskMode));
$("#composer-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $("#composer-form").requestSubmit(); }
});
$("#composer-input").addEventListener("input", (event) => {
  event.target.style.height = "auto"; event.target.style.height = `${Math.min(event.target.scrollHeight, 200)}px`;
});
document.addEventListener("click", (event) => { const card = event.target.closest(".suggest-card"); if (card?.dataset.q) submit(card.dataset.q); });
(async () => {
  try {
    await ensureSession(); await refreshTasks();
    const result = await health.ready();
    $("#status-dot").dataset.state = result.status === "ok" ? "ok" : "err";
    $("#status-text").textContent = result.status === "ok" ? "就绪" : "未就绪";
    const restoredTaskId = taskIdFromUrl();
    if (restoredTaskId) {
      setActiveTask(restoredTaskId);
      await loadTask(restoredTaskId);
    }
    document.documentElement.dataset.appReady = "true";
  } catch (error) { $("#status-text").textContent = error.message; }
})();
