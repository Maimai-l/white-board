"""应用配置：监听端口与白板存储目录。

配置文件固定放在系统应用数据目录，而白板内容目录（``data_dir``）可以
在 Mac 端 GUI 里改到任意文件夹。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict

log = logging.getLogger(__name__)

DEFAULT_PORT = 8848
APP_NAME = "Whiteboard"


def app_support_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME.lower()


def default_data_dir() -> Path:
    return app_support_dir() / "boards-data"


class Config:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else app_support_dir() / "config.json"
        self.values: Dict[str, Any] = {
            "port": DEFAULT_PORT,
            "data_dir": str(default_data_dir()),
            # 后台自动下载更新（下好之后仍然会问你装不装）
            "auto_update": True,
            # 「跳过这个版本」记在这里
            "skip_version": "",
        }
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("配置读取失败，使用默认值：%s", exc)
            return
        if isinstance(raw, dict):
            self.values.update({k: v for k, v in raw.items() if k in self.values})

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.values, ensure_ascii=False, indent=2), "utf-8")
        except OSError as exc:
            log.warning("配置写入失败：%s", exc)

    @property
    def port(self) -> int:
        try:
            return int(self.values.get("port", DEFAULT_PORT))
        except (TypeError, ValueError):
            return DEFAULT_PORT

    @port.setter
    def port(self, value: int) -> None:
        self.values["port"] = int(value)

    @property
    def auto_update(self) -> bool:
        return bool(self.values.get("auto_update", True))

    @auto_update.setter
    def auto_update(self, value: bool) -> None:
        self.values["auto_update"] = bool(value)

    @property
    def skip_version(self) -> str:
        value = self.values.get("skip_version", "")
        return value if isinstance(value, str) else ""

    @skip_version.setter
    def skip_version(self, value: str) -> None:
        self.values["skip_version"] = str(value or "")

    @property
    def data_dir(self) -> Path:
        return Path(str(self.values.get("data_dir", default_data_dir()))).expanduser()

    @data_dir.setter
    def data_dir(self, value: os.PathLike | str) -> None:
        self.values["data_dir"] = str(Path(value).expanduser())
