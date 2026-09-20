"""打包后的冒烟测试：建一块文档板，确认 PDF 真的能渲染、能导出。

pdfium 是个动态库，PyInstaller 漏掉它的话本地跑没事、打出来的 .app 一碰
文档板就炸，所以这一步必须在打包产物上跑，而不是在源码上跑。
"""

from __future__ import annotations

import io
import json
import sys
import urllib.request


def make_pdf() -> bytes:
    import pypdf

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def main(base: str) -> int:
    request = urllib.request.Request(
        f"{base}/api/doc?name=smoke.pdf", data=make_pdf(), method="POST"
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        board = json.load(response)["board"]
    if board["kind"] != "doc" or not board["doc"]["pages"]:
        print("文档板建出来了但元数据不对：", board)
        return 1

    with urllib.request.urlopen(f"{base}/api/doc/{board['id']}/0?w=320", timeout=60) as response:
        page = response.read()
    if len(page) < 600:
        print("页面渲染结果太小，pdfium 多半没打进去")
        return 1

    with urllib.request.urlopen(f"{base}/api/export/{board['id']}", timeout=60) as response:
        exported = response.read()
    if not exported.startswith(b"%PDF"):
        print("导出的不是 PDF")
        return 1

    print(f"文档板可用：{len(board['doc']['pages'])} 页，位图 {len(page)} 字节，"
          f"导出 {len(exported)} 字节")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8877"))
