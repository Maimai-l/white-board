"""手写板（inkpad.js）不能依赖白板应用的界面，其他应用才能单独嵌入它。"""

from __future__ import annotations

import re
from pathlib import Path

JS = Path(__file__).resolve().parents[1] / "whiteboard" / "web" / "static" / "js"
IMPORT_RE = re.compile(r'^\s*(?:import|export)\s[^;]*?from\s+"\./([\w-]+\.js)"', re.M)

# 白板应用专有的模块：界面、白板选择界面、设置、更新、录制面板
APP_ONLY = {"app.js", "ui.js", "ui-common.js", "ui-boards.js", "ui-dialogs.js", "ui-settings.js",
            "pkpicker.js", "toolpicker.js", "dragsort.js", "icons.js", "notes.js", "recorder.js"}


def reachable(entry: str) -> set:
    seen, todo = set(), [entry]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        todo.extend(IMPORT_RE.findall((JS / name).read_text("utf-8")))
    return seen


def test_the_handwriting_pad_does_not_import_the_app_interface():
    modules = reachable("inkpad.js")
    assert not modules & APP_ONLY, sorted(modules & APP_ONLY)


def test_the_handwriting_pad_does_not_touch_the_app_interface_directly():
    for name in ("inkpad.js", "app-eraser.js"):
        source = (JS / name).read_text("utf-8")
        assert "this.ui." not in source, name
        assert "pywebview" not in source, name
