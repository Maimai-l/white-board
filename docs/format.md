# 白板文件格式（.wbz）

`.wbz` 不是什么通用格式，就是这个项目自己的存档：**一段 zlib 压缩的 JSON**，
里面的笔画点列再单独做了一层紧凑编码。目的是把手写内容按矢量存下来，
既不存图片，也不至于让 JSON 里堆满浮点数。

存储目录（默认 `~/Library/Application Support/Whiteboard/boards-data`）长这样：

```
boards/<白板 id>.wbz     每块白板一个文件
thumbs/<白板 id>.png     Mac 端选择界面用的缩略图（只是配图，不是内容）
index.json               白板列表，未压缩；删掉能从 .wbz 重建
```

## 解开之后的结构

```jsonc
{
  "v": 1,
  "meta": {
    "id": "49b773c7c7c2",
    "name": "",
    "kind": "board",        // board 四向无限 / note 宽度固定只向下
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
