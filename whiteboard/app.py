"""Mac 端的 pywebview 窗口。

窗口里装的就是 iPad 打开的同一个网页，只是以 ``role=mac`` 载入，
多出白板列表、尺寸 / 背景设置、缩放、导出与描述文件分发。
"""

from __future__ import annotations

import base64
import logging
import subprocess
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional

from . import __version__, netinfo, profile, resources, updater
from .config import Config
from .runner import ServerThread

log = logging.getLogger(__name__)


class NativeApi:
    """暴露给网页的本地能力（保存文件、选目录、打开浏览器）。"""

    def __init__(self, config: Config, server: ServerThread):
        self.config = config
        self.server = server
        self.window = None
        self.update_info: Optional[Dict[str, Any]] = None
        self.updating = False

    # ------------------------------------------------------------------ 信息

    def info(self) -> Dict[str, Any]:
        return {
            "native": True,
            "version": __version__,
            "packaged": resources.is_frozen(),
            "hostname": netinfo.local_hostname(),
            "port": self.server.port,
            "urls": netinfo.candidate_urls(self.server.port),
            "data_dir": str(self.config.data_dir),
            "log": str(resources.log_path()),
        }

    # ------------------------------------------------------------------ 更新

    def start_update_check(self) -> None:
        """启动后在后台查一次，结果放着等网页来取。"""
        if not resources.is_frozen():
            return
        threading.Thread(target=self._check_update, name="whiteboard-update", daemon=True).start()

    def _check_update(self) -> None:
        try:
            info = updater.check()
        except Exception:  # noqa: BLE001 - 检查更新不能把程序带崩
            log.exception("检查更新出错")
            return
        if info:
            log.info("发现新版本 %s", info["version"])
            self.update_info = info

    def pending_update(self) -> Optional[Dict[str, Any]]:
        return self.update_info

    def check_update_now(self) -> Optional[Dict[str, Any]]:
        if not resources.is_frozen():
            return None
        info = updater.check()
        if info:
            self.update_info = info
        return info

    def apply_update(self) -> bool:
        """下载新版本、交给脱离进程的脚本替换，然后退出本进程。"""
        info = self.update_info
        bundle = resources.app_bundle()
        if not info or bundle is None or self.updating:
            return False
        self.updating = True
        workdir = Path(tempfile.mkdtemp(prefix="whiteboard-update-"))
        try:
            archive = updater.download(info["url"], workdir)
            if archive is None:
                return False
            staged = updater.unpack(archive, workdir / "unpacked")
            if staged is None:
                return False
            if not updater.swap_and_restart(staged, bundle):
                return False
        finally:
            if not self.updating:
                updater.cleanup(workdir)
        log.info("更新已就绪，正在退出以完成替换")
        self._quit()
        return True

    def _quit(self) -> None:
        try:
            self.server.save_now()
        except (RuntimeError, TimeoutError):
            pass
        if self.window is not None:
            self.window.destroy()

    # ------------------------------------------------------------------ 导出

    def save_png(self, data_url: str, suggested: str = "whiteboard.png") -> Optional[str]:
        data = _decode_data_url(data_url)
        if data is None:
            return None
        path = self._ask_save_path(suggested)
        if not path:
            return None
        Path(path).write_bytes(data)
        log.info("已导出 PNG：%s", path)
        return path

    def save_profile(self) -> Optional[str]:
        url = f"http://{netinfo.local_hostname()}:{self.server.port}/"
        path = self._ask_save_path("whiteboard.mobileconfig")
        if not path:
            return None
        Path(path).write_bytes(profile.build_profile(url))
        log.info("已导出描述文件：%s", path)
        return path

    def _ask_save_path(self, suggested: str) -> Optional[str]:
        import webview

        if self.window is None:
            return str(Path.home() / "Downloads" / suggested)
        result = self.window.create_file_dialog(
            webview.SAVE_DIALOG,
            directory=str(Path.home() / "Downloads"),
            save_filename=suggested,
        )
        if not result:
            return None
        return result if isinstance(result, str) else result[0]

    # -------------------------------------------------------------- 存储目录

    def choose_data_dir(self) -> Optional[str]:
        """换白板存储目录：重启服务端后 iPad 会自动重连。"""
        import webview

        if self.window is None:
            return None
        result = self.window.create_file_dialog(
            webview.FOLDER_DIALOG, directory=str(self.config.data_dir)
        )
        if not result:
            return None
        new_dir = result if isinstance(result, str) else result[0]
        if str(new_dir) == str(self.config.data_dir):
            return str(new_dir)
        self.config.data_dir = new_dir
        self.config.save()
        self.server.stop()
        server = ServerThread(self.config, advertise=self.server.advertise)
        server.start()
        self.server = server
        if self.window is not None:
            self.window.load_url(f"http://127.0.0.1:{server.port}/?role=mac")
        return str(self.config.data_dir)

    def open_data_dir(self) -> bool:
        path = self.config.data_dir
        path.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", str(path)], check=False)
            elif sys.platform.startswith("win"):
                subprocess.run(["explorer", str(path)], check=False)
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
        except OSError as exc:
            log.warning("打开目录失败：%s", exc)
            return False
        return True

    def open_external(self, url: str) -> bool:
        if not url.startswith(("http://", "https://")):
            return False
        return webbrowser.open(url)


def _decode_data_url(data_url: str) -> Optional[bytes]:
    if not isinstance(data_url, str) or "," not in data_url:
        return None
    header, _, payload = data_url.partition(",")
    if "base64" not in header:
        return None
    try:
        return base64.b64decode(payload)
    except ValueError:
        return None


def run(config: Optional[Config] = None, debug: bool = False, advertise: Optional[bool] = None) -> None:
    """启动服务端并打开 pywebview 窗口（阻塞直到窗口关闭）。"""
    import webview

    resources.setup_logging(debug)
    config = config or Config()
    if advertise is None:
        advertise = netinfo.mdns_default()
    server = ServerThread(config, advertise=advertise)
    server.start()
    config.save()

    api = NativeApi(config, server)
    window = webview.create_window(
        "白板",
        f"http://127.0.0.1:{server.port}/?role=mac",
        js_api=api,
        width=1280,
        height=860,
        min_size=(800, 560),
        background_color="#FDFBFF",
        text_select=False,
    )
    api.window = window
    api.start_update_check()

    def _on_closing() -> None:
        try:
            api.server.save_now()
        except (RuntimeError, TimeoutError) as exc:
            log.warning("退出前保存失败：%s", exc)

    window.events.closing += _on_closing
    try:
        webview.start(debug=debug)
    finally:
        try:
            api.server.save_now()
        except (RuntimeError, TimeoutError):
            pass
        api.server.stop()
        config.save()
