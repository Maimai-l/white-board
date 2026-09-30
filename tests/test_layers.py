"""手写板（inksync 的前端，packages/inksync/inksync/web）不能依赖白板应用，
其他项目才能单独使用它。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "packages" / "inksync" / "inksync" / "web"
IMPORT_RE = re.compile(r'^\s*(?:import|export)\s[^;]*?from\s+"([^"]+)"', re.M)


def test_the_sdk_only_imports_its_own_modules():
    for path in SDK.rglob("*.js"):
        for target in IMPORT_RE.findall(path.read_text("utf-8")):
            assert target.startswith("./"), (path.name, target)
            assert (path.parent / target).resolve().is_file() or target == "./version.js", (path.name, target)


def test_the_sdk_does_not_touch_the_app():
    for path in SDK.rglob("*.js"):
        if path.parent.name == "vendor":
            continue
        source = path.read_text("utf-8")
        assert "this.ui." not in source, path.name
        assert "pywebview" not in source, path.name
        # 白板应用专有的接口（缩略图、文档页、诊断）不写死在手写板里
        assert not re.search(r'["`]/api/', source), path.name
        assert "dataset.shell" not in source, path.name


def test_the_app_loads_the_sdk_from_inksync():
    app_js = ROOT / "whiteboard" / "web" / "static" / "js"
    for path in app_js.glob("*.js"):
        for target in IMPORT_RE.findall(path.read_text("utf-8")):
            assert target.startswith("./") or target.startswith("/inksync/"), (path.name, target)
