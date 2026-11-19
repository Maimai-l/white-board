// export-pages.js：导出对话框里的页面几何（和 whiteboard/pagination.py 同一套约定）。

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MARGIN,
  PAPERS,
  currentPage,
  exportRequest,
  filenameOf,
  frameAt,
  pixelSize,
  placement,
  resizeFrame,
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
