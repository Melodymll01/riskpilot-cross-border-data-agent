/**
 * kb.js — 知识库面板。
 *
 * 权限模型：
 * - 读（list / stats / detail）：任意登录用户
 * - 写（upload / web / delete）：仅 admin。非 admin 的删除按钮不渲染。
 *
 * 模块状态：
 * - 不缓存文档列表；每次 refresh() 都重新打 GET /api/v2/documents
 * - 上传 / 删除 / 网页采集成功后自动 refresh
 *
 * 调用方：app.js 在用户切换到 KB 视图时调用 mount() / refresh()
 */

import { documents } from "./api.js";
import { getUser } from "./auth.js";

const $ = (sel, root = document) => root.querySelector(sel);

let _mounted = false;

/**
 * 首次进入 KB 视图时调用；幂等。
 */
export function mount() {
  if (_mounted) return;
  _mounted = true;
  bindUI();
}

/**
 * 拉取列表 + 统计 + 渲染。
 */
export async function refresh() {
  setStatus("loading", "加载中…");
  try {
    const [listResp, statsResp] = await Promise.all([
      documents.list(),
      documents.stats(),
    ]);
    renderStats(statsResp);
    renderList(listResp.documents || []);
    setStatus("ok", `${statsResp.document_count} 个文档 · ${statsResp.chunk_count} 个 chunk`);
  } catch (err) {
    console.error("kb refresh failed", err);
    setStatus("err", `加载失败：${err.message}`);
    renderList([]);
  }
}

// ─────────── 渲染 ───────────

function renderStats(stats) {
  $("#kb-stat-docs").textContent = stats.document_count ?? 0;
  $("#kb-stat-chunks").textContent = stats.chunk_count ?? 0;
}

function renderList(docs) {
  const tbody = $("#kb-table tbody");
  tbody.innerHTML = "";
  const isAdmin = !!getUser()?.is_admin;
  if (docs.length === 0) {
    const tr = document.createElement("tr");
    tr.className = "kb-empty";
    const emptyMsg = isAdmin
      ? "知识库为空。请上传文件或采集网页。"
      : "知识库为空。需管理员入库后才有内容可查。";
    tr.innerHTML = `<td colspan="6" class="kb-empty-cell">${emptyMsg}</td>`;
    tbody.appendChild(tr);
    return;
  }
  for (const d of docs) {
    const tr = document.createElement("tr");
    tr.className = "kb-row";
    tr.dataset.sourceName = d.source_name;

    const typeBadge = d.source_type === "web" ? "🌐 网页" : "📄 文件";
    const titleHtml = d.source_url
      ? `<a href="${escapeAttr(d.source_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(d.title || d.source_name)}</a>`
      : escapeHtml(d.title || d.source_name);
    const cat = d.category ? `<span class="kb-cat">${escapeHtml(d.category)}</span>` : `<span class="kb-cat kb-cat-empty">—</span>`;
    const actionCell = isAdmin
      ? `<button class="btn btn-ghost kb-delete" title="删除该文档（连同所有 chunk）">删除</button>`
      : `<span class="kb-action-muted">—</span>`;

    tr.innerHTML = `
      <td class="kb-col-type">${typeBadge}</td>
      <td class="kb-col-source" title="${escapeAttr(d.source_name)}">${escapeHtml(d.source_name)}</td>
      <td class="kb-col-title">${titleHtml}</td>
      <td class="kb-col-cat">${cat}</td>
      <td class="kb-col-chunks">${d.chunk_count}</td>
      <td class="kb-col-action">${actionCell}</td>
    `;
    const delBtn = tr.querySelector(".kb-delete");
    if (delBtn) {
      delBtn.addEventListener("click", () => onDelete(d.source_name, d.title || d.source_name));
    }
    tbody.appendChild(tr);
  }
}

function setStatus(state, text) {
  const el = $("#kb-status");
  if (!el) return;
  el.dataset.state = state;
  el.textContent = text;
}

// ─────────── 交互 ───────────

function bindUI() {
  // 刷新按钮
  $("#kb-btn-refresh")?.addEventListener("click", refresh);

  // 上传文件
  const fileInput = $("#kb-file-input");
  $("#kb-btn-upload")?.addEventListener("click", () => fileInput.click());
  fileInput?.addEventListener("change", onFilePick);

  // 网页采集
  $("#kb-form-web")?.addEventListener("submit", onWebSubmit);
}

async function onFilePick(ev) {
  const file = ev.target.files?.[0];
  ev.target.value = ""; // 允许选同一文件再次上传
  if (!file) return;

  const category = ($("#kb-file-category")?.value || "").trim();
  setStatus("loading", `上传中：${file.name}（${(file.size / 1024).toFixed(1)} KB）…`);
  try {
    const result = await documents.ingestFile(file, category);
    if (result.success) {
      setStatus("ok", `✓ ${result.message || "入库成功"} · ${result.chunk_count} chunk`);
    } else {
      setStatus("warn", `${result.message || "入库未成功"}`);
    }
    await refresh();
  } catch (err) {
    setStatus("err", `上传失败：${err.message}`);
  }
}

async function onWebSubmit(ev) {
  ev.preventDefault();
  const url = ($("#kb-web-url")?.value || "").trim();
  const category = ($("#kb-web-category")?.value || "").trim();
  if (!url) return;

  setStatus("loading", `采集中：${url}…`);
  try {
    const result = await documents.ingestWeb({ url, category });
    if (result.success) {
      setStatus("ok", `✓ ${result.message || "采集成功"} · ${result.chunk_count} chunk`);
      $("#kb-web-url").value = "";
    } else {
      setStatus("warn", `${result.message || "采集未成功"}`);
    }
    await refresh();
  } catch (err) {
    setStatus("err", `采集失败：${err.message}`);
  }
}

async function onDelete(sourceName, displayName) {
  if (!confirm(`删除文档 "${displayName}" 及其所有 chunk？此操作不可撤销。`)) return;
  setStatus("loading", `删除中：${sourceName}…`);
  try {
    const r = await documents.remove(sourceName);
    setStatus("ok", `✓ 已删除 ${r.deleted_count} 条 chunk`);
    await refresh();
  } catch (err) {
    setStatus("err", `删除失败：${err.message}`);
  }
}

// ─────────── 工具 ───────────

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function escapeAttr(s) {
  return escapeHtml(s).replace(/"/g, "&quot;");
}
