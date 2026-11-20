// page-range.js：导出对话框里「页面」输入框的页码读写。

import assert from "node:assert/strict";
import { test } from "node:test";

import { SYNTAX_ERROR, formatPages, parsePages } from "../../whiteboard/web/static/js/page-range.js";

const ok = (pages) => ({ pages, error: null });

test("单页、范围和各种分隔符", () => {
  assert.deepEqual(parsePages("1-2、4", 21), ok([0, 1, 3]));
  assert.deepEqual(parsePages("1,3，5;7；9", 21), ok([0, 2, 4, 6, 8]));
  assert.deepEqual(parsePages("1 3  5", 21), ok([0, 2, 4]));
  assert.deepEqual(parsePages("11–13", 21), ok([10, 11, 12]));
  assert.deepEqual(parsePages("2~3", 21), ok([1, 2]));
  assert.deepEqual(parsePages("2～3、5—6", 21), ok([1, 2, 4, 5]));
});

test("两边和范围符号旁边的空格不算", () => {
  assert.deepEqual(parsePages("  1 - 3 、 8  ", 21), ok([0, 1, 2, 7]));
  assert.deepEqual(parsePages(" 2 ，3 ", 21), ok([1, 2]));
});

test("重复的页只算一次，倒着写的范围也认", () => {
  assert.deepEqual(parsePages("3、1-3、2", 21), ok([0, 1, 2]));
  assert.deepEqual(parsePages("5-3", 21), ok([2, 3, 4]));
});

test("全角数字也认", () => {
  assert.deepEqual(parsePages("１-３", 21), ok([0, 1, 2]));
});

test("空着不是错，只是一页都没选", () => {
  assert.deepEqual(parsePages("", 21), ok([]));
  assert.deepEqual(parsePages("  ", 21), ok([]));
  assert.deepEqual(parsePages(undefined, 21), ok([]));
  assert.deepEqual(parsePages("、", 21), ok([]));
});

test("超出页数：说一共几页", () => {
  assert.deepEqual(parsePages("1-22", 21), { pages: [], error: "只有 21 页" });
  assert.deepEqual(parsePages("30", 21), { pages: [], error: "只有 21 页" });
  assert.deepEqual(parsePages("1-99999999999999999999", 4), { pages: [], error: "只有 4 页" });
  assert.deepEqual(parsePages("0", 21), { pages: [], error: "页码从 1 开始" });
  assert.equal(parsePages("1", 0).error, "没有可以导出的页");
});

test("写法不对：说格式不对，不抛异常", () => {
  for (const text of ["x", "1、x", "1-", "-3", "1--3", "1-2-3", "1.5", "第1页", "1-3a"]) {
    assert.deepEqual(parsePages(text, 21), { pages: [], error: SYNTAX_ERROR }, text);
  }
  assert.equal(SYNTAX_ERROR, "页码格式不对");
});

test("选中的页写成紧凑的写法", () => {
  assert.equal(formatPages([0, 1, 2, 7]), "1-3, 8");
  assert.equal(formatPages([1, 3]), "2, 4");
  assert.equal(formatPages([0, 1, 3]), "1-2, 4");
  assert.equal(formatPages([7, 0, 2, 1, 1]), "1-3, 8");
  assert.equal(formatPages([4]), "5");
  assert.equal(formatPages([]), "");
});

test("写出来的再读回去是同一组页", () => {
  const pages = [0, 2, 3, 4, 9, 11, 12];
  assert.deepEqual(parsePages(formatPages(pages), 13), ok(pages));
});
