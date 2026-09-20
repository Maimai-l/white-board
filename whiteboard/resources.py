"""打包成 .app 之后的路径与日志。

PyInstaller 会把整个包解到一个临时目录里，``__file__`` 不再指向源码树，
所以网页资源要走 ``sys._MEIPASS``；同时 .app 里没有终端，日志得落到文件。
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def resource_root() -> Path:
    """源码树的包根目录，或冻结后的解包目录。"""
    if is_frozen():
        return Path(sys._MEIPASS) / "whiteboard"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


def web_dir() -> Path:
    return resource_root() / "web"


def app_bundle() -> Path | None:
    """自身所在的 .app 路径（仅在打包运行时有值）。"""
    if not is_frozen():
        return None
    # .../Whiteboard.app/Contents/MacOS/Whiteboard
    executable = Path(sys.executable).resolve()
    for parent in executable.parents:
        if parent.suffix == ".app":
            return parent
    return None


def log_path() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    base.mkdir(parents=True, exist_ok=True)
    return base / "Whiteboard.log"


def setup_logging(debug: bool = False, to_file: bool | None = None) -> Path | None:
    """控制台有就打控制台，打包运行时同时写一份日志文件。"""
    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S"
    )

    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)

    if to_file is None:
        to_file = is_frozen()
    if not to_file:
        return None
    path = log_path()
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=512 * 1024, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(formatter)
    root.addHandler(handler)
    return path
