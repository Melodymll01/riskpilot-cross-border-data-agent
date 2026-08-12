/**
 * api.js — `/api/v2/*` 的轻量 REST 客户端。
 *
 * 约定：
 * - 所有请求带 `credentials: "include"` 让浏览器自动携带 copilot_session cookie
 * - 服务端用结构化错误：{error_code, message}；非 2xx 抛 ApiError，业务层统一捕获
 * - SSE 端点单独走 sse.js，这里只管 JSON 请求
 */

const BASE = "/api/v2";
const V3_BASE = "/api/v3";

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

async function requestV3(method, path, body) {
  return requestAt(V3_BASE, method, path, body);
}

async function requestFormV3(path, formData) {
  const resp = await fetch(`${V3_BASE}${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "Accept": "application/json" },
    body: formData,
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

/* ─────────── documents (知识库管理 · Step 025a 多租户) ─────────── */
export const documents = {
  /**
   * 列出文档。scope: "public" | "mine" | "all"（默认 all = 公共 ∪ 自己）
   */
  list: (scope = "all") => {
    const qs = scope ? `?scope=${encodeURIComponent(scope)}` : "";
    return request("GET", `/documents${qs}`);
  },
  stats: (scope = "all") => {
    const qs = scope ? `?scope=${encodeURIComponent(scope)}` : "";
    return request("GET", `/documents/stats${qs}`);
  },
  get: (sourceName, scope = "all") => {
    const qs = scope ? `?scope=${encodeURIComponent(scope)}` : "";
    return request("GET", `/documents/${encodeURIComponent(sourceName)}${qs}`);
  },
  remove: (sourceName) =>
    request("DELETE", `/documents/${encodeURIComponent(sourceName)}`),
  /**
   * 网页入库。``asPublic`` 仅 admin 生效（true 入公共，false 入私人）。
   */
  ingestWeb: (body, { asPublic = false } = {}) => {
    const qs = asPublic ? "?as_public=true" : "";
    return request("POST", `/documents/web${qs}`, body);
  },
  /**
   * 上传文件（multipart/form-data，request() 不适配，这里单独走 fetch）。
   * @param {File} file
   * @param {string} category
   * @param {{asPublic?: boolean}} opts asPublic 仅 admin 生效
   */
  ingestFile: async (file, category = "", { asPublic = false } = {}) => {
    const fd = new FormData();
    fd.append("file", file);
    const params = new URLSearchParams();
    if (category) params.set("category", category);
    if (asPublic) params.set("as_public", "true");
    const qs = params.toString() ? `?${params.toString()}` : "";
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

/* ─────────── V3 案件工作台 ─────────── */
export const casesV3 = {
  workspaces: () =>
    requestV3("GET", "/workspaces?limit=200"),
  createWorkspace: (body) =>
    requestV3("POST", "/workspaces", body),
  list: (workspaceId) =>
    requestV3(
      "GET",
      `/cases?workspace_id=${encodeURIComponent(workspaceId)}&limit=200`
    ),
  create: (body) =>
    requestV3("POST", "/cases", body),
  get: (caseId) =>
    requestV3("GET", `/cases/${encodeURIComponent(caseId)}`),
  documents: (caseId) =>
    requestV3("GET", `/cases/${encodeURIComponent(caseId)}/documents`),
  uploadDocument: (caseId, file, { documentType = "case_material", purpose = "" } = {}) => {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("document_type", documentType);
    formData.append("purpose", purpose);
    return requestFormV3(
      `/cases/${encodeURIComponent(caseId)}/documents`,
      formData
    );
  },
  processingJob: (jobId) =>
    requestV3("GET", `/processing-jobs/${encodeURIComponent(jobId)}`),
  parseDocument: (jobId) =>
    requestV3("POST", `/processing-jobs/${encodeURIComponent(jobId)}/parse`),
  indexDocument: (jobId) =>
    requestV3("POST", `/processing-jobs/${encodeURIComponent(jobId)}/index`),
  retryDocument: (jobId) =>
    requestV3("POST", `/processing-jobs/${encodeURIComponent(jobId)}/retry`),
  facts: (caseId) =>
    requestV3("GET", `/cases/${encodeURIComponent(caseId)}/facts`),
  fact: (factId) =>
    requestV3("GET", `/facts/${encodeURIComponent(factId)}`),
  runs: (caseId) =>
    requestV3("GET", `/cases/${encodeURIComponent(caseId)}/assessment-runs`),
  run: (runId) =>
    requestV3("GET", `/runs/${encodeURIComponent(runId)}`),
  events: (runId) =>
    requestV3("GET", `/runs/${encodeURIComponent(runId)}/events?after_sequence=0`),
  proposeFacts: (caseId, body) =>
    requestV3("POST", `/cases/${encodeURIComponent(caseId)}/fact-proposals`, body),
  transitionFact: (factId, target) =>
    requestV3("POST", `/facts/${encodeURIComponent(factId)}/transitions`, { target }),
  continueRun: (runId) =>
    requestV3("POST", `/runs/${encodeURIComponent(runId)}/continue`),
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

  /**
   * 构造 CSV 导出端点 URL（Step 026a）。
   * 返回字符串而非 fetch；前端用 `<a download>` 或 location.href 触发浏览器下载，
   * 避开把整个 CSV 读到 JS 内存的开销。
   * @param {{action?: string, actor_id?: string, max_rows?: number}} params
   * @returns {string}
   */
  exportCsvUrl: ({ action = "", actor_id = "", max_rows = 10000 } = {}) => {
    const qs = new URLSearchParams();
    if (action) qs.set("action", action);
    if (actor_id) qs.set("actor_id", actor_id);
    qs.set("max_rows", String(max_rows));
    return `${BASE}/audit/export.csv?${qs.toString()}`;
  },
};

/* ─────────── memory（每用户记忆 · Step 030d/031a） ─────────── */
export const memory = {
  /** 当前 owner 的 L3 用户画像（稳定偏好）。 */
  profile: () => request("GET", "/memory/profile"),

  /** 读记忆开关（参考保存的记忆）。 */
  getSettings: () => request("GET", "/memory/settings"),

  /**
   * 部分更新记忆开关；只传需要改的字段，未传字段保持原值。
   * @param {{use_saved_memory?: boolean}} body
   */
  updateSettings: (body) => request("PUT", "/memory/settings", body),

  /** 当前 owner 生效的长期事实清单 + 容量上限。 */
  facts: () => request("GET", "/memory/facts"),

  /**
   * 删除当前 owner 的单条长期事实（被遗忘权细粒度，Step 034）。
   * 成功 204（request 返回 null）；事实不存在抛 ApiError(404)。
   * @param {string} factId
   */
  deleteFact: (factId) =>
    request("DELETE", `/memory/facts/${encodeURIComponent(factId)}`),

  /**
   * 主动遗忘（被遗忘权）。
   * @param {"memory"|"all"} scope "memory"=只清派生记忆；"all"=连带原始对话
   */
  forget: (scope = "memory") => request("POST", "/memory/forget", { scope }),
};
