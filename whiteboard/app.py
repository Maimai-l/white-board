"""Mac 端的 pywebview 窗口。

窗口里装的就是 iPad 打开的同一个网页，只是以 ``role=mac`` 载入，
多出白板列表、尺寸 / 背景设置、缩放、导出与描述文件分发。
"""

from __future__ import annotations

import base64
import json
import logging
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional

from . import RELEASES_URL, __version__, backup, netinfo, profile, resources, sparkle
from .config import Config
from .runner import ServerThread
from .server import list_apps

log = logging.getLogger(__name__)


class NativeApi:
    """暴露给网页的本地能力（保存文件、选目录、打开浏览器）。"""

    def __init__(self, config: Config, server: ServerThread):
        self.config = config
        self.server = server
        self.window = None
        # 更新全由 Sparkle 负责（打包好的应用才有）；从源码运行时用 git 更新
        self.sparkle = sparkle.Updater(config)

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
            "remote_permissions": self.config.remote_permissions,
            "apps": list_apps(self.config),
            "ipad_home": self.config.ipad_home,
            "log": str(resources.log_path()),
            "releases": RELEASES_URL,
            "updates": self.sparkle.describe(),
        }

    # ------------------------------------------------------------------ 更新

    def check_update_now(self) -> Dict[str, Any]:
        """「检查更新」：打包好的应用交给 Sparkle，它自己的窗口显示结果。

        从源码运行没有 Sparkle，用 git 更新；打包了却没带上能用的 Sparkle
        （本地构建没嵌入、framework 加载失败）时说清楚原因，让用户去下载页。
        """
        if not resources.is_frozen():
            return {"status": "source", "version": __version__}
        if self.sparkle.check_now():
            return {"status": "sparkle", "version": __version__}
        log.warning("app.check_update_now：Sparkle 不可用：%s", self.sparkle.error or "这个构建没有带 Sparkle")
        return {"status": "unavailable", "version": __version__, "message": self.sparkle.error or ""}

    def set_remote_permission(self, name: str, enabled: bool) -> Optional[Dict[str, bool]]:
        """放开 / 收回别的设备的某一项权限。名字见 config.REMOTE_PERMISSIONS。"""
        try:
            granted = self.config.set_remote_permission(name, enabled)
        except ValueError:
            log.warning("未知的权限名：%s", name)
            return None
        self.config.save()
        log.info("其他设备的 %s 权限：%s", name, "开" if granted[name] else "关")
        try:
            self.server.refresh_permissions()
        except Exception:  # noqa: BLE001 - 设置已经存下，下次连接时照样生效
            log.exception("通知已连接的设备更新权限失败")
        return granted

    def set_ipad_home(self, name: str) -> Optional[str]:
        """设定 iPad 打开时进入的应用；空串是白板本身。返回设定后的值，名字不对返回 None。"""
        name = str(name or "")
        if name and name not in list_apps(self.config):
            log.warning("没有这个应用：%s", name)
            return None
        self.config.ipad_home = name
        self.config.save()
        log.info("iPad 首页：%s", name or "白板")
        return name

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

    def export_doc(self, board_id: str, options: Optional[Dict[str, Any]] = None) -> Any:
        """导出：服务端出文件，这边负责挑保存位置。

        ``options`` 是导出对话框的选择（POST /api/export/{board} 的请求体：格式、页框、
        选中的页、清晰度）；不给时是文档板原来的整份导出。保存对话框里建议的文件名
        和扩展名来自服务端的 Content-Disposition。

        返回保存后的路径；用户取消返回 None，失败返回 False。
        """
        data_and_name = self._fetch_export(board_id, options)
        if data_and_name is None:
            return False
        data, suggested = data_and_name
        path = self._ask_save_path(suggested)
        if not path:
            return None
        Path(path).write_bytes(data)
        log.info("已导出白板 %s：%s（%.1f KB）", board_id, path, len(data) / 1024)
        return path

    def _fetch_export(self, board_id: str, options: Optional[Dict[str, Any]]):
        """向本机的服务端要导出的文件，返回 ``(内容, 建议的文件名)``；失败记日志并返回 None。"""
        import urllib.error
        import urllib.parse
        import urllib.request

        url = f"http://127.0.0.1:{self.server.port}/api/export/{urllib.parse.quote(board_id)}"
        request = urllib.request.Request(url)
        if options is not None:
            request = urllib.request.Request(
                url,
                data=json.dumps(options).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                disposition = response.headers.get("Content-Disposition", "")
                data = response.read()
        except urllib.error.HTTPError as exc:
            log.error("app._fetch_export 导出失败：%s %s", exc.code, exc.read()[:200].decode("utf-8", "replace"))
            return None
        except OSError as exc:
            log.error("app._fetch_export 导出失败：%s", exc)
            return None

        suggested = "whiteboard.pdf"
        marker = "filename*=UTF-8''"
        if marker in disposition:
            suggested = urllib.parse.unquote(disposition.split(marker, 1)[1].strip('"'))
        return data, suggested

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
        old_dir = self.config.data_dir
        advertise, bonjour = self.server.advertise, self.server.bonjour
        self.config.data_dir = new_dir
        self.server.stop()
        server = ServerThread(self.config, advertise=advertise, bonjour=bonjour)
        try:
            server.start()
        except Exception:  # noqa: BLE001 - 新目录用不了就退回原来的，不能让服务端就此停着
            log.exception("换存储目录失败，退回 %s", old_dir)
            self.config.data_dir = old_dir
            server = ServerThread(self.config, advertise=advertise, bonjour=bonjour)
            server.start()
            self.server = server
            if self.window is not None:
                self.window.load_url(f"http://127.0.0.1:{server.port}/?role=mac")
            return None
        # 新目录真的用上了才写进配置
        self.config.save()
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

    def open_log(self) -> bool:
        """在访达里选中日志文件，方便直接拖去看。"""
        path = resources.log_path()
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", "-R", str(path)], check=False)
            else:
                subprocess.run(["xdg-open", str(path.parent)], check=False)
        except OSError as exc:
            log.warning("打开日志失败：%s", exc)
            return False
        return True

    def open_external(self, url: str) -> bool:
        if not url.startswith(("http://", "https://")):
            return False
        return webbrowser.open(url)


class FileOpener:
    """Finder 交给应用的文件（「打开方式」、双击、拖到程序坞图标上）。

    应用由打开文件启动时，文件往往先于网页加载完到达，所以先排队，等网页加载完
    再按顺序打开。每个文件都放进网页当前所在的文件夹，和拖进窗口相同。
    """

    def __init__(self, server: ServerThread):
        self.server = server
        self.window = None
        self._pending: list = []
        self._ready = False
        self._lock = threading.Lock()

    def open(self, paths) -> None:
        with self._lock:
            self._pending.extend(str(path) for path in paths)
            ready = self._ready
        if ready:
            threading.Thread(target=self._flush, name="open-files", daemon=True).start()

    def on_loaded(self) -> None:
        with self._lock:
            self._ready = True
        threading.Thread(target=self._flush, name="open-files", daemon=True).start()

    def _flush(self) -> None:
        from .server import OpenFileError  # noqa: WPS433

        while True:
            with self._lock:
                if not self._pending:
                    return
                path = self._pending.pop(0)
            log.info("打开文件：%s", path)
            try:
                self.server.open_file(path, self._folder())
            except OpenFileError as exc:
                log.warning("app.FileOpener 打不开 %s：%s", path, exc)
                self._message(f"打不开 {Path(path).name}")
            except Exception:  # noqa: BLE001 - 一个文件出错不该挡住后面的
                log.exception("打开文件出错：%s", path)
                self._message(f"打不开 {Path(path).name}")

    def _folder(self) -> str:
        try:
            folder = self.window.evaluate_js("whiteboard.ui.dropFolder(whiteboard.state.id)")
        except Exception as exc:  # noqa: BLE001 - 网页没准备好就放在最外层
            log.warning("app._folder：问不到网页当前的文件夹，放在最外层：%s", exc)
            return ""
        return folder if isinstance(folder, str) else ""

    def _message(self, text: str) -> None:
        try:
            self.window.evaluate_js(f"whiteboard.ui.message({json.dumps(text, ensure_ascii=False)}, 'close', 6000)")
        except Exception:  # noqa: BLE001
            log.warning("%s", text)


def install_open_files_handler(callback) -> bool:
    """让 macOS 把「打开文件」事件交给 ``callback(paths)``。

    pywebview 没有这项功能：这里给它的应用代理类补上 ``application:openFiles:``。
    NSApplication 收到打开文件的 Apple Event 时会调用代理的这个方法。
    """
    if sys.platform != "darwin":
        return False
    try:
        import objc  # noqa: WPS433
        from webview.platforms.cocoa import BrowserView  # noqa: WPS433
    except Exception:  # noqa: BLE001 - 版本不对就只是少了这项功能
        log.exception("无法接管打开文件事件")
        return False

    def application_openFiles_(self, app, filenames):  # noqa: N802 - Objective-C 方法名
        try:
            callback([str(name) for name in filenames])
        finally:
            app.replyToOpenOrPrint_(0)  # NSApplicationDelegateReplySuccess

    objc.classAddMethods(
        BrowserView.AppDelegate,
        [objc.selector(application_openFiles_, selector=b"application:openFiles:", signature=b"v@:@@")],
    )
    return True


def _error_page(message: str) -> str:
    import html

    return (
        "<!doctype html><meta charset='utf-8'><body style='font:15px -apple-system,sans-serif;"
        "padding:28px;color:#1b1b1f;background:#fdfbff'><h3 style='margin-top:0'>白板没有启动</h3>"
        + "".join(f"<p>{html.escape(line)}</p>" for line in message.splitlines())
        + "</body>"
    )


def _decode_data_url(data_url: str) -> Optional[bytes]:
    if not isinstance(data_url, str) or "," not in data_url:
        return None
    header, _, payload = data_url.partition(",")
    if "base64" not in header:
        return None
    try:
        return base64.b64decode(payload)
    except ValueError as exc:
        log.warning("app._decode_data_url：网页传来的图片解不开：%s", exc)
        return None


def run(
    config: Optional[Config] = None,
    debug: bool = False,
    advertise: Optional[bool] = None,
    bonjour: Optional[bool] = None,
) -> None:
    """启动服务端并打开 pywebview 窗口（阻塞直到窗口关闭）。"""
    import webview

    resources.setup_logging(debug)
    config = config or Config()
    if advertise is None:
        advertise = netinfo.mdns_default()
    server = ServerThread(config, advertise=advertise, bonjour=bonjour)
    try:
        server.start()
    except backup.BackupError as exc:
        # 1.0.x 的存储目录要转换格式，转换前的备份没做成：不启动，告诉用户原因
        log.error("%s", exc)
        webview.create_window("白板", html=_error_page(str(exc)), width=520, height=260)
        webview.start()
        return
    config.save()
    opener = FileOpener(server)
    install_open_files_handler(opener.open)

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
    opener.window = window
    window.events.loaded += opener.on_loaded
    api.sparkle.remember_channel()

    def _on_closing() -> None:
        try:
            api.server.save_now()
        except (RuntimeError, TimeoutError) as exc:
            log.warning("退出前保存失败：%s", exc)

    window.events.closing += _on_closing
    try:
        # Sparkle 要在主线程、事件循环跑起来之后创建；start 的回调在别的线程，转一下
        webview.start(api.sparkle.start_on_main_thread, debug=debug)
    finally:
        try:
            api.server.save_now()
        except (RuntimeError, TimeoutError) as exc:
            log.warning("app.run：窗口关闭后保存失败：%s", exc)
        api.server.stop()
        config.save()
