"""把 iPad 原生的橡皮印记和我们的栅格化到同一张图上逐像素比。

用法：先把 InkProbe 导出的会话 zip 解开，然后

    python3 tools/compare_native_eraser.py <解开后的 sessions 目录>

会打印 IoU、多擦/漏擦的比例，并为每个会话生成一张差异图：
灰 = 两边都擦了，红 = 只有原生擦了（我们漏），蓝 = 只有我们擦了（我们多）。

度量上有三处必须注意，都是踩过的坑：

1. 橡皮半径是屏幕尺度的，换算到 drawing 坐标要除以当时的缩放。
2. 原生的 mask 只是个裁剪区域，它在墨迹之外长什么样对 PencilKit 没有影响，
   所以洞可以伸到墨迹外面。直接拿洞比会把墨迹外那部分也算成「原生擦掉了」，
   实测能占到四成。两边都要先和真实墨迹求交。
3. 墨迹区域只能从 interpolatedPoints 加逐点宽度重建，不能用 mask 的外轮廓
   代替。重建出来比导出的透明底图瘦约 13%，这个偏差两边同样承受。

原生的洞 = PencilKit 像素橡皮实际擦掉的形状。
我们的印记 = 按 input.json 的原始触摸重放一遍，用 input.js 的 ERASER_CURVE 算半径、
沿采样点扫出胶囊链。两边都画到 renderRect 上，scale 相同。
"""
import json, math, os, re, sys
from PIL import Image, ImageChops, ImageDraw

ROOT = sys.argv[1] if len(sys.argv) > 1 else "sessions"
JS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                  "..", "whiteboard", "web", "static", "js", "input.js")


def our_curve():
    """直接从 input.js 里读 ERASER_CURVE，免得分析脚本和线上实现悄悄跑偏。"""
    src = open(JS).read()
    body = re.search(r"const ERASER_CURVE = \[(.*?)\n\];", src, re.S).group(1)
    pts = [(float(a), float(b)) for a, b in re.findall(r"\[\s*([\d.]+)\s*,\s*([\d.]+)\s*\]", body)]
    return pts


CURVE = our_curve()
SMOOTH = float(re.search(r"const ERASER_SMOOTH = ([\d.]+)", open(JS).read()).group(1))


def our_diameter(deg):
    if deg >= CURVE[0][0]:
        return CURVE[0][1]
    for i in range(len(CURVE) - 1):
        (ah, dh), (al, dl) = CURVE[i], CURVE[i + 1]
        if deg >= al:
            t = (ah - deg) / (ah - al)
            return dh + (dl - dh) * t
    return CURVE[-1][1]


def subpaths(mask):
    out, cur = [], None
    for tok in re.finditer(r"([MLZ])((?:\s+-?[\d.eE+]+)*)", mask):
        cmd = tok.group(1)
        args = [float(v) for v in tok.group(2).split()]
        if cmd == "M":
            if cur:
                out.append(cur)
            cur = [(args[0], args[1])]
        elif cmd == "L":
            cur.append((args[0], args[1]))
        elif cmd == "Z":
            if cur:
                out.append(cur)
            cur = None
    if cur:
        out.append(cur)
    return out


def signed_area(poly):
    a = 0.0
    for i in range(len(poly)):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % len(poly)]
        a += x0 * y1 - x1 * y0
    return a / 2


def render_native_erased(inner, region, scale):
    """原生实际擦掉的区域 = 完整路径占的地方 − 导出底图里还看得见的墨。

    不能只数 mask 里的洞：橡皮擦穿一条笔画时 PencilKit 会把它拆成几条独立笔画，
    被擦掉的那一段不再是某条笔画里的洞，而是落在每一片的外轮廓之外，数洞会漏掉
    绝大部分。实测一个会话里 4 条笔画被拆成 15 条，数洞只统计到实际擦除量的四分之一。

    用导出的 render-transparent@2x.png 当真值最稳：它就是原生渲染出来的结果。
    """
    png = Image.open(os.path.join(inner, "final", "render-transparent@2x.png"))
    if png.size != region.size:
        png = png.resize(region.size)
    alpha = png.split()[-1]
    visible = alpha.point(lambda a: 255 if a > 32 else 0).convert("1")
    gone = ImageChops.subtract(region.convert("L"), visible.convert("L")).convert("1")
    return gone


def render_our_marks(sequences, rect, scale):
    x0, y0, w, h = rect
    img = Image.new("1", (int(w * scale), int(h * scale)), 0)
    d = ImageDraw.Draw(img)
    for seq in sequences:
        if seq["tool"]["category"] != "eraser":
            continue
        pts = [s for s in seq["samples"] if s["kind"] == "coalesced"]
        if not pts:
            continue
        # 橡皮半径是屏幕尺度的，换算到 drawing 坐标要除以当时的缩放
        zoom = seq["viewportAtBegin"]["zoom"] or 1.0
        radius = our_diameter(math.degrees(pts[0]["altitude"])) / 2 / zoom
        prev = None
        for s in pts:
            target = our_diameter(math.degrees(s["altitude"])) / 2 / zoom
            radius += (target - radius) * SMOOTH
            px, py = (s["x"] - x0) * scale, (s["y"] - y0) * scale
            r = radius * scale
            d.ellipse([px - r, py - r, px + r, py + r], fill=1)
            if prev:
                d.line([prev, (px, py)], fill=1, width=int(round(2 * r)))
            prev = (px, py)
    return img


def render_ink_region(strokes, rect, scale):
    """原生墨迹本身占的区域，从 interpolatedPoints 加逐点宽度画出来。

    不能用 mask 的外轮廓代替：mask 只是个裁剪区域，它在墨迹之外长什么样对
    PencilKit 没有影响，所以洞可以伸到墨迹外面去。拿原始的洞去比，等于把墨迹外
    那部分也算成「原生擦掉了」——实测里这一项能占到四成。
    """
    x0, y0, w, h = rect
    img = Image.new("1", (int(w * scale), int(h * scale)), 0)
    d = ImageDraw.Draw(img)
    for s in strokes:
        # 只能用 points（控制点），不能用 interpolatedPoints：后者带 rangeIndex，
        # 只覆盖遮罩之后还活着的那几段，正好把擦掉的地方挖空了，拿它当「擦之前的
        # 墨迹」会让橡皮路径整条落在区域之外。控制点走的是完整路径，路径本身不受
        # 遮罩影响（WWDC20 session 10148：strokes are masked, but stroke paths are not）。
        pts = s.get("points") or []
        prev = None
        for q in pts:
            r = max(q.get("width", 1.0), q.get("height", 1.0)) / 2 * scale
            px, py = (q["x"] - x0) * scale, (q["y"] - y0) * scale
            d.ellipse([px - r, py - r, px + r, py + r], fill=1)
            # 控制点之间可能隔着几个单位，补上中间的带子
            if prev is not None:
                d.line([prev[0], prev[1], px, py], fill=1, width=max(1, int(round(2 * r))))
            prev = (px, py, r)
    return img


def compare(name):
    # zip 解出来有时会多一层同名目录，两种布局都认
    inner = os.path.join(ROOT, name, name)
    if not os.path.isfile(os.path.join(inner, "meta.json")):
        inner = os.path.join(ROOT, name)
    st = json.load(open(os.path.join(inner, "final", "strokes.json")))
    inp = json.load(open(os.path.join(inner, "input.json")))
    rect = st["renderRect"]
    scale = 2.0
    # 比较只在「墨迹本来占的地方」里做：橡皮划过空白不算擦除
    region = render_ink_region(st["strokes"], rect, scale)
    native = render_native_erased(inner, region, scale)
    ours = render_our_marks(inp["sequences"], rect, scale)
    ours = Image.composite(ours, Image.new("1", ours.size, 0), region)

    # mode "1" 的 getdata 返回 0/255，两边统一成 0/1 再数
    n = [1 if v else 0 for v in native.getdata()]
    o = [1 if v else 0 for v in ours.getdata()]
    inter = sum(1 for a, b in zip(n, o) if a and b)
    na = sum(n)
    oa = sum(o)
    union = na + oa - inter
    print(f"\n=== {name} ===")
    print(f"  原生擦掉 {na:8d} px   我们擦掉 {oa:8d} px   面积比 {oa/na:.3f}" if na else "  原生没擦")
    if union:
        print(f"  交集 {inter:8d}   并集 {union:8d}   IoU {inter/union:.3f}")
        print(f"  我们多擦 {oa-inter:7d} px ({100*(oa-inter)/union:.1f}%)   "
              f"我们漏擦 {na-inter:7d} px ({100*(na-inter)/union:.1f}%)")
    out = Image.new("RGB", native.size, (255, 255, 255))
    px = out.load()
    W = native.size[0]
    for i, (a, b) in enumerate(zip(n, o)):
        if a and b:   px[i % W, i // W] = (210, 210, 210)   # 都擦了
        elif a:       px[i % W, i // W] = (220, 60, 50)     # 只有原生擦了：我们漏
        elif b:       px[i % W, i // W] = (40, 110, 220)    # 只有我们擦了：我们多
    out.save(os.path.join(ROOT, name + "-diff.png"))


def main():
    for name in sorted(os.listdir(ROOT)):
        base = os.path.join(ROOT, name)
        if os.path.isfile(os.path.join(base, "meta.json")) or os.path.isfile(
            os.path.join(base, name, "meta.json")
        ):
            compare(name)


if __name__ == "__main__":
    main()
