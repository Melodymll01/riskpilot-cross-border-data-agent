/**
 * api.js — `/api/v2/*` 的轻量 REST 客户端。
 *
 * 约定：
 * - 所有请求带 `credentials: "include"` 让浏览器自动携带 copilot_session cookie
 * - 服务端用结构化错误：{error_code, message}；非 2xx 抛 ApiError，业务层统一捕获
 * - SSE 端点单独走 sse.js，这里只管 JSON 请求
 */

const BASE = "/api/v2";

export class ApiError extends Error {
  constructor(status, errorCode, message) {
    super(message || errorCode || `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.errorCode = errorCode;
  }
}

async function request(method, path, body) {
  const opts = {
    method,
    credentials: "include",
    headers: { "Accept": "application/json" },
  };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(`${BASE}${path}`, opts);
  const text = await resp.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = { raw: text }; }
  }
  if (!resp.ok) {
    const code = data?.error_code || data?.detail?.error_code || "HTTP_ERROR";
    const msg = data?.message || data?.detail?.message || data?.detail || resp.statusText;
    throw new ApiError(resp.status, code, msg);
  }
  return data;
}

/* ─────────── auth ─────────── */
export const auth = {
  me: () => request("GET", "/auth/me"),
  anonymous: () => request("POST", "/auth/anonymous"),
  githubLoginUrl: () => request("GET", "/auth/github/login"),
  logout: () => request("POST", "/auth/logout"),
};

/* ─────────── tasks ─────────── */
export const tasks = {
  list: (limit = 50) => request("GET", `/tasks?limit=${limit}`),
  get: (taskId) => request("GET", `/tasks/${encodeURIComponent(taskId)}`),
  patch: (taskId, body) => request("PATCH", `/tasks/${encodeURIComponent(taskId)}`, body),
  remove: (taskId) => request("DELETE", `/tasks/${encodeURIComponent(taskId)}`),
};

/* ─────────── copilot ─────────── */
export const copilot = {
  // 同步聚合（备用，前端默认走 SSE）
  chat: (body) => request("POST", "/copilot/chat", body),
};

/* ─────────── documents (知识库管理 · admin-only) ─────────── */
export const documents = {
  list: () => request("GET", "/documents"),
  stats: () => request("GET", "/documents/stats"),
  get: (sourceName) => request("GET", `/documents/${encodeURIComponent(sourceName)}`),
  remove: (sourceName) => request("DELETE", `/documents/${encodeURIComponent(sourceName)}`),
  ingestWeb: (body) => request("POST", "/documents/web", body),
  /**
   * 上传文件（multipart/form-data，request() 不适配，这里单独走 fetch）。
   * @param {File} file
   * @param {string} category
   */
  ingestFile: async (file, category = "") => {
    const fd = new FormData();
    fd.append("file", file);
    const qs = category ? `?category=${encodeURIComponent(category)}` : "";
    const resp = await fetch(`${BASE}/documents/file${qs}`, {
      method: "POST",
      credentials: "include",
      headers: { Accept: "application/json" },
      body: fd,
    });
    const text = await resp.text();
    let data = null;
    if (text) {
      try { data = JSON.parse(text); } catch { data = { raw: text }; }
    }
    if (!resp.ok) {
      const code = data?.error_code || data?.detail?.error_code || "HTTP_ERROR";
      const msg = data?.message || data?.detail?.message || data?.detail || resp.statusText;
      throw new ApiError(resp.status, code, msg);
    }
    return data;
  },
};

/* ─────────── health ─────────── */
export const health = {
  check: () => request("GET", "/health"),
  ready: () => request("GET", "/health/ready"),
};

/* ─────────── audit (admin-only) ─────────── */
export const audit = {
  /**
   * 拉取审计日志。全部参数可选。
   * @param {{limit?: number, offset?: number, action?: string, actor_id?: string}} params
   */
  list: ({ limit = 50, offset = 0, action = "", actor_id = "" } = {}) => {
    const qs = new URLSearchParams();
    qs.set("limit", String(limit));
    qs.set("offset", String(offset));
    if (action) qs.set("action", action);
    if (actor_id) qs.set("actor_id", actor_id);
    return request("GET", `/audit/logs?${qs.toString()}`);
  },
};
