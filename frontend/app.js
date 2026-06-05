/**
 * app.js — 应用入口：组装 auth + chat + tasks + UI 事件。
 *
 * ES module；由 index.html `<script type="module">` 拉起。
 */

import { ensureSession, getUser, onUserChange, startGithubLogin, logout, displayLabels } from "./auth.js";
import {
  sendMessage,
  newConversation,
  loadTask,
  onTaskCreated,
  onTaskUpdated,
  onModeChanged,
  onConversationReset,
  getCurrentTaskId,
  getCurrentMode,
  setCurrentMode,
} from "./chat.js";
import { refresh as refreshTasks, onSelect as onTaskSelect, setActive as setActiveTask } from "./tasks.js";
import * as kb from "./kb.js";
import { health } from "./api.js";

const $ = (sel) => document.querySelector(sel);

// 当前主视图：chat | kb
let _currentView = "chat";

function switchView(view) {
  if (view !== "chat" && view !== "kb") return;
  if (view === "kb" && !getUser()?.is_admin) return;  // 守门：非 admin 不许进 KB
  _currentView = view;

  $("#chat-pane").classList.toggle("hidden", view !== "chat");
  $("#kb-pane").classList.toggle("hidden", view !== "kb");

  document.querySelectorAll(".side-nav-item").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === view);
  });

  if (view === "kb") {
    kb.mount();
    kb.refresh();
  }
}

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
  // 初始 UI 同步： Tab 高亮 + placeholder + welcome 卡
  applyMode(getCurrentMode());
  // chat 重置时由 app 重画 welcome
  onConversationReset((m) => renderWelcome(m));
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
  const { initial, name, provider, avatarUrl } = displayLabels(user);
  const avatar = $("#user-avatar");
  if (avatarUrl) {
    // 有真实头像（如 GitHub），用 <img> 填充；加 referrerpolicy 避免部分 CDN 防盗链 403
    avatar.innerHTML = "";
    const img = document.createElement("img");
    img.src = avatarUrl;
    img.alt = name;
    img.referrerPolicy = "no-referrer";
    img.onerror = () => { avatar.textContent = initial; };  // 加载失败回退首字母
    avatar.appendChild(img);
  } else {
    avatar.textContent = initial;
  }
  $("#user-name").textContent = name;
  $("#user-provider").textContent = provider;

  // admin 才能看到知识库入口
  applyAdminGate(!!user?.is_admin);
});

// 当前 admin 状态下隐藏 KB 视图
function applyAdminGate(isAdmin) {
  const navKb = $("#nav-kb");
  if (navKb) {
    navKb.classList.toggle("hidden", !isAdmin);
    navKb.hidden = !isAdmin;
  }
  // 非 admin 却停在 KB 视图：踢回对话
  if (!isAdmin && _currentView === "kb") {
    switchView("chat");
  }
}

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
  // 侧边栏主导航：对话 / 知识库
  document.querySelectorAll(".side-nav-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      const v = btn.dataset.view;
      if (v) switchView(v);
    });
  });

  // 新建任务
  $("#btn-new-task").addEventListener("click", () => {
    if (_currentView !== "chat") switchView("chat");
    setActiveTask(null);
    newConversation();
    $("#composer-input").focus();
  });

  // 模式 Tab 切换
  document.querySelectorAll(".mode-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      const m = btn.dataset.mode;
      if (!m) return;
      // 已有进行中的对话：切模式 = 自动开新对话（三种业务形态不共享上下文）
      if (getCurrentTaskId()) {
        setActiveTask(null);
        newConversation();
      }
      if (setCurrentMode(m)) {
        applyMode(m);
      } else {
        // mode 未变（点了已激活的 Tab）：仅刷新 UI
        applyMode(m);
      }
      $("#composer-input")?.focus();
    });
  });

  // 监听 chat.js 内部的 mode 变更（例如 loadTask 拉到历史 task 的 mode）
  onModeChanged((m) => applyMode(m));

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

// ─────────── 模式 UI 同步 ───────────
const MODE_LABELS = { qa: "知识问答", research: "深度研究", profile: "风险画像" };

const MODE_PRESETS = {
  qa: {
    placeholder: "问点合规问题…  (Enter 发送，Shift+Enter 换行)",
    title: "欢迎使用数智合规",
    desc: "面向数据出境法规的对话式合规助手。Agent 会自主调用工具检索法条、研判证据并给出可追溯的回答。",
    suggestions: [
      { icon: "📜", q: "个人信息出境的三种合规路径分别是什么？" },
      { icon: "🔍", q: "什么情况下必须申报数据出境安全评估？" },
      { icon: "📝", q: "标准合同备案需要准备哪些材料？" },
      { icon: "⚖️", q: "重要数据如何识别？依据是什么？" },
    ],
  },
  research: {
    placeholder: "描述你要研究的议题，Agent 会做多轮检索 + 长报告… (Enter 发送)",
    title: "深度研究模式",
    desc: "Agent 会做多轮检索、跨文档归纳，输出长篇结构化报告。耗时较长但更全面，适合综述、对比、方案设计类问题。",
    suggestions: [
      { icon: "🔬", q: "综述近三年个人信息出境监管框架的演进与关键节点" },
      { icon: "⚖️", q: "对比安全评估、标准合同、个保认证三条路径的适用场景与差异" },
      { icon: "🌐", q: "我公司将客户数据传输至 AWS 新加坡区，请给出完整合规方案与材料清单" },
      { icon: "📊", q: "盘点国内重要数据识别相关的部委文件与行业指引" },
    ],
  },
  profile: {
    placeholder: "输入一句话的场景描述或目标命题，例如“某公司向香港传输客户订单数据是否需安全评估”… (Enter 发送)",
    title: "风险画像（接口预留 · evidence-state 模型训练中）",
    desc: "描述你的出境场景或提出一句话的目标命题，未来会返回 evidence-state（supported / contradicted / not_disclosed）+ 证据 span + 解释。schema-evidence-risk-profiling 模型部署前，本 Tab 仍以普通对话形式回答。",
    suggestions: [
      { icon: "🛍️", q: "跨境电商日均向香港传输 5 万条订单数据，是否需要申报数据出境安全评估？" },
      { icon: "💊", q: "跨国药企将临床试验数据传至德国总部，这是否属于重要数据？需走哪条路径？" },
      { icon: "🚗", q: "智能汽车将行驶轨迹传至海外算法平台进行训练，该场景需要备案标准合同吗？" },
      { icon: "🏦", q: "金融机构应境外母公司风控查询请求向其提供客户交易记录，是否需个人信息保护认证？" },
    ],
  },
};

function applyMode(mode) {
  syncModeUI(mode);
  setPlaceholder(mode);
  renderWelcome(mode);
}

function syncModeUI(mode) {
  // Tab 高亮
  document.querySelectorAll(".mode-tab").forEach((btn) => {
    const on = btn.dataset.mode === mode;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-selected", on ? "true" : "false");
  });
  // chat-title 旁的 mode badge（qa 不显示）
  const badge = $("#chat-mode-badge");
  if (!badge) return;
  if (mode === "qa") {
    badge.hidden = true;
    badge.textContent = "";
    return;
  }
  badge.hidden = false;
  badge.dataset.mode = mode;
  badge.textContent = MODE_LABELS[mode] || mode;
}

function setPlaceholder(mode) {
  const input = $("#composer-input");
  if (!input) return;
  const preset = MODE_PRESETS[mode] || MODE_PRESETS.qa;
  input.placeholder = preset.placeholder;
}

function renderWelcome(mode) {
  const welcome = $("#welcome");
  if (!welcome) return;  // 无欢迎卡（已进入历史 task 或对话中），跳过
  const preset = MODE_PRESETS[mode] || MODE_PRESETS.qa;
  const cards = preset.suggestions
    .map(
      (s) => `
        <button class="suggest-card" data-q="${escapeAttr(s.q)}">
          <div class="suggest-icon">${s.icon}</div>
          <div class="suggest-text">${escapeHtml(s.q)}</div>
        </button>`
    )
    .join("");
  welcome.innerHTML = `
    <h1>${escapeHtml(preset.title)}</h1>
    <p>${escapeHtml(preset.desc)}</p>
    <div class="suggest-grid">${cards}</div>
  `;
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function escapeAttr(s) {
  return escapeHtml(s).replace(/"/g, "&quot;");
}
