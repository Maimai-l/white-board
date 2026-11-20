// 导出对话框「页面」那一栏的页码：把用户敲的「1-5、8、11-13」读成页的序号，
// 再把选中的页写回同样的写法。纯函数，不碰页面，node 里直接测（tests/js/page-range.test.mjs）。

const RANGE = "[-–—~～－]";
const SPACE_AROUND_RANGE = new RegExp(`\\s*(${RANGE})\\s*`, "g");
const SEPARATORS = /[、,，;；\s]+/;
const NUMBER = /^\d+$/;
const SPAN = new RegExp(`^(\\d+)${RANGE}(\\d+)$`);

export const SYNTAX_ERROR = "页码写成 1-5、8 这样";

/** 全角数字换成半角：中文输入法下敲出来的「１-３」也认。 */
function halfWidth(text) {
  return text.replace(/[０-９]/g, (ch) => String.fromCharCode(ch.charCodeAt(0) - 0xfee0));
}

/**
 * 读页码。``count`` 是总页数。返回 ``{pages, error}``：``pages`` 是从 0 数的页序号
 * （去重、从小到大），``error`` 是要显示在输入框下面的一句话，没错时是 null。
 *
 * 分隔符认 、 , ， ; ； 和空格；范围认 - – — ~ ～ －，两边可以有空格；「5-3」当成
 * 「3-5」。空着不算错，只是一页都没选。永远不抛异常。
 */
export function parsePages(text, count) {
  const source = halfWidth(String(text ?? "")).trim();
  if (!source) return { pages: [], error: null };
  const tokens = source.replace(SPACE_AROUND_RANGE, "$1").split(SEPARATORS).filter(Boolean);
  const spans = [];
  for (const token of tokens) {
    if (NUMBER.test(token)) {
      const n = Number(token);
      spans.push([n, n]);
      continue;
    }
    const match = SPAN.exec(token);
    if (!match) return { pages: [], error: SYNTAX_ERROR };
    const a = Number(match[1]);
    const b = Number(match[2]);
    spans.push([Math.min(a, b), Math.max(a, b)]);
  }
  if (spans.some(([a]) => a < 1)) return { pages: [], error: "页码从 1 开始" };
  if (spans.some(([, b]) => b > count)) {
    return { pages: [], error: count > 0 ? `只有 ${count} 页` : "没有可以导出的页" };
  }
  const picked = new Set();
  for (const [a, b] of spans) for (let n = a; n <= b; n += 1) picked.add(n - 1);
  return { pages: [...picked].sort((x, y) => x - y), error: null };
}

/** 把从 0 数的页序号写成「1-3、8」：连着的两页以上写成范围。 */
export function formatPages(indices) {
  const sorted = [...new Set(indices)].sort((a, b) => a - b);
  const parts = [];
  let start = null;
  let prev = null;
  const flush = () => {
    if (start === null) return;
    parts.push(start === prev ? String(start + 1) : `${start + 1}-${prev + 1}`);
  };
  for (const index of sorted) {
    if (prev !== null && index === prev + 1) {
      prev = index;
      continue;
    }
    flush();
    start = index;
    prev = index;
  }
  flush();
  return parts.join("、");
}
