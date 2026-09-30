// util.js 里判断「这个按键归不归白板」的两个小函数。

import assert from "node:assert/strict";
import { test } from "node:test";

import { isComposing, isTextField } from "../../packages/inksync/inksync/web/util.js";

function node(tagName, attrs = {}, extra = {}) {
  return { nodeType: 1, tagName, getAttribute: (name) => attrs[name] ?? null, isContentEditable: false, ...extra };
}

test("文本输入框、多行文本、可编辑区域算能打字的地方", () => {
  assert.equal(isTextField(node("INPUT")), true);
  assert.equal(isTextField(node("INPUT", { type: "text" })), true);
  assert.equal(isTextField(node("INPUT", { type: "search" })), true);
  assert.equal(isTextField(node("TEXTAREA")), true);
  assert.equal(isTextField(node("DIV", {}, { isContentEditable: true })), true);
});

test("勾选框、按钮、画布和空值不算", () => {
  for (const type of ["checkbox", "radio", "button", "file", "range"]) {
    assert.equal(isTextField(node("INPUT", { type })), false, type);
  }
  assert.equal(isTextField(node("CANVAS")), false);
  assert.equal(isTextField(node("BUTTON")), false);
  assert.equal(isTextField(null), false);
  assert.equal(isTextField({ nodeType: 9 }), false); // document
});

test("输入法组字中的按键：isComposing 或 keyCode 229", () => {
  assert.equal(isComposing({ isComposing: true, keyCode: 13 }), true);
  assert.equal(isComposing({ isComposing: false, keyCode: 229 }), true);
  assert.equal(isComposing({ isComposing: false, keyCode: 13 }), false);
});
