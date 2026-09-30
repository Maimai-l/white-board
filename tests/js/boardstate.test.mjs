// boardstate.js：层叠顺序、空间索引。

import assert from "node:assert/strict";
import { test } from "node:test";

import { BoardState } from "../../packages/inksync/inksync/web/boardstate.js";

function dot(id, x, y, n) {
  const stroke = { id, tool: "pen", color: "#000000", w: 4, p: [x, y, 0.5, x + 1, y + 1, 0.5] };
  if (n !== undefined) stroke.n = n;
  return stroke;
}

function fresh(strokes = []) {
  const state = new BoardState();
  state.reset({ id: "b", kind: "board" }, strokes);
  return state;
}

test("按层叠序号 n 排序，不管加进来的先后", () => {
  const state = fresh([dot("c", 0, 0, 5), dot("a", 0, 0, 1), dot("b", 0, 0, 3)]);
  assert.deepEqual(state.strokes.map((s) => s.id), ["a", "b", "c"]);
});

test("没有 n 的笔画排在最后；重复 id 不会加两遍", () => {
  const state = fresh([dot("a", 0, 0, 7)]);
  const { added } = state.add([dot("b", 0, 0), dot("a", 0, 0, 7)]);
  assert.deepEqual(added.map((s) => s.id), ["b"]);
  assert.deepEqual(state.strokes.map((s) => s.id), ["a", "b"]);
  assert.ok(state.byId.get("b").n > 7);
});

test("回执带来的正式序号会改变顺序", () => {
  const state = fresh([dot("a", 0, 0, 1), dot("b", 0, 0, 2)]);
  state.add([{ ...dot("a", 0, 0), n: 9 }]);
  assert.deepEqual(state.strokes.map((s) => s.id), ["b", "a"]);
});

test("near 只给出扫过的那几格里的笔画，远处的不在里面", () => {
  const state = fresh([dot("near", 10, 10, 0), dot("far", 5000, 5000, 1)]);
  const ids = state.near(0, 0, 20, 20, 5).map((s) => s.id);
  assert.ok(ids.includes("near"));
  assert.ok(!ids.includes("far"));
});

test("删掉的笔画从顺序和索引里一起消失", () => {
  const state = fresh([dot("a", 10, 10, 0), dot("b", 10, 10, 1)]);
  const removed = state.remove(["a", "missing"]);
  assert.deepEqual(removed.map((s) => s.id), ["a"]);
  assert.deepEqual(state.strokes.map((s) => s.id), ["b"]);
  assert.ok(!state.near(0, 0, 20, 20, 5).some((s) => s.id === "a"));
});

test("hitTest 由新到旧返回命中的笔画", () => {
  const state = fresh([dot("a", 10, 10, 0), dot("b", 10, 10, 1)]);
  assert.deepEqual(state.hitTest(10, 10, 5, () => true), ["b", "a"]);
});

test("书写范围来自画布", () => {
  const state = new BoardState();
  state.reset({ id: "a", canvas: { mode: "infinite" } }, []);
  assert.equal(state.limits, null);
  assert.equal(state.kind, "board");
  state.reset({ id: "b", canvas: { mode: "column", width: 1000 } }, []);
  assert.deepEqual(state.limits, { x0: 0, x1: 1000, y0: 0 });
  assert.equal(state.kind, "note");
  state.reset({ id: "c", canvas: { mode: "fixed", width: 800, height: 1200 } }, []);
  assert.deepEqual(state.limits, { x0: 0, x1: 800, y0: 0, y1: 1200 });
  state.reset({ id: "d" }, []);
  assert.equal(state.limits, null);
});
