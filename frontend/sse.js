/**
 * sse.js — fetch + ReadableStream 实现 SSE 客户端。
 *
 * 为什么不用 EventSource：EventSource 只支持 GET，无法带 JSON body。
 * 我们的 `/api/v2/copilot/chat/stream` 是 POST + JSON，必须用 fetch。
 *
 * 帧格式（与 api/v2/sse.py 对齐）：
 *   event: thought
 *   data: {"text":"..."}
 *   \n\n
 *
 * 心跳帧以 `:` 开头，按 SSE 规范作为注释忽略。
 */

/**
 * 起一个流式 chat 请求。
 *
 * @param {object} body  - {task_id?, message, attachment_doc_ids?}
 * @param {object} cb    - { onEvent, onError, onDone }
 * @returns {AbortController} 调用方可 .abort() 取消
 */
export function streamChat(body, { onEvent, onError, onDone }) {
  const ac = new AbortController();
  const url = "/api/v2/copilot/chat/stream";

  (async () => {
    try {
      const resp = await fetch(url, {
        method: "POST",
        credentials: "include",
        signal: ac.signal,
        headers: {
          "Content-Type": "application/json",
          "Accept": "text/event-stream",
        },
        body: JSON.stringify(body),
      });

      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try {
          const j = await resp.json();
          detail = j?.message || j?.detail?.message || j?.error_code || detail;
        } catch { /* keep default */ }
        onError?.(new Error(detail));
        return;
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buf = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });

        // 按 \n\n 切帧
        let sepIdx;
        while ((sepIdx = buf.indexOf("\n\n")) !== -1) {
          const raw = buf.slice(0, sepIdx);
          buf = buf.slice(sepIdx + 2);
          const frame = parseFrame(raw);
          if (frame) onEvent?.(frame);
        }
      }
      onDone?.();
    } catch (err) {
      if (err.name === "AbortError") return; // 主动取消，不报错
      onError?.(err);
    }
  })();

  return ac;
}

function parseFrame(raw) {
  const block = raw.trim();
  if (!block || block.startsWith(":")) return null;  // 心跳/空帧

  let event = "message";
  let dataStr = "";
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataStr = line.slice(5).trim();
  }
  let data = null;
  if (dataStr) {
    try { data = JSON.parse(dataStr); } catch { data = { raw: dataStr }; }
  }
  return { event, data };
}
