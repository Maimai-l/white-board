"""应用配置：监听端口与白板存储目录。

配置文件固定放在系统应用数据目录，而白板内容目录（``data_dir``）可以
在 Mac 端 GUI 里改到任意文件夹。
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

DEFAULT_PORT = 8848
APP_NAME = "Whiteboard"

# 局域网上别的设备可以被授予的权限。名字同时是配置的键和界面上的分组。
# 应用名，与 models.APP_RE 相同
IPAD_HOME_RE = re.compile(r"^[a-z0-9-]{1,32}$")

REMOTE_PERMISSIONS = ("manage", "settings", "clear", "export")


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
            # 后台自动下载更新：默认关闭。开着的话启动时查到新版本会先把包下好，
            # 但手动点「检查更新」永远只拿版本信息，不会偷偷占带宽。
            "auto_update": False,
            # 「跳过这个版本」记在这里
            "skip_version": "",
            # 局域网上的别的设备各能做什么，一项一开关，默认全关：那些设备只能写字。
            # 检查更新、选存储目录不在这里：它们走 pywebview 的本地接口，
            # 别的设备本来就够不着，给个开关反而是骗人。
            "remote_permissions": {},
            # 上一次运行的版本号。换了版本（升级或降级）时先备份白板，见 backup.py
            "last_version": "",
            # iPad 打开时进入的应用（存储目录 apps/ 下的文件夹名）；空串是白板本身
            "ipad_home": "",
        }
        # 只在这一次运行里有效、不写进配置文件的值：命令行给的端口和存储目录，
        # 以及端口被占用时实际顺延到的那个端口。以前这些都直接写进配置，
        # 顺延一次（8848 → 8849）之后就永远是 8849，iPad 上记的地址就失效了。
        self._runtime: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("配置读取失败，使用默认值：%s", exc)
            self._keep_aside()
            return
        if not isinstance(raw, dict):
            log.warning("配置文件不是预期的结构，使用默认值")
            self._keep_aside()
            return
        self.values.update({k: v for k, v in raw.items() if k in self.values})
        # 旧版本只有一个总开关，开着就等于三项全开。
        if raw.get("allow_remote_control") and not self.values["remote_permissions"]:
            self.values["remote_permissions"] = {name: True for name in REMOTE_PERMISSIONS}

    def _keep_aside(self) -> None:
        """读不出来的配置文件先挪到一边再用默认值：里面记着白板存在哪个目录，
        直接被默认值覆盖掉，用户会以为白板全没了。"""
        target = self.path.with_name(f"{self.path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
        try:
            os.replace(self.path, target)
            log.warning("原配置文件已挪到 %s", target)
        except OSError as exc:
            log.warning("原配置文件挪不走：%s", exc)

    def save(self) -> None:
        """先写临时文件再替换，写到一半断电也不会留下半截配置。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = json.dumps(self.values, ensure_ascii=False, indent=2).encode("utf-8")
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".tmp-")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, self.path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        except OSError as exc:
            log.warning("配置写入失败：%s", exc)

    def set_runtime(self, port: Optional[int] = None, data_dir: Optional[os.PathLike | str] = None) -> None:
        """这一次运行用的端口 / 存储目录（命令行参数），不写进配置文件。"""
        if port is not None:
            self._runtime["port"] = int(port)
        if data_dir is not None:
            self._runtime["data_dir"] = str(Path(data_dir).expanduser())

    @property
    def requested_port(self) -> int:
        """想要监听的端口：命令行给的，否则是配置里存的。被占用时从这里往后顺延。"""
        try:
            return int(self._runtime.get("port", self.values.get("port", DEFAULT_PORT)))
        except (TypeError, ValueError):
            return DEFAULT_PORT

    @property
    def port(self) -> int:
        """实际在用的端口：顺延过就是顺延之后的那个，页面上显示的地址用它。"""
        return int(self._runtime.get("bound_port", self.requested_port))

    @port.setter
    def port(self, value: int) -> None:
        self.values["port"] = int(value)

    def set_bound_port(self, value: int) -> None:
        """服务端实际绑定到的端口。只在这一次运行里有效。"""
        self._runtime["bound_port"] = int(value)

    @property
    def last_version(self) -> str:
        value = self.values.get("last_version", "")
        return value if isinstance(value, str) else ""

    @last_version.setter
    def last_version(self, value: str) -> None:
        self.values["last_version"] = str(value or "")

    @property
    def auto_update(self) -> bool:
        return bool(self.values.get("auto_update", False))

    @auto_update.setter
    def auto_update(self, value: bool) -> None:
        self.values["auto_update"] = bool(value)

    @property
    def remote_permissions(self) -> Dict[str, bool]:
        """别的设备被允许做的事，键见 ``REMOTE_PERMISSIONS``。"""
        raw = self.values.get("remote_permissions")
        raw = raw if isinstance(raw, dict) else {}
        return {name: bool(raw.get(name)) for name in REMOTE_PERMISSIONS}

    def set_remote_permission(self, name: str, enabled: bool) -> Dict[str, bool]:
        if name not in REMOTE_PERMISSIONS:
            raise ValueError(f"没有这个权限：{name}")
        current = self.remote_permissions
        current[name] = bool(enabled)
        self.values["remote_permissions"] = current
        return current

    @property
    def ipad_home(self) -> str:
        value = self.values.get("ipad_home", "")
        return value if isinstance(value, str) and IPAD_HOME_RE.match(value) else ""

    @ipad_home.setter
    def ipad_home(self, value: str) -> None:
        value = str(value or "")
        if value and not IPAD_HOME_RE.match(value):
            raise ValueError(f"应用名不合法：{value}")
        self.values["ipad_home"] = value

    @property
    def skip_version(self) -> str:
        value = self.values.get("skip_version", "")
        return value if isinstance(value, str) else ""

    @skip_version.setter
    def skip_version(self, value: str) -> None:
        self.values["skip_version"] = str(value or "")

    @property
    def data_dir(self) -> Path:
        raw = self._runtime.get("data_dir", self.values.get("data_dir", default_data_dir()))
        return Path(str(raw)).expanduser()

    @data_dir.setter
    def data_dir(self, value: os.PathLike | str) -> None:
        """在界面上选的存储目录：用户的决定，要记住，并且盖过命令行那一次。"""
        self.values["data_dir"] = str(Path(value).expanduser())
        self._runtime.pop("data_dir", None)
