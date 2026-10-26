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
  return requestAt(BASE, method, path, body);
}

async function requestAt(base, method, path, body) {
  const opts = {
    method,
    credentials: "include",
    headers: { "Accept": "application/json" },
  };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(`${base}${path}`, opts);
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

/* ─────────── knowledge base management ─────────── */
export const documents = {
  list: (scope = "public") => request("GET", `/documents?scope=${encodeURIComponent(scope)}`),
  stats: (scope = "public") => request("GET", `/documents/stats?scope=${encodeURIComponent(scope)}`),
  remove: (sourceName) => request("DELETE", `/documents/${encodeURIComponent(sourceName)}`),
  uploadPublicFile: async (file, category = "法规") => {
    const form = new FormData();
    form.append("file", file);
    const response = await fetch(
      `${BASE}/documents/file?as_public=true&category=${encodeURIComponent(category)}`,
      { method: "POST", credentials: "include", body: form },
    );
    const text = await response.text();
    let data = null;
    if (text) {
      try { data = JSON.parse(text); } catch { data = { raw: text }; }
    }
    if (!response.ok) {
      const code = data?.error_code || data?.detail?.error_code || "HTTP_ERROR";
      const message = data?.message || data?.detail?.message || data?.detail || response.statusText;
      throw new ApiError(response.status, code, message);
    }
    return data;
  },
  uploadPublicWeb: (url, category = "法规") =>
    request("POST", "/documents/web?as_public=true", { url, category }),
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
  chat: (body) => request("POST", "/copilot/chat", body),
  getRun: (runId) => request("GET", `/copilot/runs/${encodeURIComponent(runId)}`),
  cancelRun: (runId) => request("POST", `/copilot/runs/${encodeURIComponent(runId)}/cancel`),
};

/* ─────────── feedback（消息点赞/点踩 · 后台统计） ─────────── */
export const feedback = {
  /**
   * 提交点赞/点踩。
   * @param {{msg_id: string, task_id: string, rating: "up"|"down"|"none"}} body
   *   rating="none" 表示撤销之前的评价
   */
  submit: (body) => request("POST", "/feedback", body),

  /**
   * 拉取某个 task 下当前用户的全部反馈，用于加载历史时回显按钮状态。
   * @param {string} taskId
   * @returns {Promise<{ratings: Record<string,string>}>}
   */
  forTask: (taskId) =>
    request("GET", `/feedback?task_id=${encodeURIComponent(taskId)}`),
};

export const health = {
  check: () => request("GET", "/health"),
  ready: () => request("GET", "/health/ready"),
};
