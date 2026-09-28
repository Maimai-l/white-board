// 把 GitHub Release 的更新说明（Markdown 子集）渲染成 HTML。
//
// 这段文字来自网络，所以先整体转义，再只把我们认识的几种写法变成标签：
// 标题、无序列表、粗体、行内代码、链接。其余一律当纯文本。

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

function escapeHtml(text) {
  return String(text == null ? "" : text).replace(/[&<>"']/g, (ch) => ESCAPES[ch]);
}

/** GitHub 的 PR 链接显示成 #1285，其余去掉协议头并截断。 */
function linkLabel(url) {
  const pull = url.match(/github\.com\/[^/]+\/[^/]+\/(?:pull|issues)\/(\d+)/);
  if (pull) return `#${pull[1]}`;
  const bare = url.replace(/^https?:\/\//, "");
  return bare.length > 42 ? `${bare.slice(0, 39)}…` : bare;
}

function inline(text) {
  return text
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(
      /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      (match, label, href) => `<a href="${href}">${label}</a>`
    )
    .replace(
      /(^|[\s(])(https?:\/\/[^\s<)]+)/g,
      (match, prefix, href) => `${prefix}<a href="${href}">${linkLabel(href)}</a>`
    );
}

export function renderNotes(markdown) {
  const lines = escapeHtml(markdown).split(/\r?\n/);
  const out = [];
  let inList = false;
  for (const raw of lines) {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    if (bullet) {
      if (!inList) {
        out.push("<ul>");
        inList = true;
      }
      out.push(`<li>${inline(bullet[1])}</li>`);
      continue;
    }
    if (inList) {
      out.push("</ul>");
      inList = false;
    }
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      out.push(`<h4>${inline(heading[2])}</h4>`);
      continue;
    }
    if (!line.trim()) continue;
    out.push(`<p>${inline(line)}</p>`);
  }
  if (inList) out.push("</ul>");
  return out.join("");
}
