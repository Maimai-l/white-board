# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 配置：把服务端、前端资源和 pywebview 一起打成 Whiteboard.app。"""

import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(SPECPATH)))
if not os.path.exists(os.path.join(ROOT, "whiteboard")):
    ROOT = os.path.dirname(os.path.abspath(SPECPATH))

VERSION = os.environ.get("WHITEBOARD_VERSION", "0.0.0")
ICON = os.path.join(SPECPATH, "whiteboard.icns")

def _is_lab(dest):
    """打包目标路径是不是 whiteboard/web/static/lab/ 底下的文件。"""
    return dest.replace(os.sep, "/").startswith("whiteboard/web/static/lab/")


datas = [(os.path.join(ROOT, "whiteboard", "web"), "whiteboard/web")]
# 同版本的 iPad 外壳：CI 的 ipad 任务先构建好放在这里，安装页和 /ipad/Whiteboard.ipa
# 从资源目录里取（见 whiteboard/ipadshell.py）。本地打包没有它时照常打，安装页会
# 提示去 Release 下载。
IPA = os.path.join(ROOT, "packaging", "ipad", "Whiteboard.ipa")
if os.path.exists(IPA):
    datas.append((IPA, "whiteboard/ipad"))
else:
    print("[spec] 没有 packaging/ipad/Whiteboard.ipa，这个包不带 iPad 外壳")
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
# static/lab/ 是在真机上调手感用的开发页面（见 README「拖动」一节），不进正式包；
# 从源码运行时照常能打开。
a.datas = [entry for entry in a.datas if not _is_lab(entry[0])]
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
        # _whiteboard._tcp 给 iPad 外壳自动发现这台 Mac 用（docs/ipad-shell.md 8.4 节）
        "NSBonjourServices": ["_http._tcp", "_whiteboard._tcp"],
    },
)
