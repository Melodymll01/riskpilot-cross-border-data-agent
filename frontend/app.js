/**
 * app.js — 应用入口：组装 auth + chat + tasks + UI 事件。
 *
 * ES module；由 index.html `<script type="module">` 拉起。
 */

import { ensureSession, getUser, onUserChange, startGithubLogin, logout, displayLabels } from "./auth.js";
import { sendMessage, newConversation, loadTask, onTaskCreated, onTaskUpdated, getCurrentTaskId } from "./chat.js";
import { refresh as refreshTasks, onSelect as onTaskSelect, setActive as setActiveTask } from "./tasks.js";
import { health } from "./api.js";

const $ = (sel) => document.querySelector(sel);

// ─────────── 启动 ───────────
(async function bootstrap() {
  try {
    await ensureSession();
  } catch (err) {
    console.error("session bootstrap failed", err);
  }
  await healthCheck();
  await refreshTasks();

  bindUI();
})();

// ─────────── 健康检查（顶部状态点） ───────────
async function healthCheck() {
  const dot = $("#status-dot");
  const text = $("#status-text");
  try {
    const r = await health.ready();
    if (r.status === "ok") {
      dot.dataset.state = "ok";
      text.textContent = `就绪 · ${r.tools?.length || 0} 工具`;
    } else {
      dot.dataset.state = "err";
      text.textContent = "未就绪";
    }
  } catch {
    dot.dataset.state = "err";
    text.textContent = "服务异常";
  }
}

// ─────────── 用户信息渲染 ───────────
onUserChange((user) => {
  const { initial, name, provider } = displayLabels(user);
  $("#user-avatar").textContent = initial;
  $("#user-name").textContent = name;
  $("#user-provider").textContent = provider;
});

// ─────────── 任务列表 ↔ 聊天联动 ───────────
onTaskSelect(async (taskId) => {
  setActiveTask(taskId);
  await loadTask(taskId);
});

onTaskCreated(async (taskId, title) => {
  await refreshTasks();
  setActiveTask(taskId);
  $("#chat-title").textContent = title || "新对话";
});

onTaskUpdated(async () => {
  await refreshTasks();
  const cid = getCurrentTaskId();
  if (cid) setActiveTask(cid);
});

// ─────────── UI 事件绑定 ───────────
function bindUI() {
  // 新建任务
  $("#btn-new-task").addEventListener("click", () => {
    setActiveTask(null);
    newConversation();
    $("#composer-input").focus();
  });

  // 用户菜单
  $("#btn-user-menu").addEventListener("click", () => {
    $("#user-menu").classList.toggle("hidden");
  });
  $("#btn-github-login").addEventListener("click", async () => {
    $("#user-menu").classList.add("hidden");
    try { await startGithubLogin(); }
    catch (err) { alert(`GitHub 登录失败：${err.message}`); }
  });
  $("#btn-logout").addEventListener("click", async () => {
    $("#user-menu").classList.add("hidden");
    await logout();
    newConversation();
    await refreshTasks();
  });

  // 欢迎卡里的建议问题
  document.addEventListener("click", (ev) => {
    const card = ev.target.closest(".suggest-card");
    if (!card) return;
    const q = card.dataset.q;
    if (q) submit(q);
  });

  // 输入框
  const input = $("#composer-input");
  const form = $("#composer-form");

  // 自动增高
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 200) + "px";
  });

  // Enter 发送，Shift+Enter 换行
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      form.requestSubmit();
    }
  });

  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    submit(input.value);
  });

  function submit(text) {
    const t = (text || "").trim();
    if (!t) return;
    sendMessage(t);
    input.value = "";
    input.style.height = "auto";
    input.focus();
  }
}
