// 更新说明的渲染（whiteboard/web/static/js/notes.js）。

import assert from "node:assert/strict";
import { test } from "node:test";

import { renderNotes } from "../../whiteboard/web/static/js/notes.js";

test("release_notes_are_escaped：更新说明来自网络，必须当纯文本处理", () => {
  const html = renderNotes("- <img src=x onerror=alert(1)> **粗** `码`\n- [看](https://example.com/a)");
  assert.ok(!html.includes("<img"), html);
  assert.ok(html.includes("&lt;img"), html);
  assert.ok(html.includes("<strong>粗</strong>") && html.includes("<code>码</code>"), html);
  assert.ok(html.includes('<a href="https://example.com/a">看</a>'), html);
});
