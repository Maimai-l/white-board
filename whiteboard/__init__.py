"""局域网共享白板：Mac 端服务 + iPad 端 Web Clip 客户端。"""

import sys as _sys
from pathlib import Path as _Path

__version__ = "1.0.1"

# 同步服务端在仓库的 packages/inksync 里（其他项目可以单独安装它）。从源码运行、
# 跑测试和工具时从这里导入；打包后的应用由 PyInstaller 收进去（见 whiteboard.spec）。
_INKSYNC = _Path(__file__).resolve().parents[1] / "packages" / "inksync"
if _INKSYNC.is_dir() and str(_INKSYNC) not in _sys.path:
    _sys.path.insert(0, str(_INKSYNC))
