// export-pages.js：导出对话框里的页面几何（和 whiteboard/pagination.py 同一套约定）。

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  MARGIN,
  PAPERS,
  contentSheet,
  currentPage,
  drawnFrame,
  exportRequest,
  filenameOf,
  frameAt,
  pixelSize,
  placement,
  rawName,
  resizeFrame,
  roundHalfEven,
} from "../../whiteboard/web/static/js/export-pages.js";

const A4 = PAPERS.a4;

test("页框等比放进留白之内，水平居中、顶端对齐", () => {
  const exact = { x: 0, y: 0, w: A4[0] - 2 * MARGIN, h: A4[1] - 2 * MARGIN, paper: A4 };
  assert.deepEqual(placement(exact), { scale: 1, left: MARGIN, top: MARGIN });
  const tall = { x: 0, y: 0, w: 100, h: 400, paper: [600, 800] };
  const { scale, left, top } = placement(tall);
  assert.equal(scale, (800 - 2 * MARGIN) / 400);
  assert.equal(left, (600 - 100 * scale) / 2);
  assert.equal(top, MARGIN);
  assert.equal(placement({ ...tall, margin: 0 }).top, 0);
});

test("当前页：视口中心所在的那一页，否则最近的一页", () => {
  const pages = [
    { x: 0, y: 0, w: 100, h: 100 },
    { x: 500, y: 0, w: 100, h: 100 },
  ];
  assert.equal(currentPage(pages, 550, 50), 1);
  assert.equal(currentPage(pages, 160, 50), 0);
  assert.equal(currentPage(pages, 400, 50), 1);
  assert.equal(currentPage([], 0, 0), -1);
});

test("PNG 每页的像素：按 dpi 算，超过上限时按比例降", () => {
  assert.deepEqual(pixelSize({ paper: A4 }, 300), [2480, 3508]);
  assert.deepEqual(pixelSize({ paper: [A4[1], A4[0]] }, 150), [1754, 1240]);
  const [w, h] = pixelSize({ paper: [2000, 3000] }, 600);
  assert.ok(w * h <= 40_000_000 + 4000);
});

test("PNG 的像素尺寸和服务端算出来的一模一样（含 4000 万像素的上限）", () => {
  const fixture = JSON.parse(readFileSync(new URL("../fixtures/png-sizes.json", import.meta.url), "utf-8"));
  for (const { paper, dpi, size } of fixture.cases) {
    assert.deepEqual(pixelSize({ paper }, dpi), size, `${paper} @ ${dpi}`);
  }
});

test("拖角缩放：对角不动，宽高比不变，有最小尺寸", () => {
  const frame = { x: 0, y: 0, w: 300, h: 200, paper: [841.89, 595.28] };
  const grown = resizeFrame(frame, "se", 600, 250);
  assert.equal(grown.x, 0);
  assert.equal(grown.y, 0);
  assert.equal(grown.w, 600);
  assert.equal(grown.h, 400);
  const nw = resizeFrame(frame, "nw", 150, -100);
  assert.equal(nw.x + nw.w, 300);
  assert.equal(nw.y + nw.h, 200);
  assert.ok(Math.abs(nw.w / nw.h - 1.5) < 1e-9);
  const tiny = resizeFrame(frame, "se", 1, 1);
  assert.ok(tiny.h >= 40 - 1e-9);
  assert.deepEqual(tiny.paper, frame.paper);
});

test("新加的一页：照着模板的大小，没有模板就按纸张竖放", () => {
  const added = frameAt(100, 100, { w: 200, h: 100, paper: [1, 2] }, A4);
  assert.deepEqual(added, { x: 0, y: 50, w: 200, h: 100, paper: [1, 2] });
  const fresh = frameAt(0, 0, null, [A4[1], A4[0]]);
  assert.deepEqual(fresh.paper, A4);
  assert.equal(fresh.w, A4[0] - 2 * MARGIN);
});

test("请求体：PNG 一页是 png，多页是 zip；文档板用原件的页", () => {
  const frames = [{ x: 0, y: 0, w: 10, h: 20, paper: A4, key: 3 }];
  assert.deepEqual(exportRequest({ format: "pdf", frames, selected: [0], paper: "a4", dpi: 300 }), {
    format: "pdf",
    paper: "a4",
    select: [0],
    frames: [{ x: 0, y: 0, w: 10, h: 20, paper: A4 }],
  });
  assert.equal(exportRequest({ format: "png", frames, selected: [0], paper: "a4", dpi: 150 }).format, "png");
  const zip = exportRequest({ format: "png", frames: null, selected: [2, 0], paper: "letter", dpi: 600 });
  assert.equal(zip.format, "zip");
  assert.equal(zip.dpi, 600);
  assert.deepEqual(zip.select, [0, 2]);
  assert.equal(zip.frames, "auto");
});

test("文件名取 Content-Disposition 里的 filename*", () => {
  assert.equal(filenameOf("attachment; filename*=UTF-8''%E5%91%A8%E4%BC%9A.pdf", "x"), "周会.pdf");
  assert.equal(filenameOf("", "x.pdf"), "x.pdf");
  assert.equal(filenameOf("attachment; filename*=UTF-8''%E0%A4", "x"), "x");
});

test("和 Python 的 round 一样：正好一半取偶数", () => {
  assert.equal(roundHalfEven(100.5), 100);
  assert.equal(roundHalfEven(101.5), 102);
  assert.equal(roundHalfEven(2480.333), 2480);
  assert.equal(roundHalfEven(7.51), 8);
});

test("纸张跟随内容：纸是页框加留白，太大时连留白一起缩到 14400 pt", () => {
  assert.deepEqual(contentSheet(100, 200), [172, 272]);
  const [w, h] = contentSheet(1000, 28728);
  assert.equal(h, 14400);
  assert.ok(Math.abs(w - 1072 / 2) < 1e-9);
});

test("跟随内容时拖角缩放不保持比例，纸跟着页框变", () => {
  const frame = { x: 0, y: 0, w: 300, h: 200, paper: [372, 272], margin: 36 };
  const free = resizeFrame(frame, "se", 500, 100, null);
  assert.deepEqual([free.x, free.y, free.w, free.h], [0, 0, 500, 100]);
  assert.deepEqual(free.paper, [572, 172]);
  assert.equal(free.margin, undefined);
  const tiny = resizeFrame(frame, "nw", 299, 199, null);
  assert.equal(tiny.w, 40);
  assert.equal(tiny.x, 260);
});

test("拖出来的页框：跟随内容时就是拖出的矩形，往哪边拖都行", () => {
  assert.deepEqual(drawnFrame(10, 20, 110, 70, null), { x: 10, y: 20, w: 100, h: 50, paper: [172, 122] });
  const back = drawnFrame(110, 70, 10, 20, null);
  assert.deepEqual([back.x, back.y, back.w, back.h], [10, 20, 100, 50]);
  const thin = drawnFrame(0, 0, 300, 2, null);
  assert.equal(thin.h, 40);
});

test("拖出来的页框：选了 A4 时按拖的形状横放或竖放，保持内容区的比例", () => {
  const wide = drawnFrame(0, 0, 400, 100, A4);
  assert.deepEqual(wide.paper, [A4[1], A4[0]]);
  assert.ok(Math.abs(wide.w / wide.h - (A4[1] - 72) / (A4[0] - 72)) < 1e-9);
  assert.equal(wide.w, 400);
  const tall = drawnFrame(0, 0, -100, -600, A4);
  assert.deepEqual(tall.paper, A4);
  assert.ok(Math.abs(tall.h - 600) < 1e-9);
  assert.equal(tall.x + tall.w, 0);
  assert.equal(tall.y + tall.h, 0);
});

test("跟随内容时新加的一页是竖放 A4 内容区那么大，纸是它加留白", () => {
  const added = frameAt(0, 0, null, null);
  assert.equal(added.w, A4[0] - 2 * MARGIN);
  assert.ok(Math.abs(added.paper[0] - A4[0]) < 1e-9);
  assert.ok(Math.abs(added.paper[1] - A4[1]) < 1e-9);
});

test("白板文件的文件名照服务端的规则", () => {
  assert.equal(rawName("周会"), "周会.wbz");
  assert.equal(rawName("课堂/笔记"), "课堂-笔记.wbz");
  assert.equal(rawName(" .x. "), "x.wbz");
  assert.equal(rawName("", new Date(2026, 10, 2)), "whiteboard-2026-11-02.wbz");
  assert.equal(rawName("字".repeat(100)), `${"字".repeat(80)}.wbz`);
});

test("请求体：白板文件只要格式；整块不带页和选页；跟随内容时页框不带纸", () => {
  assert.deepEqual(exportRequest({ format: "wbz", frames: [], selected: [0], paper: "content", dpi: 300 }), {
    format: "wbz",
  });
  assert.deepEqual(exportRequest({ format: "png", frames: "whole", selected: [], paper: "content", dpi: 150 }), {
    format: "png",
    paper: "content",
    frames: "whole",
    dpi: 150,
  });
  assert.deepEqual(exportRequest({ format: "pdf", frames: "whole", selected: [], paper: "a4", dpi: 150 }), {
    format: "pdf",
    paper: "a4",
    frames: "whole",
  });
  const content = exportRequest({
    format: "pdf",
    frames: [{ x: 1, y: 2, w: 3, h: 4, paper: [75, 76], margin: 36, key: 1 }],
    selected: [0],
    paper: "content",
    dpi: 300,
  });
  assert.deepEqual(content.frames, [{ x: 1, y: 2, w: 3, h: 4 }]);
});
