"""局域网共享白板：Mac 端服务 + iPad 端 Web Clip 客户端。"""

import sys as _sys
from pathlib import Path as _Path

__version__ = "1.3.0"
# 打包时由 packaging/set_version.py 写入：构建时间（CFBundleVersion，Sparkle 只比它）和更新通道。
# 从源码运行时两者为空 / "source"。见 docs/design/ci-and-updates.zh-CN.md 第 3.2 节。
__build__ = ""
__channel__ = "source"

# 发布页：「关于」里的更新日志、iPad 外壳安装页的下载链接都指向这里。
# 发布资产的名字由 CI 的 release job 决定，1.2.0 的旧更新器还靠它们升级到 1.3.0。
REPO = "Maimai-l/white-board"
RELEASES_URL = f"https://github.com/{REPO}/releases"

# 同步服务端在仓库的 packages/inksync 里（其他项目可以单独安装它）。从源码运行、
# 跑测试和工具时从这里导入；打包后的应用由 PyInstaller 收进去（见 whiteboard.spec）。
_INKSYNC = _Path(__file__).resolve().parents[1] / "packages" / "inksync"
if _INKSYNC.is_dir() and str(_INKSYNC) not in _sys.path:
    _sys.path.insert(0, str(_INKSYNC))
