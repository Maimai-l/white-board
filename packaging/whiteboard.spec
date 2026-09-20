# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 配置：把服务端、前端资源和 pywebview 一起打成 Whiteboard.app。"""

import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(SPECPATH)))
if not os.path.exists(os.path.join(ROOT, "whiteboard")):
    ROOT = os.path.dirname(os.path.abspath(SPECPATH))

VERSION = os.environ.get("WHITEBOARD_VERSION", "0.0.0")
ICON = os.path.join(SPECPATH, "whiteboard.icns")

datas = [(os.path.join(ROOT, "whiteboard", "web"), "whiteboard/web")]
binaries = []
hiddenimports = ["webview.platforms.cocoa"]

# pywebview / zeroconf / aiohttp 都有运行时才用到的子模块和资源
# pypdfium2 带着一个动态库，PIL / pypdf 也有运行时才导入的子模块
for package in ("webview", "zeroconf", "aiohttp", "certifi", "pypdfium2", "pypdfium2_raw", "PIL", "pypdf"):
    try:
        package_datas, package_binaries, package_hidden = collect_all(package)
    except Exception as exc:  # 少了某个可选依赖时照常打包，功能在运行时降级
        print(f"[spec] 跳过 {package}：{exc}")
        continue
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

a = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=["tkinter", "pytest", "playwright"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Whiteboard",
    console=False,
    debug=False,
    strip=False,
    upx=False,
)

collected = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Whiteboard",
)

app = BUNDLE(
    collected,
    name="Whiteboard.app",
    icon=ICON if os.path.exists(ICON) else None,
    bundle_identifier="local.whiteboard",
    version=VERSION,
    info_plist={
        "CFBundleName": "Whiteboard",
        "CFBundleDisplayName": "白板",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        # macOS 15 起，访问局域网要有这段说明，否则 iPad 连不上
        "NSLocalNetworkUsageDescription": "与同一局域网内的 iPad 同步白板内容。",
        "NSBonjourServices": ["_http._tcp"],
    },
)
