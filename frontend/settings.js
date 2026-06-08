/**
 * settings.js — 「记忆与隐私」模态：双开关 + 记忆管理面板 + 被遗忘权清除。
 *
 * 对接 Step 031a 后端：
 *   GET/PUT /api/v2/memory/settings   两个开关（参考保存的记忆 / 参考会话上下文）
 *   GET     /api/v2/memory/profile    L3 用户画像
 *   GET     /api/v2/memory/facts      生效的长期事实 + 容量上限
 *   DELETE  /api/v2/memory/facts/{id} 删单条长期事实（被遗忘权细粒度，Step 034）
 *   POST    /api/v2/memory/forget     主动遗忘（scope = memory | all）
 *
 * 交互约定：
 * - 开关采用「乐观切换」：先翻 UI 再落 PUT；失败回滚并提示。
 * - 清除是危险操作，必须二次确认；scope="all" 会连带删历史对话，
 *   通过 onMemoryCleared 通知 app.js 刷新任务列表 / 重置当前对话。
 */

import { memory, ApiError } from "./api.js";

const $ = (sel) => document.querySelector(sel);

let _mounted = false;
let _busy = false;
// app.js 订阅：记忆被清除后回调（scope = "memory" | "all"）
const _clearedListeners = [];

export function onMemoryCleared(cb) {
  if (typeof cb === "function") _clearedListeners.push(cb);
}

function emitCleared(scope) {
  for (const cb of _clearedListeners) {
    try { cb(scope); } catch (err) { console.error("onMemoryCleared listener failed", err); }
  }
}

/** 绑定一次性事件（幂等）。由 app.js 在启动时调用。 */
export function mount() {
  if (_mounted) return;
  _mounted = true;

  $("#memory-modal-close")?.addEventListener("click", close);
  $("#memory-modal")?.addEventListener("click", (ev) => {
    // 点遮罩空白处关闭；点模态内部不关
    if (ev.target === $("#memory-modal")) close();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !$("#memory-modal")?.classList.contains("hidden")) close();
  });

  $("#toggle-use-saved-memory")?.addEventListener("change", (ev) =>
    onToggle("use_saved_memory", ev.target.checked),
  );
  $("#toggle-reference-history")?.addEventListener("change", (ev) =>
    onToggle("reference_history", ev.target.checked),
  );

  $("#memory-refresh")?.addEventListener("click", () => loadManagement());
  $("#btn-forget-memory")?.addEventListener("click", () => onForget("memory"));
  $("#btn-forget-all")?.addEventListener("click", () => onForget("all"));
}

/** 打开模态并加载全部数据。 */
export async function open() {
  mount();
  const modal = $("#memory-modal");
  if (!modal) return;
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
  setHint("");
  await Promise.all([loadSettings(), loadManagement()]);
}

export function close() {
  const modal = $("#memory-modal");
  if (!modal) return;
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
  setStatus("", "idle");
}

// ─────────── 开关 ───────────

async function loadSettings() {
  try {
    const s = await memory.getSettings();
    setToggle("#toggle-use-saved-memory", s.use_saved_memory);
    setToggle("#toggle-reference-history", s.reference_history);
  } catch (err) {
    setStatus(`读取开关失败：${errMsg(err)}`, "err");
  }
}

async function onToggle(field, value) {
  if (_busy) return;
  _busy = true;
  setHint("保存中…");
  try {
    const updated = await memory.updateSettings({ [field]: value });
    setToggle("#toggle-use-saved-memory", updated.use_saved_memory);
    setToggle("#toggle-reference-history", updated.reference_history);
    setHint("已保存");
    setTimeout(() => setHint(""), 1500);
  } catch (err) {
    // 回滚 UI
    setToggle(
      field === "use_saved_memory" ? "#toggle-use-saved-memory" : "#toggle-reference-history",
      !value,
    );
    setHint(`保存失败：${errMsg(err)}`, true);
  } finally {
    _busy = false;
  }
}

// ─────────── 管理面板（画像 + 事实） ───────────

async function loadManagement() {
  await Promise.all([loadProfile(), loadFacts()]);
}

async function loadProfile() {
  const list = $("#profile-list");
  const empty = $("#profile-empty");
  const count = $("#profile-count");
  if (!list) return;
  try {
    const p = await memory.profile();
    const entries = Object.entries(p.facts || {});
    count.textContent = String(entries.length);
    list.innerHTML = "";
    if (entries.length === 0) {
      empty.hidden = false;
      return;
    }
    empty.hidden = true;
    for (const [k, v] of entries) {
      const li = document.createElement("li");
      li.className = "memory-item";
      const key = document.createElement("span");
      key.className = "memory-item-key";
      key.textContent = k;
      const val = document.createElement("span");
      val.className = "memory-item-val";
      val.textContent = formatValue(v);
      li.append(key, val);
      list.appendChild(li);
    }
  } catch (err) {
    count.textContent = "—";
    setStatus(`读取画像失败：${errMsg(err)}`, "err");
  }
}

async function loadFacts() {
  const list = $("#facts-list");
  const empty = $("#facts-empty");
  const count = $("#facts-count");
  if (!list) return;
  try {
    const r = await memory.facts();
    count.textContent = `${r.count} / ${r.cap}`;
    list.innerHTML = "";
    if (!r.facts || r.facts.length === 0) {
      empty.hidden = false;
      return;
    }
    empty.hidden = true;
    for (const f of r.facts) {
      const li = document.createElement("li");
      li.className = "memory-item memory-fact";
      const text = document.createElement("span");
      text.className = "memory-fact-text";
      text.textContent = f.text;
      li.appendChild(text);
      if (f.tags && f.tags.length) {
        const tags = document.createElement("span");
        tags.className = "memory-fact-tags";
        tags.textContent = f.tags.map((t) => `#${t}`).join(" ");
        li.appendChild(tags);
      }
      const del = document.createElement("button");
      del.type = "button";
      del.className = "memory-fact-del";
      del.title = "删除这条记忆";
      del.setAttribute("aria-label", "删除这条记忆");
      del.textContent = "×";
      del.addEventListener("click", () => onDeleteFact(f.fact_id, f.text));
      li.appendChild(del);
      list.appendChild(li);
    }
  } catch (err) {
    count.textContent = "—";
    setStatus(`读取事实失败：${errMsg(err)}`, "err");
  }
}

/** 删除单条长期事实（二次确认，删后刷新清单）。 */
async function onDeleteFact(factId, text) {
  if (_busy || !factId) return;
  const preview = (text || "").length > 40 ? `${text.slice(0, 40)}…` : text || "";
  if (!window.confirm(`确定删除这条记忆吗？\n\n“${preview}”\n\n此操作不可恢复。`)) return;
  _busy = true;
  setStatus("删除中…", "busy");
  try {
    await memory.deleteFact(factId);
    setStatus("已删除", "ok");
    await loadFacts();
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      setStatus("这条记忆已不存在", "ok");
      await loadFacts();
    } else {
      setStatus(`删除失败：${errMsg(err)}`, "err");
    }
  } finally {
    _busy = false;
  }
}

// ─────────── 危险操作：清除 ───────────

async function onForget(scope) {
  if (_busy) return;
  const isAll = scope === "all";
  const msg = isAll
    ? "确定要清空全部记忆与历史对话吗？\n\n这将删除你的用户画像、长期事实、会话摘要，以及所有历史任务对话。此操作不可恢复。"
    : "确定要清空记忆吗？\n\n这将删除用户画像、长期事实与会话摘要，但保留历史对话记录。此操作不可恢复。";
  if (!window.confirm(msg)) return;

  _busy = true;
  setStatus("清除中…", "busy");
  try {
    const r = await memory.forget(scope);
    setStatus(`已清除 ${r.total_deleted} 项记忆。`, "ok");
    await loadManagement();
    emitCleared(scope);
  } catch (err) {
    setStatus(`清除失败：${errMsg(err)}`, "err");
  } finally {
    _busy = false;
  }
}

// ─────────── 小工具 ───────────

function setToggle(sel, on) {
  const el = $(sel);
  if (el) el.checked = !!on;
}

function setHint(text, isErr = false) {
  const el = $("#memory-settings-hint");
  if (!el) return;
  el.textContent = text || "";
  el.hidden = !text;
  el.classList.toggle("is-err", isErr);
}

function setStatus(text, state = "idle") {
  const el = $("#memory-modal-status");
  if (!el) return;
  el.textContent = text || "";
  el.hidden = !text;
  el.dataset.state = state;
}

function formatValue(v) {
  if (Array.isArray(v)) return v.join("、");
  if (v && typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function errMsg(err) {
  if (err instanceof ApiError) return err.message;
  return err?.message || "未知错误";
}
