/**
 * auth.js — 鉴权状态管理。
 *
 * 启动流程：
 * 1. GET /auth/me 看是否已有 session
 * 2. 没有则自动 POST /auth/anonymous 拿匿名 session（产品默认体验）
 * 3. 用户点 "使用 GitHub 登录"：GET /auth/github/login → 拿 authorize_url → window.location 跳转
 *
 * 状态只保存在内存，不写 localStorage——cookie 才是单一真相源。
 */

import { auth as authApi, ApiError } from "./api.js";

let _current = null;
const _listeners = new Set();

export function getUser() {
  return _current;
}

export function onUserChange(fn) {
  _listeners.add(fn);
  return () => _listeners.delete(fn);
}

function setUser(user) {
  _current = user;
  for (const fn of _listeners) {
    try { fn(user); } catch (e) { console.error("user listener failed", e); }
  }
}

/**
 * 启动期身份检查 + 自动匿名兜底。
 */
export async function ensureSession() {
  try {
    const me = await authApi.me();
    if (me.authenticated) {
      setUser(me.user);
      return me.user;
    }
  } catch (err) {
    console.warn("auth.me failed, will try anonymous", err);
  }

  // 自动匿名
  try {
    const resp = await authApi.anonymous();
    setUser(resp.user);
    return resp.user;
  } catch (err) {
    console.error("anonymous login failed", err);
    setUser(null);
    throw err;
  }
}

export async function startGithubLogin() {
  const resp = await authApi.githubLoginUrl();
  if (!resp?.authorize_url) {
    throw new Error("github login url 缺失");
  }
  window.location.href = resp.authorize_url;
}

export async function logout() {
  try { await authApi.logout(); } catch { /* 即便失败也清本地 */ }
  setUser(null);
  // 退出后重新匿名登录，避免界面卡死无 session
  return ensureSession();
}

/**
 * 拿一段简短的展示名（avatar 字母 + display_name + avatar_url）。
 */
export function displayLabels(user) {
  if (!user) return { initial: "?", name: "未登录", provider: "—", avatarUrl: "" };
  const name = user.display_name || user.user_id || "未命名";
  const initial = (name.replace(/^anon:/, "")[0] || "?").toUpperCase();
  const provider =
    user.provider === "anonymous" ? "匿名访客"
    : user.provider === "github"  ? "GitHub 用户"
    : user.provider || "—";
  return { initial, name, provider, avatarUrl: user.avatar_url || "" };
}

// 把 ApiError 暴露给上层用
export { ApiError };
