/**
 * kb.js — 知识库面板（Step 025a 多租户）。
 *
 * 权限模型：
 * - 读（list / stats / detail）：任意登录用户，按 ``scope`` 过滤可见范围
 * - 写（upload / web）：登录用户可入私人库；admin 可勾选"入公共"
 * - 删：admin 可删任意；普通用户仅可删自己上传的（owner_id == user_id）
 *
 * UI 增量：
 * - scope toggle（公共 / 我的 / 全部）
 * - 上传时 admin 看到 "入公共库" 复选框
 * - 列表行追加 owner badge（"公共"绿 / "我的"蓝 / "他人"灰）
 * - 删除按钮按行级权限渲染（仅自己的 + admin）
 *
 * 模块状态：
 * - 不缓存文档列表；每次 refresh() 都重新打 GET /api/v2/documents?scope=
 * - 上传 / 删除 / 网页采集成功后自动 refresh
 *
 * 调用方：app.js 在用户切换到 KB 视图时调用 mount() / refresh()
 */

import { documents } from "./api.js";
import { getUser } from "./auth.js";

const $ = (sel, root = document) => root.querySelector(sel);

let _mounted = false;
let _currentScope = "all"; // "public" | "mine" | "all"

/**
 * 首次进入 KB 视图时调用；幂等。
 */
export function mount() {
  if (_mounted) return;
  _mounted = true;
  bindUI();
  syncAdminUI();
}

/**
 * 拉取列表 + 统计 + 渲染。
 */
export async function refresh() {
  // 每次刷新都跟随当前登录态同步 admin 专属 UI
  syncAdminUI();
  setStatus("loading", "加载中…");
  try {
    const [listResp, statsResp] = await Promise.all([
      documents.list(_currentScope),
      documents.stats(_currentScope),
    ]);
    renderStats(statsResp);
    renderList(listResp.documents || []);
    setStatus(
      "ok",
      `[${scopeLabel(_currentScope)}] ${statsResp.document_count} 个文档 · ${statsResp.chunk_count} 个 chunk（全库）`,
    );
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
  const user = getUser();
  const isAdmin = !!user?.is_admin;
  const myId = user?.user_id || null;
  if (docs.length === 0) {
    const tr = document.createElement("tr");
    tr.className = "kb-empty";
    const emptyMsg =
      _currentScope === "mine"
        ? "你还没有上传过私人文档。请通过下方上传文件或采集网页。"
        : _currentScope === "public"
        ? "公共库为空。等待管理员入库后才有公共内容。"
        : "知识库为空。可上传私人文档，或等待管理员入库公共内容。";
    tr.innerHTML = `<td colspan="7" class="kb-empty-cell">${emptyMsg}</td>`;
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
    const cat = d.category
      ? `<span class="kb-cat">${escapeHtml(d.category)}</span>`
      : `<span class="kb-cat kb-cat-empty">—</span>`;
    const ownerBadge = renderOwnerBadge(d.owner_id, myId);
    // 行级删除权限：admin 总可见；普通用户仅看到自己的
    const canDelete = isAdmin || (myId !== null && d.owner_id === myId);
    const actionCell = canDelete
      ? `<button class="btn btn-ghost kb-delete" title="删除该文档（连同所有 chunk）">删除</button>`
      : `<span class="kb-action-muted">—</span>`;

    tr.innerHTML = `
      <td class="kb-col-type">${typeBadge}</td>
      <td class="kb-col-owner">${ownerBadge}</td>
      <td class="kb-col-source" title="${escapeAttr(d.source_name)}">${escapeHtml(d.source_name)}</td>
      <td class="kb-col-title">${titleHtml}</td>
      <td class="kb-col-cat">${cat}</td>
      <td class="kb-col-chunks">${d.chunk_count}</td>
      <td class="kb-col-action">${actionCell}</td>
    `;
    const delBtn = tr.querySelector(".kb-delete");
    if (delBtn) {
      delBtn.addEventListener("click", () =>
        onDelete(d.source_name, d.title || d.source_name),
      );
    }
    tbody.appendChild(tr);
  }
}

function renderOwnerBadge(ownerId, myId) {
  if (!ownerId) {
    return `<span class="kb-badge kb-badge-public" title="公共库（所有用户可见）">公共</span>`;
  }
  if (myId !== null && ownerId === myId) {
    return `<span class="kb-badge kb-badge-mine" title="你上传的私人文档">我的</span>`;
  }
  return `<span class="kb-badge kb-badge-other" title="他人的私人文档（仅 admin 可见）">他人</span>`;
}

function scopeLabel(scope) {
  return { public: "公共", mine: "我的", all: "全部" }[scope] || "全部";
}

function setStatus(state, text) {
  const el = $("#kb-status");
  if (!el) return;
  el.dataset.state = state;
  el.textContent = text;
}

function syncAdminUI() {
  // admin-only 元素：as_public 复选框（默认为 admin 勾上 = 入公共）
  const isAdmin = !!getUser()?.is_admin;
  const filePublicWrap = $("#kb-file-aspublic-wrap");
  const webPublicWrap = $("#kb-web-aspublic-wrap");
  if (filePublicWrap) filePublicWrap.style.display = isAdmin ? "" : "none";
  if (webPublicWrap) webPublicWrap.style.display = isAdmin ? "" : "none";
  // admin 默认勾上
  const fp = $("#kb-file-aspublic");
  const wp = $("#kb-web-aspublic");
  if (fp && isAdmin) fp.checked = true;
  if (wp && isAdmin) wp.checked = true;
}

// ─────────── 交互 ───────────

function bindUI() {
  // 刷新按钮
  $("#kb-btn-refresh")?.addEventListener("click", refresh);

  // scope toggle
  document.querySelectorAll(".kb-scope-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const next = btn.dataset.scope || "all";
      if (next === _currentScope) return;
      _currentScope = next;
      document.querySelectorAll(".kb-scope-btn").forEach((b) =>
        b.classList.toggle("is-active", b === btn),
      );
      refresh();
    });
  });

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
  const asPublic = !!$("#kb-file-aspublic")?.checked && !!getUser()?.is_admin;
  setStatus(
    "loading",
    `上传中：${file.name}（${(file.size / 1024).toFixed(1)} KB）${asPublic ? " · 公共" : " · 私人"}…`,
  );
  try {
    const result = await documents.ingestFile(file, category, { asPublic });
    if (result.success) {
      setStatus(
        "ok",
        `✓ ${result.message || "入库成功"} · ${result.chunk_count} chunk · ${asPublic ? "公共" : "私人"}`,
      );
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
  const asPublic = !!$("#kb-web-aspublic")?.checked && !!getUser()?.is_admin;

  setStatus("loading", `采集中：${url}${asPublic ? " · 公共" : " · 私人"}…`);
  try {
    const result = await documents.ingestWeb({ url, category }, { asPublic });
    if (result.success) {
      setStatus(
        "ok",
        `✓ ${result.message || "采集成功"} · ${result.chunk_count} chunk · ${asPublic ? "公共" : "私人"}`,
      );
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
