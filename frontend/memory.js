import { getUser, onUserChange } from "./auth.js";

const $ = (id) => document.getElementById(id);
const dialog = $("memory-dialog");
const status = $("memory-status");
const list = $("memory-list");
const form = $("memory-form");
const content = $("memory-content");
const kind = $("memory-kind");
const search = $("memory-search");
const enabledInput = $("memory-enabled");
let generation = 0;
let memories = [];
let enabled = false;
let busy = false;
let loading = false;
let loadError = false;
let confirmingClear = false;
let lifecyclePolicy = { business_ttl_days: 90, profile_ttl_days: 365, profile_review_days: 180 };
const PROFILE_LABELS = { name: "姓名", role: "当前身份 / 职业", organization: "所属组织" };

const NOTE_ICON = '<svg fill="none" viewBox="0 0 24 24"><rect x="5" y="3" width="14" height="18" rx="3"/><path d="M9 8h6M9 12h6M9 16h3"/></svg>';

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(text, className, onClick) {
  const node = element("button", className, text);
  node.type = "button";
  node.onclick = onClick;
  return node;
}

function setStatus(message = "", tone = "info") {
  status.textContent = message;
  status.dataset.tone = tone;
  status.hidden = !message;
}

function updateControls() {
  for (const control of dialog.querySelectorAll("input, select, textarea, button")) {
    control.disabled = busy && !["memory-dismiss", "memory-close"].includes(control.id);
  }
  const editing = Boolean(list.querySelector(".memory-edit-form"));
  search.disabled = busy || editing;
  for (const control of list.querySelectorAll(".memory-edit")) control.disabled = busy || editing;
  $("memory-save").disabled = busy || !content.value.trim();
  $("memory-clear").disabled = busy || editing || memories.length === 0;
  $("memory-character-count").textContent = `${content.value.length} / 800`;
}

function updateKindHelp() {
  const field = $("memory-profile-field").value;
  const profile = kind.value === "profile";
  $("memory-profile-field-wrap").hidden = !profile;
  const stable = kind.value === "preference" || (profile && field === "name");
  $("memory-retention").querySelector('option[value="none"]').disabled = !stable;
  if (!stable && $("memory-retention").value === "none") $("memory-retention").value = "default";
  const helps = {
    auto: "识别明确的姓名、身份声明；含糊信息保留为业务背景。姓名与身份分别保存。",
    preference: "回答语言、详略与组织方式，默认长期保留。",
    business_context: `业务流程、系统部署等背景，默认保存 ${lifecyclePolicy.business_ttl_days} 天。`,
    profile: field === "name" ? "姓名 / 称呼默认长期保留，直到你修改或删除。"
      : `默认保存 ${lifecyclePolicy.profile_ttl_days} 天，${lifecyclePolicy.profile_review_days} 天后提示复核。`,
  };
  $("memory-kind-help").textContent = helps[kind.value];
  const examples = { auto: "例如：我叫马乐乐，是学生", preference: "例如：回答时先列法规依据，再给出建议", business_context: "例如：公司使用新加坡 CRM 处理客户联系方式", profile: field === "name" ? "例如：马乐乐" : (field === "role" ? "例如：学生" : "例如：某公司") };
  content.placeholder = examples[kind.value];
}

function resetClearConfirmation() {
  confirmingClear = false;
  $("memory-clear").textContent = "清空全部";
  $("memory-clear-cancel").hidden = true;
}

async function request(method, path = "", body) {
  const response = await fetch(`/api/v2/memory${path}`, {
    method, credentials: "include",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    const detail = data.message || data.detail;
    throw new Error(typeof detail === "string" ? detail : "记忆操作失败，请稍后重试");
  }
  return data;
}

function placeholder(title, detail, actionText, action, spinner = false) {
  const node = element("div", "memory-placeholder");
  const icon = element("div", spinner ? "memory-placeholder-spinner" : "memory-placeholder-icon");
  icon.setAttribute("aria-hidden", "true");
  if (!spinner) icon.innerHTML = NOTE_ICON;
  node.append(icon, element("h4", "", title), element("p", "", detail));
  if (action) node.append(button(actionText, "btn btn-ghost", action));
  return node;
}

function startEdit(row, memory, text, actions, footer) {
  text.hidden = true;
  actions.hidden = true;
  const editor = element("form", "memory-edit-form");
  const input = element("textarea");
  input.value = memory.content;
  input.required = true;
  input.maxLength = 800;
  input.setAttribute("aria-label", "修改记忆内容");
  const controls = element("div", "memory-edit-actions");
  const cancel = button("取消", "btn btn-ghost", () => {
    editor.remove();
    text.hidden = false;
    actions.hidden = false;
    updateControls();
  });
  const save = button("保存修改", "btn btn-primary");
  save.type = "submit";
  controls.append(cancel, save);
  editor.append(input, controls);
  row.insertBefore(editor, footer);
  editor.onsubmit = (event) => {
    event.preventDefault();
    void act(() => request("PATCH", `/${encodeURIComponent(memory.memory_id)}`, {
      content: input.value.trim(), expected_revision: memory.revision,
    }), "正在保存修改…", "记忆已更新");
  };
  updateControls();
  input.focus();
}

function memoryCard(memory) {
  const row = element("article", "memory-item");
  const meta = element("div", "memory-item-meta");
  const labels = { profile: "用户档案", preference: "表达偏好", business_context: "业务背景" };
  const badge = element("span", "memory-kind-badge", labels[memory.kind]);
  badge.dataset.kind = memory.kind;
  meta.append(badge, element("span", "", memory.source_type === "manual" ? "手动保存" : "来自聊天"));
  const text = element("p", "memory-item-content", memory.content);
  const profileValue = memory.profile_field ? element("p", "memory-profile-value", `${PROFILE_LABELS[memory.profile_field]}：${memory.value}`) : null;
  const footer = element("div", "memory-item-footer");
  const date = new Date(memory.updated_at * 1000).toLocaleDateString("zh-CN");
  const validity = memory.expires_at
    ? `有效至 ${new Date(memory.expires_at * 1000).toLocaleDateString("zh-CN")}`
    : "持续保留";
  const review = memory.needs_review ? " · 待复核" : (memory.review_at ? ` · ${new Date(memory.review_at * 1000).toLocaleDateString("zh-CN")} 复核` : "");
  footer.append(element("small", "memory-item-date", `更新于 ${date} · ${validity}${review}`));
  const actions = element("div", "memory-item-actions");
  actions.append(
    button("编辑", "btn btn-ghost memory-edit", () => startEdit(row, memory, text, actions, footer)),
    button("删除", "btn btn-ghost memory-remove", () => {
      void act(() => request("DELETE", `/${encodeURIComponent(memory.memory_id)}`), "正在删除…", "记忆已删除");
    }),
  );
  if (memory.retention_days) {
    if (memory.needs_review) row.dataset.needsReview = "true";
    actions.prepend(button(memory.needs_review ? "确认仍有效" : "更新有效期", "btn btn-ghost memory-confirm", () => {
      void act(() => request("POST", `/${encodeURIComponent(memory.memory_id)}/confirm`, { expected_revision: memory.revision }), "正在确认资料…", "已确认资料，复核时间与有效期已更新");
    }));
  }
  footer.append(actions);
  row.append(meta);
  if (profileValue) row.append(profileValue);
  row.append(text, footer);
  return row;
}

function renderList() {
  $("memory-count").textContent = String(memories.length);
  list.replaceChildren();
  if (loading) {
    list.append(placeholder("正在加载记忆", "稍等片刻，正在读取你的账号记忆。", null, null, true));
  } else if (loadError) {
    list.append(placeholder("暂时无法加载记忆", "可以重新加载，已保存的内容不会受到影响。", "重新加载", () => void loadPanel()));
  } else if (!memories.length) {
    list.append(placeholder("还没有保存的记忆", "添加一条表达偏好或背景信息，在新对话中继续使用。", "添加第一条记忆", () => content.focus()));
  } else {
    const query = search.value.trim().toLocaleLowerCase();
    const matches = memories.filter(memory => `${memory.content} ${memory.value || ""} ${PROFILE_LABELS[memory.profile_field] || ""}`.toLocaleLowerCase().includes(query));
    if (!matches.length) {
      list.append(placeholder("没有找到匹配的记忆", "试试其他关键词，或查看全部已保存的内容。", "清除搜索", () => {
        search.value = "";
        renderList();
        updateControls();
      }));
    } else {
      for (const memory of matches) list.append(memoryCard(memory));
    }
  }
}

async function refresh(current) {
  const data = await request("GET");
  if (current !== generation || !dialog.open) return false;
  memories = data.memories;
  enabled = data.enabled;
  enabledInput.checked = enabled;
  lifecyclePolicy = data.policy || lifecyclePolicy;
  updateKindHelp();
  loading = false;
  loadError = false;
  renderList();
  return true;
}

async function loadPanel() {
  const current = ++generation;
  busy = true;
  loading = true;
  loadError = false;
  setStatus();
  renderList();
  updateControls();
  try {
    await refresh(current);
  } catch (error) {
    if (current === generation) {
      loading = false;
      loadError = true;
      renderList();
      setStatus(error.message, "error");
    }
  } finally {
    if (current === generation) {
      busy = false;
      updateControls();
    }
  }
}

async function act(action, pending, success) {
  if (busy) return false;
  const current = generation;
  let completed = false;
  busy = true;
  setStatus(pending);
  updateControls();
  try {
    const result = await action();
    completed = true;
    if (current !== generation) return false;
    if (!await refresh(current)) return false;
    resetClearConfirmation();
    setStatus(typeof success === "function" ? success(result) : success, "success");
    return true;
  } catch (error) {
    if (current === generation) {
      enabledInput.checked = enabled;
      setStatus(completed ? "操作已完成，但列表刷新失败。请重新打开查看。" : error.message, "error");
    }
    return false;
  } finally {
    if (current === generation) {
      busy = false;
      updateControls();
    }
  }
}

$("btn-account-memory").onclick = () => {
  if (!getUser() || getUser().provider === "anonymous" || dialog.open) return;
  form.reset();
  search.value = "";
  memories = [];
  resetClearConfirmation();
  updateKindHelp();
  dialog.showModal();
  void loadPanel();
};
$("memory-dismiss").onclick = () => dialog.close();
$("memory-close").onclick = () => dialog.close();
dialog.addEventListener("close", () => {
  generation += 1;
  busy = false;
  resetClearConfirmation();
  setStatus();
});
dialog.addEventListener("click", (event) => {
  if (event.target !== dialog) return;
  const rect = dialog.getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
});
form.onsubmit = async (event) => {
  event.preventDefault();
  const value = content.value.trim();
  if (!value) return;
  const body = { content: value, kind: kind.value };
  if (kind.value === "profile") body.profile_field = $("memory-profile-field").value;
  const retention = $("memory-retention").value;
  if (retention !== "default") body.expires_at = retention === "none" ? null : Date.now() / 1000 + Number(retention) * 86400;
  const saved = await act(() => request("POST", "", body), "正在保存记忆…", result => result.unchanged ? "这条记忆已存在，没有重复新增" : (result.saved_count > 1 ? `记忆已保存，拆分为 ${result.saved_count} 条字段记录` : "记忆已保存"));
  if (saved) {
    content.value = "";
    updateControls();
  }
};
content.addEventListener("input", updateControls);
kind.addEventListener("change", updateKindHelp);
$("memory-profile-field").addEventListener("change", updateKindHelp);
search.addEventListener("input", () => { renderList(); updateControls(); });
enabledInput.onchange = () => {
  const next = enabledInput.checked;
  void act(() => request("PUT", "/settings", { enabled: next }), "正在更新设置…", next ? "已开启新对话记忆" : "已关闭新对话记忆，保存的内容仍保留");
};
$("memory-clear").onclick = () => {
  if (!confirmingClear) {
    confirmingClear = true;
    $("memory-clear").textContent = `确认清空 ${memories.length} 条`;
    $("memory-clear-cancel").hidden = false;
    setStatus("将删除当前账号保存的全部记忆，请确认后继续。", "warning");
    return;
  }
  void act(() => request("DELETE"), "正在清空记忆…", "已清空全部记忆");
};
$("memory-clear-cancel").onclick = () => { resetClearConfirmation(); setStatus(); };
onUserChange((user) => {
  generation += 1;
  busy = false;
  memories = [];
  dialog.close();
  list.replaceChildren();
  $("memory-count").textContent = "0";
  setStatus();
  $("btn-account-memory").hidden = !user || user.provider === "anonymous";
});
