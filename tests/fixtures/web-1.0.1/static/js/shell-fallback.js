// 外壳断流时，网页用 Safari 自己的 pen 事件兜底。
//
// 0.9.43 的外壳在网页 preventDefault 之后就收不到这一笔的触摸了（见
// docs/ipad-shell.md 12 节 Q2），每一笔只有开头约 30 ms；外壳要重新安装才能修好。
// 这里的几个方法替它补上缺的落笔、断掉的中段和没有送来的抬笔，精度退回 Safari 的
// 水平，但笔画是完整的。
//
// 这段逻辑是一层一层叠上去的，彼此靠计时配合（下面两个常数），改动其中一处很容易
// 牵连别处，所以原样搬到这个文件里单独放着，没有改写。新版外壳不会走到这里：
// 外壳正常送采样时，shellMissing / shellStalled 都是假，两个看门狗也不会触发。
// 所有 iPad 都升级到新版外壳之后，可以连同 input.js 里的调用点一起删掉。
//
// 这些方法装到 InputController.prototype 上（见 input.js 末尾），里面的 this
// 就是那个 InputController，用的状态都在它的 this.shell 里。

// Safari 的 pen 已经抬起，外壳那一笔过了这么久还没有 up：由网页替它收尾。
// 不收尾的话这一笔一直开着，手掌屏蔽就一直生效，手指的平移缩放全部失灵——
// 0.9.43 的录像 20260927-211825 就是这样。
const SHELL_ORPHAN_MS = 150;
// 外壳这一笔超过这么久没有新采样、Safari 的 pen 事件却还在来：外壳断流了，
// 用 Safari 的事件把这一笔接着画完。外壳每帧至少送一批，正常间隔不到 20 ms。
const SHELL_STALL_MS = 50;

// 由 input.js 传进来（反过来 import 会成环）
let SHELL_BRIDGE;
let penAltitude;

export function installShellFallback(proto, deps) {
  ({ SHELL_BRIDGE, penAltitude } = deps);
  Object.assign(proto, methods);
}

const methods = {
  /**
   * Safari 报了抬笔：等一会儿，外壳要是一直没再送采样，就替它送一个 up。
   *
   * 替它送的这一批照样经过 receiveShell，所以录像里有它，回放时结果相同。
   * 这一笔之后如果外壳又送来采样，按 ignored 丢掉。
   */
  watchShellStroke() {
    const shell = this.shell;
    if (this.replaying || this.openShellId() === null) return;
    const liftedAt = performance.now();
    clearTimeout(shell.watchdog);
    shell.watchdog = setTimeout(() => {
      shell.watchdog = 0;
      if (shell.lastAt > liftedAt) return; // 外壳还在送，它自己会收尾
      const id = this.openShellId();
      const last = shell.lastSample;
      if (id === null || !last || last.id !== id) return;
      this.stats.shellOrphan += 1;
      this.receiveShell({
        bridge: SHELL_BRIDGE[1],
        samples: [{ ...last, ph: "up", est: [], ui: null }],
        pred: null,
        updates: [],
        orphan: true,
      });
    }, SHELL_ORPHAN_MS);
  },

  /**
   * Safari 已经落笔一阵了，外壳却一个采样都没送：这一笔由网页用 Safari 的事件起笔。
   */
  shellMissing() {
    const down = this.shell.safariDown;
    if (this.replaying || !down || this.openShellId() !== null) return false;
    if (this.shell.realAt >= down.at) return false; // 外壳在 Safari 落笔之后送过采样
    return performance.now() - down.at > SHELL_STALL_MS;
  },

  /**
   * 点一下就抬起、外壳这期间一个采样都没送：再等一会儿，还是没有的话，
   * 用 Safari 的落笔和抬笔补出这一点。
   */
  watchShellTap(up) {
    const shell = this.shell;
    const down = shell.safariDown;
    if (this.replaying || !down || shell.realAt >= down.at) return;
    clearTimeout(shell.watchdog);
    shell.watchdog = setTimeout(() => {
      shell.watchdog = 0;
      if (shell.realAt >= down.at || this.openShellId() !== null) return;
      this.shellFallback(down.event, "down");
      this.shellFallback(up, "up");
    }, SHELL_ORPHAN_MS);
  },

  /** 外壳这一笔开着，却已经有一阵没送真实采样了。 */
  shellStalled() {
    if (this.replaying || this.openShellId() === null) return false;
    return performance.now() - this.shell.realAt > SHELL_STALL_MS;
  },

  /**
   * 外壳断流时，用 Safari 自己的 pen 事件把这一笔接着画完。
   *
   * 0.9.43 的外壳在网页 preventDefault 之后就收不到这一笔的触摸了（见
   * docs/ipad-shell.md 12 节 Q2），每一笔只有开头约 30 ms。外壳要重新安装才能
   * 修好，网页这边先兜住：精度退回 Safari 的水平，但笔画是完整的。
   *
   * 补的采样也走 receiveShell，录像里有它，回放时不再重新判断。
   */
  shellFallback(event, phase) {
    const shell = this.shell;
    if (phase === "down") shell.fallbackSeq += 1;
    const id = phase === "down" ? -shell.fallbackSeq : this.openShellId();
    if (id === null) return;
    const last = shell.lastSample;
    const t = last ? last.t + (performance.now() - shell.lastAt) / 1000 : 0;
    this.stats.shellFallback += 1;
    if (phase === "up") this.stats.shellOrphan += 1;
    this.receiveShell({
      bridge: SHELL_BRIDGE[1],
      samples: [{
        id,
        ph: phase,
        k: "safari",
        t,
        x: event.clientX,
        y: event.clientY,
        f: event.pressure || 0,
        fmax: 1,
        alt: penAltitude(event),
        az: typeof event.azimuthAngle === "number" ? event.azimuthAngle : 0,
        est: [],
        ui: null,
      }],
      pred: null,
      updates: [],
      fallback: true,
      orphan: phase === "up",
    });
  },
};
