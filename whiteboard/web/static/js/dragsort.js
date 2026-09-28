// 白板选择界面里把卡片拖来拖去：归进文件夹、移出来、调顺序。
//
// 手感照 iPadOS 主屏幕整理图标那一套（调参过程见 static/lab/drag.html 的「启动台」
// 预设，那个页面可以在真机上开着改数值）。要点：
//
// - 拖的是卡片条目本身，不是一份副本。原来那一格换成一个同尺寸的隐形空位，
//   条目挪到 body 上跟着指针走，松手再放回空位那里。副本的做法在「拎起来放大」
//   的时候会露馅：副本在上面、原件在下面，一放大就看得出是两张。
// - 按住的时候先缩一点（PRESS_SCALE），到点了再弹起来放大（LIFT）。这两段和
//   让位、落位一样走弹簧曲线，参数与 SwiftUI 的 Spring(duration:bounce:) 同义，
//   由 spring() 算成 CSS linear()。Safari 17.2 以下不支持 linear()，退回三次贝塞尔。
// - 别的卡片不是碰到就让位：指针要在同一个插入位置停够 REORDER_DELAY 才让，
//   否则从一头拖到另一头的路上，整片卡片会一直翻。
// - 文件夹只认卡片中间 FOLDER_HIT 那一块，边上算路过。不然沿着一排卡片拖过去，
//   蹭到文件夹边上就被收进去了。
//
// 鼠标和手指走同一套，区别只在起拖：手指要按住 HOLD_MS，鼠标挪够 MOUSE_SLOP 就走。
// 不用浏览器自带的拖放（HTML5 drag and drop）：iOS Safari 根本不发那些事件，而且
// 拖影的样子和动画都由浏览器定，两端不可能一致。

/** 手感参数。改这里之前先在 static/lab/drag.html 上按真机调，调完把数值搬过来。 */
export const TUNING = {
  HOLD_MS: 400, // 手指按住多久才开始拖
  HOLD_SLOP: 10, // 按住期间挪过这个距离就当是在滚列表
  MOUSE_SLOP: 4, // 鼠标按下之后挪过这个距离就算起拖
  PRESS_SCALE: 0.96, // 按住期间缩到
  LIFT: 1.12, // 拎起来之后放大到
  FOLDER_SCALE: 1.18, // 指到文件夹上时它放大到
  REORDER_DELAY: 180, // 在同一个插入位置停多久才让位
  FOLDER_HIT: 0.6, // 文件夹卡片中间多大一块算「放进去」
  LIFT_RESPONSE: 300, // 拎起 / 文件夹放大的弹簧周期
  LIFT_BOUNCE: 0.2,
  FLIP_RESPONSE: 400, // 让位
  FLIP_BOUNCE: 0.15,
  SETTLE_RESPONSE: 450, // 落位
  SETTLE_BOUNCE: 0.2,
  INTO_SCALE: 0.3, // 落进文件夹时缩到原尺寸的几分之一再淡出
  EDGE_ZONE: 72, // 离上下边多近开始自动滚
  EDGE_SPEED: 16, // 自动滚每帧最多几个像素
};

const SHADOW_REST = "0 1px 3px rgba(0, 0, 0, .1)";
const SHADOW_LIFT = "0 18px 40px rgba(0, 0, 0, .2)";

const LINEAR_OK = !!(
  window.CSS &&
  CSS.supports &&
  CSS.supports("transition-timing-function", "linear(0, 1)")
);

/**
 * 阻尼谐振子的解析解，采样成 CSS ``linear()``。
 *
 * ``responseMs`` 和 ``bounce`` 的含义与 SwiftUI 的 ``Spring(duration:bounce:)``
 * 相同：固有周期就是 responseMs，阻尼比是 1 - bounce（bounce 为负时取
 * 1 / (1 + bounce)）。返回的 ms 是位移与终点相差不到 0.1% 的时刻，也就是这段
 * 动画实际要跑多久。
 */
export function spring(responseMs, bounce) {
  const period = Math.max(responseMs, 16) / 1000;
  const b = Math.max(-0.95, Math.min(0.95, bounce || 0));
  const w = (2 * Math.PI) / period;
  const z = b >= 0 ? 1 - b : 1 / (1 + b);
  let x;
  if (Math.abs(z - 1) < 1e-6) {
    x = (t) => 1 - Math.exp(-w * t) * (1 + w * t);
  } else if (z < 1) {
    const wd = w * Math.sqrt(1 - z * z);
    x = (t) => 1 - Math.exp(-z * w * t) * (Math.cos(wd * t) + ((z * w) / wd) * Math.sin(wd * t));
  } else {
    const r1 = -w * (z - Math.sqrt(z * z - 1));
    const r2 = -w * (z + Math.sqrt(z * z - 1));
    x = (t) => 1 - (r2 * Math.exp(r1 * t) - r1 * Math.exp(r2 * t)) / (r2 - r1);
  }
  let end = 0;
  for (let t = 0; t <= 4; t += 0.002) if (Math.abs(1 - x(t)) > 0.001) end = t;
  end += 0.002;
  const ms = Math.round(end * 1000);
  if (!LINEAR_OK) {
    // Safari 17.2 以下：没有 linear()，用一条形状接近的三次贝塞尔顶上
    return {
      ms: Math.round(ms * 0.8),
      easing: b > 0 ? "cubic-bezier(.3, 1.3, .5, 1)" : "cubic-bezier(.2, .8, .2, 1)",
    };
  }
  const n = Math.max(24, Math.min(120, Math.round(end * 90)));
  const points = [];
  for (let i = 0; i < n; i++) points.push(+x((end * i) / n).toFixed(4));
  points.push(1);
  return { ms, easing: `linear(${points.join(", ")})` };
}

function currentScale(node) {
  const value = getComputedStyle(node).scale;
  if (!value || value === "none") return 1;
  const n = parseFloat(value);
  return Number.isFinite(n) ? n : 1;
}

/** 条目的缩放中心放在卡片中心，名字跟着卡片一起缩，卡片本身不会偏。 */
function originFor(item, card) {
  return `50% ${card.offsetTop + card.offsetHeight / 2}px`;
}

/**
 * FLIP：记住动之前每一格在哪，改完 DOM 让它们从原位滑过来。
 * 不这样的话重排是一下跳过去的。
 */
function flip(grid, mutate) {
  const before = new Map();
  for (const node of grid.children) before.set(node, node.getBoundingClientRect());
  mutate();
  const s = spring(TUNING.FLIP_RESPONSE, TUNING.FLIP_BOUNCE);
  for (const node of grid.children) {
    const first = before.get(node);
    if (!first) continue;
    const last = node.getBoundingClientRect();
    const dx = first.left - last.left;
    const dy = first.top - last.top;
    if (!dx && !dy) continue;
    node.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }], {
      duration: s.ms,
      easing: s.easing,
    });
  }
}

/** 指到文件夹上它放大一下，离开缩回去。 */
function pop(node, value) {
  if (!node || node.dataset.folder === undefined) return;
  if (value !== 1 && TUNING.FOLDER_SCALE === 1) return;
  if (value === 1 && !node._pop) return;
  const from = currentScale(node);
  if (node._pop) node._pop.cancel();
  const s = spring(TUNING.LIFT_RESPONSE, TUNING.LIFT_BOUNCE);
  const anim = node.animate([{ scale: String(from) }, { scale: String(value) }], {
    duration: s.ms,
    easing: s.easing,
    fill: "forwards",
  });
  if (value === 1) {
    node._pop = null;
    anim.onfinish = () => anim.cancel();
  } else {
    node._pop = anim;
  }
}

/**
 * 松手：条目从指针底下移到落点。
 *
 * ``point`` 是目标点（空位里那张卡片的中心，或者文件夹的中心），空的话原地收。
 * ``into`` 为真表示落进文件夹或移出文件夹，缩小并淡出；否则回到空位，保持不透明，
 * 到位之后由调用方把条目换回格子里。
 */
function settle(item, card, anchor, point, into, onDone) {
  const fromScale = currentScale(item);
  const fromShadow = getComputedStyle(card).boxShadow;
  const fromTranslate = item.style.translate || "0px 0px";
  if (item._lift) {
    item._lift.cancel();
    item._lift = null;
  }
  if (card._shadow) {
    card._shadow.cancel();
    card._shadow = null;
  }
  let tx;
  let ty;
  if (point) {
    tx = point.x - anchor.x;
    ty = point.y - anchor.y;
  } else {
    const parts = fromTranslate.split(" ").map(parseFloat);
    tx = parts[0] || 0;
    ty = parts[1] || 0;
  }
  const s = spring(TUNING.SETTLE_RESPONSE, into ? 0 : TUNING.SETTLE_BOUNCE);
  const anim = item.animate(
    [
      { translate: fromTranslate, scale: String(fromScale), opacity: "1" },
      {
        translate: `${tx}px ${ty}px`,
        scale: String(into ? TUNING.INTO_SCALE : 1),
        opacity: into ? "0" : "1",
      },
    ],
    { duration: s.ms, easing: s.easing, fill: "forwards" }
  );
  card.animate([{ boxShadow: fromShadow }, { boxShadow: SHADOW_REST }], {
    duration: s.ms,
    easing: s.easing,
    fill: "forwards",
  });
  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    onDone();
  };
  anim.onfinish = finish;
  anim.oncancel = finish;
}

/**
 * 卡片上按下去了：手指按住 ``HOLD_MS`` 才开始拖，鼠标挪够 ``MOUSE_SLOP`` 就开始。
 *
 * ``ctx`` 要给的东西：
 *   ``card`` / ``item`` / ``id``  这张卡片、它那一格、这块白板的 id
 *   ``grid`` / ``gallery`` / ``root``  格子、能滚的那层、界面根节点
 *   ``folderOf(id)``    这块白板现在在哪个文件夹（空串表示没归类）
 *   ``onFile(id, folder)``  落在文件夹或者「移出」上
 *   ``onReorder(ids)``  顺序变了，参数是格子里现在的先后
 *   ``onRefresh()``     拖动被系统取消，重铺一遍退回原样
 */
export function startCardDrag(event, ctx) {
  if (event.isPrimary === false || event.button > 0) return;
  const { card, item } = ctx;
  if (!item || !item.isConnected) return;
  const at = { x: event.clientX, y: event.clientY };
  const mouse = event.pointerType === "mouse";
  let timer = 0;
  let press = null;

  const detach = () => {
    clearTimeout(timer);
    removeEventListener("pointermove", onMove);
    removeEventListener("pointerup", giveUp);
    removeEventListener("pointercancel", giveUp);
  };
  // 没拖起来就松手了：从当前缩放弹回原样
  const giveUp = () => {
    detach();
    if (!press) return;
    const now = currentScale(item);
    press.cancel();
    press = null;
    if (Math.abs(now - 1) < 0.001) return;
    const s = spring(TUNING.LIFT_RESPONSE, TUNING.LIFT_BOUNCE);
    item.animate([{ scale: String(now) }, { scale: "1" }], { duration: s.ms, easing: s.easing });
  };
  function onMove(move) {
    const far = Math.hypot(move.clientX - at.x, move.clientY - at.y);
    if (mouse) {
      if (far <= TUNING.MOUSE_SLOP) return;
      detach();
      drag(ctx, { x: move.clientX, y: move.clientY }, event.pointerId, 1);
      return;
    }
    if (far > TUNING.HOLD_SLOP) giveUp(); // 手指在滚列表，这一次不拖
  }
  addEventListener("pointermove", onMove);
  addEventListener("pointerup", giveUp);
  addEventListener("pointercancel", giveUp);
  if (mouse) return;

  item.style.transformOrigin = originFor(item, card);
  press = item.animate([{ scale: "1" }, { scale: String(TUNING.PRESS_SCALE) }], {
    duration: TUNING.HOLD_MS,
    easing: "cubic-bezier(.4, 0, .6, 1)",
    fill: "forwards",
  });
  timer = setTimeout(() => {
    detach();
    const from = press ? currentScale(item) : 1;
    if (press) press.cancel();
    press = null;
    drag(ctx, at, event.pointerId, from);
  }, TUNING.HOLD_MS);
}

/** 真正开始拖。条目挪到 body 上跟着指针走，格子里留一个隐形空位。 */
function drag(ctx, at, pointerId, startScale) {
  const { card, item, id, grid, gallery, root } = ctx;
  if (!item.isConnected || !grid.isConnected) return;

  const itemBox = item.getBoundingClientRect();
  const cardBox = card.getBoundingClientRect();
  const slot = item.cloneNode(true);
  slot.classList.add("slot");
  delete slot.dataset.board;
  grid.insertBefore(slot, item);

  root.append(item);
  item.classList.add("lifted");
  item.style.left = `${itemBox.left}px`;
  item.style.top = `${itemBox.top}px`;
  item.style.width = `${itemBox.width}px`;
  item.style.translate = "0px 0px";
  item.style.transformOrigin = originFor(item, card);

  // 条目上与卡片中心重合的那个点，落位时让它对准目标
  const anchor = { x: cardBox.left + cardBox.width / 2, y: cardBox.top + cardBox.height / 2 };
  const lift = spring(TUNING.LIFT_RESPONSE, TUNING.LIFT_BOUNCE);
  item._lift = item.animate([{ scale: String(startScale) }, { scale: String(TUNING.LIFT) }], {
    duration: lift.ms,
    easing: lift.easing,
    fill: "forwards",
  });
  card._shadow = card.animate([{ boxShadow: SHADOW_REST }, { boxShadow: SHADOW_LIFT }], {
    duration: lift.ms,
    easing: lift.easing,
    fill: "forwards",
  });

  const pos = { x: at.x, y: at.y };
  let target = null; // 停在文件夹或者「移出」上时是那个元素
  let folder = null; // 对应的落点：文件夹名，或者空串表示移出
  let sorted = false;
  let raf = 0;
  let pendingRef;
  let pendingTimer = 0;

  const follow = () => {
    item.style.translate = `${pos.x - at.x}px ${pos.y - at.y}px`;
  };
  const light = (node, name) => {
    if (target === node) return;
    if (target) {
      target.classList.remove("drop-target");
      pop(target, 1);
    }
    target = node;
    folder = name;
    if (target) {
      target.classList.add("drop-target");
      pop(target, TUNING.FOLDER_SCALE);
    }
  };
  const clearPending = () => {
    clearTimeout(pendingTimer);
    pendingRef = undefined;
  };
  // 让位要等 REORDER_DELAY，这段时间里格子可能已经被重铺过一轮（别处改了名、
  // 新建、广播回来的列表）。所以动手之前按父节点核对一遍：空位和参照点都还在
  // 这个格子里才动，否则这一下作废。
  const moveSlot = (ref) => {
    if (slot.parentNode !== grid) return;
    if (ref && ref.parentNode !== grid) return;
    if (ref === slot || slot.nextSibling === ref) return;
    flip(grid, () => grid.insertBefore(slot, ref));
    sorted = true;
  };
  // 在同一个插入位置停够了才让位；中途换了位置就重新计时
  const queueMove = (ref) => {
    if (ref === slot || slot.nextSibling === ref) return clearPending();
    if (TUNING.REORDER_DELAY <= 0) return moveSlot(ref);
    if (ref === pendingRef) return;
    clearTimeout(pendingTimer);
    pendingRef = ref;
    pendingTimer = setTimeout(() => {
      pendingRef = undefined;
      moveSlot(ref);
    }, TUNING.REORDER_DELAY);
  };
  const inCenter = (node) => {
    const box = node.getBoundingClientRect();
    return (
      Math.abs(pos.x - (box.left + box.width / 2)) <= (box.width * TUNING.FOLDER_HIT) / 2 &&
      Math.abs(pos.y - (box.top + box.height / 2)) <= (box.height * TUNING.FOLDER_HIT) / 2
    );
  };
  const hitTest = () => {
    if (slot.parentNode !== grid) return clearPending();
    const under = document.elementFromPoint(pos.x, pos.y);
    if (!under || !under.closest) {
      clearPending();
      return light(null, null);
    }
    const into = under.closest("[data-folder]");
    if (into) {
      clearPending();
      const hit = inCenter(into);
      return light(hit ? into : null, hit ? into.dataset.folder : null);
    }
    const out = under.closest("[data-drop-out]");
    if (out) {
      clearPending();
      return light(out, "");
    }
    light(null, null);
    const over = under.closest(".board-item[data-board]");
    if (!over || over === slot) return clearPending();
    const box = over.getBoundingClientRect();
    queueMove(pos.x > box.left + box.width / 2 ? over.nextSibling : over);
  };
  // 拖到上下边上自动滚，不然够不到屏幕外那几块
  const tick = () => {
    raf = requestAnimationFrame(tick);
    if (!gallery.isConnected) return;
    const view = gallery.getBoundingClientRect();
    let dy = 0;
    if (pos.y < view.top + TUNING.EDGE_ZONE) dy = -(view.top + TUNING.EDGE_ZONE - pos.y);
    else if (pos.y > view.bottom - TUNING.EDGE_ZONE) dy = pos.y - (view.bottom - TUNING.EDGE_ZONE);
    if (!dy) return;
    const step = Math.max(-TUNING.EDGE_SPEED, Math.min(TUNING.EDGE_SPEED, dy / 3));
    const before = gallery.scrollTop;
    gallery.scrollTop += step;
    if (gallery.scrollTop !== before) hitTest();
  };
  raf = requestAnimationFrame(tick);

  const onMove = (event) => {
    if (event.pointerId !== pointerId) return;
    event.preventDefault();
    pos.x = event.clientX;
    pos.y = event.clientY;
    follow();
    hitTest();
  };
  // Safari 只认非 passive 的 touchmove：不拦住它，列表会跟着手指一起滚
  const block = (event) => event.preventDefault();

  /** 落位动画走完：条目回到空位那个位置，拖动时加的样式全部清掉。 */
  const restore = () => {
    for (const anim of item.getAnimations()) anim.cancel();
    for (const anim of card.getAnimations()) anim.cancel();
    item.classList.remove("lifted");
    for (const prop of ["left", "top", "width", "translate", "scale", "opacity", "transformOrigin"]) {
      item.style[prop] = "";
    }
    card.style.boxShadow = "";
    if (slot.isConnected) grid.insertBefore(item, slot);
    else item.remove();
    slot.remove();
  };

  const done = (event) => {
    if (event && event.pointerId !== undefined && event.pointerId !== pointerId) return;
    removeEventListener("pointermove", onMove);
    removeEventListener("pointerup", done);
    removeEventListener("pointercancel", cancel);
    removeEventListener("touchmove", block);
    cancelAnimationFrame(raf);
    clearPending();

    const landing = target;
    const moved = folder !== null && ctx.folderOf(id) !== folder;
    if (target) {
      target.classList.remove("drop-target");
      pop(target, 1);
    }
    // 空位可能还在让位动画里，先让它走完，才量得到最终位置
    for (const anim of slot.getAnimations()) anim.finish();

    let point = null;
    if (moved && landing) {
      const box = landing.getBoundingClientRect();
      point = { x: box.left + box.width / 2, y: box.top + box.height / 2 };
    } else if (slot.isConnected) {
      const box = slot.querySelector(".board-card").getBoundingClientRect();
      point = { x: box.left + box.width / 2, y: box.top + box.height / 2 };
    }
    settle(item, card, anchor, point, moved, restore);

    if (moved) ctx.onFile(id, folder);
    else if (folder === null && sorted) ctx.onReorder(orderOf(grid, slot, id));
  };
  const cancel = (event) => {
    if (event && event.pointerId !== pointerId) return;
    folder = null;
    sorted = false;
    done(event);
    ctx.onRefresh();
  };
  addEventListener("pointermove", onMove, { passive: false });
  addEventListener("pointerup", done);
  addEventListener("pointercancel", cancel);
  addEventListener("touchmove", block, { passive: false });
  follow();
  hitTest();
}

/** 格子里现在的先后。空位顶替正在拖的那一块，文件夹和「新建」不算。 */
function orderOf(grid, slot, id) {
  return [...grid.children]
    .map((node) => (node === slot ? id : node.dataset.board))
    .filter(Boolean);
}
