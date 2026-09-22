# 白板文件格式（.wbz）

`.wbz` 不是什么通用格式，就是这个项目自己的存档：**一段 zlib 压缩的 JSON**，
里面的笔画点列再单独做了一层紧凑编码。目的是把手写内容按矢量存下来，
既不存图片，也不至于让 JSON 里堆满浮点数。

存储目录（默认 `~/Library/Application Support/Whiteboard/boards-data`）长这样：

```
boards/<白板 id>.wbz     每块白板一个文件
thumbs/<白板 id>.png     Mac 端选择界面用的缩略图（只是配图，不是内容）
docs/<白板 id>.<扩展名>  文档板的原件副本（PDF / 图片），只读不改
index.json               白板列表，未压缩；删掉能从 .wbz 重建（所以改名要两边都写）
```

## 解开之后的结构

```jsonc
{
  "v": 1,
  "meta": {
    "id": "49b773c7c7c2",
    "name": "",             // 空串表示没起名，界面上按 kind 给默认叫法
    "kind": "board",        // board 四向无限 / note 宽度固定只向下 / doc 文档板
    "background": "grid",   // blank / grid / lines / dots
    "created": 1758000000.0,
    "updated": 1758000123.4
  },
  "strokes": [
    {
      "id": "c3f1a2-7k-1f",  // 客户端生成，全局唯一
      "tool": "pen",         // pen / marker / highlighter
      "color": "#1b1b1f",
      "w": 3.0,              // 线宽（世界坐标）
      "n": 42,               // 层叠序号，按它升序绘制
      "dev": "ipad",
      "p": "A6ABwAKABAiZ1QG2ev8="   // base64，见下
    }
  ]
}
```

## 点列的编码（`p` 字段）

内存和网络上点列是扁平数组 `[x, y, 压感, x, y, 压感, ...]`，落盘时换成字节流：

1. 坐标量化到 1/8 像素（`QUANT = 8`），取整；
2. 相邻点存**增量**，不存绝对值；
3. 增量用 zigzag varint（小的负数也只占一个字节）；
4. 压感量化成 1 字节；
5. 整串 base64 塞进 JSON，最后整个 JSON 再 zlib 压缩。

一条 500 个点的笔画通常不到 2 KB。代码在 `whiteboard/codec.py`，
`encode_points` / `decode_points` 是一对逆运算，有往返测试。

## 自己读一个文件

```python
import json, zlib
from whiteboard.codec import decode_points_b64

payload = json.loads(zlib.decompress(open("boards/xxx.wbz", "rb").read()))
for stroke in payload["strokes"]:
    points = decode_points_b64(stroke["p"])   # [x, y, 压感, ...]
    print(stroke["tool"], stroke["color"], len(points) // 3, "个点")
```

导出 PNG 走的是另一条路（界面上的导出按钮），`.wbz` 里永远只有矢量数据。

## 文档板（beta）

`kind` 是 `doc` 的白板多一段 `meta.doc`，记着原件是什么、每页多大：

```jsonc
"doc": {
  "type": "pdf",            // pdf / image
  "name": "讲义.pdf",        // 原始文件名，只用来起导出文件名
  "ext": ".pdf",
  "pages": [[595.28, 841.89], [595.28, 841.89]]   // 每页的显示尺寸
}
```

页面在世界坐标里自上而下排列，页宽按最宽的一页居中，页间距 24 单位
（`whiteboard/docs.py` 的 `PAGE_GAP`，前端 `boardstate.js` 里有一份同样的值，
两边对不上导出时笔迹就会落到别的页上）。PDF 的尺寸用 pt，图片用像素；
两者都已经算进 `/Rotate`，所以世界坐标和屏幕上看到的排版是一一对应的。

原件本身不进 `.wbz`，而是原样复制到 `docs/` 下，导出时才和笔迹合到一起：

- **PDF**：笔迹编成一段新的内容流，以 `/Contents` 数组的形式追加在原页面后面，
  原有的内容流对象一个字节都不重写。笔迹先做 RDP 抽稀（阈值取笔宽的 5%，夹在
  0.35～1.5 pt 之间：细笔差半点就看得出来，马克笔差一点半也看不出来），坐标按
  1/4 pt 取整后靠 `cm` 缩放回去（整数比小数好压）。每一笔都是**一次填充闭合轮廓**，
  和屏幕上的画法逐段对应：两侧是插值曲线（Catmull-Rom 转三次贝塞尔），笔尖是两段
  四分之一圆弧；半透明由 `/ExtGState` 的 `ca` 负责，一次填充顺带保证重叠处不变深。
  最后整段 zlib 压缩，实测每一笔 300～450 字节。代码在 `whiteboard/inkpdf.py`。

  每次导出都是**从原件重新写一遍**（pypdf 的 `PdfWriter(clone_from=...)` 只带走
  还能被引用到的对象），所以：原件里堆了多少次「增量保存」的死版本都会被甩掉；
  同一块白板导出多少次都一样大——原件只读、笔迹另存在 `.wbz` 里，不存在
  「保存一次多一份副本」这回事。

  早先为了省体积做过「按线宽分桶描成折线」：线宽一变就断一段，每段两头各带一个
  圆头，笔一粗就变成一串大小不一的圆饼，和屏幕上完全不是一个形状，已经废弃。
- **图片**：笔迹按原分辨率栅格化叠上去，每一笔只在自己的包围盒里做 3 倍超采样；
  JPEG 复用原来的量化表和色度采样重编码，不画笔迹时体积与原件一致。

`/Rotate` 的处理见 `inkpdf.page_matrix`：它给出「显示坐标（左上原点、y 向下）
→ PDF 用户坐标」的 `cm` 矩阵，四个角度各一套，顺带把 CropBox 的偏移算进去。
