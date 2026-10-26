import { renderRiskPilotFlowcharts } from "./flowchart.js?v=20261023-flow7";

// All assistant Markdown, including restored history, passes through this sink.
export function renderSafeMarkdown(element, markdown) {
  if (!window.marked || !window.DOMPurify) {
    element.textContent = markdown;
    return;
  }
  const renderer = new window.marked.Renderer();
  renderer.html = ({ text }) => text.replace(/&/g, "&amp;")
    .replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const fragment = window.DOMPurify.sanitize(window.marked.parse(markdown, { renderer }), {
    ALLOWED_TAGS: ["p", "br", "hr", "strong", "b", "em", "i", "del", "s", "blockquote",
      "pre", "code", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
      "table", "thead", "tbody", "tr", "th", "td", "a"],
    ALLOWED_ATTR: ["href", "title", "class"],
    ALLOW_DATA_ATTR: false,
    RETURN_DOM_FRAGMENT: true,
  });
  for (const link of fragment.querySelectorAll("a[href]")) {
    try {
      const url = new URL(link.getAttribute("href"), window.location.href);
      if (!["http:", "https:", "mailto:"].includes(url.protocol)) {
        link.removeAttribute("href");
      } else {
        link.setAttribute("rel", "noopener noreferrer");
      }
    } catch {
      link.removeAttribute("href");
    }
  }
  decorateInlineCitations(fragment);
  renderRiskPilotFlowcharts(fragment);
  element.replaceChildren(fragment);
}

function decorateInlineCitations(fragment) {
  const citationPattern = /\[SRC-[A-Za-z0-9_-]{1,64}\]/g;
  const walker = document.createTreeWalker(fragment, NodeFilter.SHOW_TEXT);
  const targets = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (
      citationPattern.test(node.nodeValue || "")
      && !node.parentElement?.closest("code, pre, a, .inline-citation")
    ) {
      targets.push(node);
    }
    citationPattern.lastIndex = 0;
  }

  for (const node of targets) {
    const text = node.nodeValue || "";
    const replacement = document.createDocumentFragment();
    let cursor = 0;
    for (const match of text.matchAll(citationPattern)) {
      replacement.append(document.createTextNode(text.slice(cursor, match.index)));
      const citation = document.createElement("span");
      citation.className = "inline-citation";
      citation.textContent = match[0];
      citation.title = `引用证据 ${match[0].slice(1, -1)}`;
      replacement.append(citation);
      cursor = match.index + match[0].length;
    }
    replacement.append(document.createTextNode(text.slice(cursor)));
    node.replaceWith(replacement);
  }
}
