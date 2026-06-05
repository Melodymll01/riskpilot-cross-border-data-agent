/**
 * tasks.js — 左侧任务列表：拉取 / 渲染 / 删除 / 切换。
 *
 * 不缓存：每次 refresh 都打一次 /api/v2/tasks（数量少、cheap）。
 */

import { tasks as tasksApi } from "./api.js";

const $ = (sel) => document.querySelector(sel);

let _onSelect = null;
let _activeId = null;

export function onSelect(fn) { _onSelect = fn; }

export function setActive(taskId) {
  _activeId = taskId;
  for (const li of document.querySelectorAll(".task-item")) {
    li.classList.toggle("active", li.dataset.taskId === taskId);
  }
}

export async function refresh() {
  let resp;
  try {
    resp = await tasksApi.list(50);
  } catch (err) {
    console.warn("tasks.list failed", err);
    return;
  }
  const list = $("#task-list");
  list.innerHTML = "";
  const items = resp?.tasks || [];
  $("#task-count").textContent = items.length;

  for (const t of items) {
    const li = document.createElement("li");
    li.className = "task-item";
    li.dataset.taskId = t.task_id;
    if (t.task_id === _activeId) li.classList.add("active");

    const title = t.title || "未命名对话";
    li.innerHTML = `
      <span class="task-title-text" title="${escapeAttr(title)}">${escapeHtml(title)}</span>
      <button class="task-delete" title="删除">×</button>
    `;
    li.querySelector(".task-title-text").addEventListener("click", () => {
      _onSelect?.(t.task_id);
    });
    li.querySelector(".task-delete").addEventListener("click", async (ev) => {
      ev.stopPropagation();
      if (!confirm(`删除任务"${title}"？此操作不可撤销。`)) return;
      try {
        await tasksApi.remove(t.task_id);
        if (_activeId === t.task_id) _activeId = null;
        await refresh();
      } catch (err) {
        alert(`删除失败：${err.message}`);
      }
    });
    list.appendChild(li);
  }
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
function escapeAttr(s) {
  return escapeHtml(s).replace(/"/g, "&quot;");
}
