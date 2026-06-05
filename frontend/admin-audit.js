/**
 * admin-audit.js — admin 审计日志面板（Step 023a）。
 *
 * 数据源：GET /api/v2/audit/logs?limit=&offset=&action=&actor_id=
 *
 * 权限：UI 入口由 app.js 在 `applyAdminGate()` 控制（仅 admin 可见）；
 *       端点本身也是 admin-only，非 admin 即使手动调也会 403。
 *
 * 翻页策略：服务端没返回 total，本面板按"返回数 < limit ⇒ 到底"判断
 * 「下一页」是否可点；upcoming Step 若加 total，可在 renderPager 里直接消费。
 */

import { audit } from "./api.js";

const $ = (sel, root = document) => root.querySelector(sel);

const PAGE_SIZE = 50;

const _state = {
  mounted: false,
  page: 0, // 0-based
  actionFilter: "",
  actorFilter: "",
  lastCount: 0, // 上一次返回条数，用于判定到底
};

/** 首次进入审计视图时调用；幂等。 */
export function mount() {
  if (_state.mounted) return;
  _state.mounted = true;
  bindUI();
}

/** 拉取当前页并渲染。 */
export async function refresh() {
  setStatus("loading", "加载中…");
  try {
    const resp = await audit.list({
      limit: PAGE_SIZE,
      offset: _state.page * PAGE_SIZE,
      action: _state.actionFilter,
      actor_id: _state.actorFilter,
    });
    const entries = resp.entries || [];
    _state.lastCount = entries.length;
    renderRows(entries);
    renderPager();
    if (entries.length === 0 && _state.page === 0) {
      setStatus("ok", "暂无审计记录");
    } else {
      setStatus("ok", `第 ${_state.page + 1} 页 · ${entries.length} 条`);
    }
  } catch (err) {
    console.error("audit refresh failed", err);
    setStatus("err", `加载失败：${err.message}`);
    renderRows([]);
    renderPager();
  }
}

// ─────────── 渲染 ───────────

function renderRows(entries) {
  const tbody = $("#audit-table tbody");
  tbody.innerHTML = "";
  if (entries.length === 0) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="6" class="audit-empty-cell">无记录</td>`;
    tbody.appendChild(tr);
    return;
  }
  for (const e of entries) {
    const tr = document.createElement("tr");
    tr.appendChild(td(formatTime(e.timestamp), "audit-col-time"));
    tr.appendChild(td(e.actor_id, "audit-col-actor"));
    tr.appendChild(td(e.action, "audit-col-action"));
    tr.appendChild(td(e.resource, "audit-col-resource"));
    tr.appendChild(td(
      successBadge(e.success, e.error),
      "audit-col-success",
      true,
    ));
    tr.appendChild(td(
      extraSummary(e.extra_json),
      "audit-col-extra",
      true,
    ));
    tbody.appendChild(tr);
  }
}

function renderPager() {
  $("#audit-page-label").textContent = `第 ${_state.page + 1} 页`;
  $("#audit-prev").disabled = _state.page === 0;
  // 当前页拿满 PAGE_SIZE 时假定可能还有下一页；不满则到底
  $("#audit-next").disabled = _state.lastCount < PAGE_SIZE;
}

// ─────────── 辅助 ───────────

function td(content, cls, isHtml = false) {
  const cell = document.createElement("td");
  if (cls) cell.className = cls;
  if (isHtml && content instanceof Node) {
    cell.appendChild(content);
  } else if (isHtml) {
    cell.innerHTML = content;
  } else {
    cell.textContent = String(content ?? "");
  }
  return cell;
}

function formatTime(ts) {
  // ts 是 unix seconds（float）；本地时区显示到秒
  const d = new Date(ts * 1000);
  if (isNaN(d.getTime())) return String(ts);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function successBadge(success, error) {
  if (success) {
    return `<span class="audit-badge audit-badge-ok">成功</span>`;
  }
  const safeErr = escapeHtml(error || "失败");
  return `<span class="audit-badge audit-badge-err" title="${safeErr}">失败</span>`;
}

function extraSummary(extra) {
  if (!extra || typeof extra !== "object") return "";
  const keys = Object.keys(extra);
  if (keys.length === 0) return `<span class="audit-extra-empty">—</span>`;
  // 紧凑摘要：取前 3 个字段 key=value（截断长 value）
  const parts = keys.slice(0, 3).map((k) => {
    const v = extra[k];
    const vs = typeof v === "string" ? v : JSON.stringify(v);
    const short = vs.length > 28 ? vs.slice(0, 28) + "…" : vs;
    return `<code>${escapeHtml(k)}=${escapeHtml(short)}</code>`;
  });
  const more = keys.length > 3 ? ` <span class="audit-extra-more">+${keys.length - 3}</span>` : "";
  // 把完整 JSON 放在 title 里，鼠标悬停可看
  const fullJson = escapeHtml(JSON.stringify(extra, null, 2));
  return `<span class="audit-extra" title="${fullJson}">${parts.join(" ")}${more}</span>`;
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function setStatus(state, text) {
  const el = $("#audit-status");
  if (!el) return;
  el.dataset.state = state;
  el.textContent = text;
}

// ─────────── 事件绑定 ───────────

function bindUI() {
  $("#audit-btn-refresh").addEventListener("click", () => {
    _state.page = 0;
    refresh();
  });

  $("#audit-action-filter").addEventListener("change", (ev) => {
    _state.actionFilter = ev.target.value || "";
    _state.page = 0;
    refresh();
  });

  let actorDebounce;
  $("#audit-actor-filter").addEventListener("input", (ev) => {
    clearTimeout(actorDebounce);
    actorDebounce = setTimeout(() => {
      _state.actorFilter = (ev.target.value || "").trim();
      _state.page = 0;
      refresh();
    }, 300);
  });

  $("#audit-prev").addEventListener("click", () => {
    if (_state.page === 0) return;
    _state.page -= 1;
    refresh();
  });

  $("#audit-next").addEventListener("click", () => {
    if (_state.lastCount < PAGE_SIZE) return;
    _state.page += 1;
    refresh();
  });
}
