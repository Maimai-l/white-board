"""消息条：同时有几条时往下排开，不互相盖住；导入一个文件只有一条消息，进行中的那条直接变成结果。

用户反馈：从访达拖一个 PDF 进来，弹出两个打不开的提示，后一个把前一个盖住了。
"""

from __future__ import annotations

import base64

from e2e.support import open_mac, open_touch

DROP_FILE = """([name, type, base64]) => {
  const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
  const data = new DataTransfer();
  data.items.add(new File([bytes], name, { type }));
  for (const kind of ["dragover", "drop"]) {
    window.dispatchEvent(new DragEvent(kind, { dataTransfer: data, bubbles: true, cancelable: true }));
  }
}"""

BOXES = """() => [...document.querySelectorAll('.notice:not(.leaving)')].map((n) => {
  const r = n.getBoundingClientRect();
  return { text: n.innerText, top: r.top, bottom: r.bottom, left: r.left, right: r.right };
})"""


def overlapping(boxes):
    return [
        (a["text"], b["text"])
        for i, a in enumerate(boxes)
        for b in boxes[i + 1 :]
        if a["top"] < b["bottom"] and b["top"] < a["bottom"] and a["left"] < b["right"] and b["left"] < a["right"]
    ]


def test_messages_stack_instead_of_covering_each_other(browser, server):
    for page in (open_mac(browser, server.port), open_touch(browser, server.port)):
        page.evaluate("() => { whiteboard.ui.message('第一条'); whiteboard.ui.message(['第二条', '有两行']); }")
        page.wait_for_function("() => document.querySelectorAll('.notice:not(.leaving)').length === 2")
        page.wait_for_function("() => document.getAnimations().every((a) => a.playState !== 'running')")
        boxes = page.evaluate(BOXES)
        assert not overlapping(boxes), boxes
        assert boxes[0]["bottom"] <= boxes[1]["top"], boxes  # 新的排在下面


def test_a_file_that_cannot_be_opened_gives_one_message(browser, server):
    mac = open_mac(browser, server.port)
    bad = base64.b64encode(b"this is not a pdf").decode()
    mac.evaluate(DROP_FILE, ["坏掉的.pdf", "application/pdf", bad])
    mac.wait_for_selector('.notice:not(.leaving):has-text("打不开 坏掉的.pdf")')
    mac.wait_for_function("() => document.querySelectorAll('.notice:not(.leaving)').length === 1")
    assert mac.locator(".notice:not(.leaving)").inner_text() == "打不开 坏掉的.pdf"
