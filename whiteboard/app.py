"""Mac 端的 pywebview 窗口。

窗口里装的就是 iPad 打开的同一个网页，只是以 ``role=mac`` 载入，
多出白板列表、尺寸 / 背景设置、缩放、导出与描述文件分发。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional

from . import __version__, backup, netinfo, profile, resources, updater
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
        self.update_info: Optional[Dict[str, Any]] = None
        self.staged_app: Optional[Path] = None
        self.updating = False
        self.downloading = False
        self.install_on_quit = False
        self.progress = 0.0
        self.stage_error: Optional[str] = None

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
            "releases": f"https://github.com/{updater.REPO}/releases",
        }

    # ------------------------------------------------------------------ 更新

    def start_update_check(self) -> None:
        """启动后在后台查一次。只有明确开了自动下载才会顺手把包下好。"""
        if not resources.is_frozen():
            return
        threading.Thread(target=self._check_update, name="whiteboard-update", daemon=True).start()

    def _check_update(self, force: bool = False) -> Dict[str, Any]:
        """返回带 status 的结果，界面照原样显示，不再让用户面对一个没头没尾的对勾。"""
        try:
            result = updater.check()
        except Exception as exc:  # noqa: BLE001 - 检查更新不能把程序带崩
            log.exception("检查更新出错")
            return {"status": "error", "message": str(exc)}
        if result.get("status") != "update":
            return result
        if not force and result["version"] == self.config.skip_version:
            log.info("版本 %s 已被跳过", result["version"])
            return {"status": "skipped", "version": result["version"]}
        log.info("发现新版本 %s", result["version"])
        self.update_info = result
        # 手动检查只回报结果；自动下载是用户明确打开的开关，才会在后台拉包。
        if self.config.auto_update and not force:
            threading.Thread(target=self._stage_update, daemon=True).start()
        return result

    def _stage_update(self) -> bool:
        """把更新包下好、解开放着，之后「安装」就是一瞬间的事。"""
        info = self.update_info
        if not info or self.staged_app is not None or self.downloading:
            return self.staged_app is not None
        self.downloading = True
        self.progress = 0.0
        self.stage_error = None
        workdir = Path(tempfile.mkdtemp(prefix="whiteboard-update-"))
        try:
            archive = updater.download(info["url"], workdir, on_progress=self._on_progress)
            if archive is None:
                self.stage_error = "下载失败"
                return False
            staged = updater.unpack(archive, workdir / "unpacked")
            if staged is None:
                self.stage_error = "更新包解不开"
                return False
            # 解出来先验一遍：宁可这次不更新，也不要换上一个打不开的 .app
            problem = updater.verify_bundle(staged)
            if problem is not None:
                log.error("更新包不可用：%s", problem)
                self.stage_error = problem
                return False
            self.staged_app = staged
            self.progress = 1.0
            log.info("更新包已就绪：%s", staged)
            return True
        finally:
            self.downloading = False
            if self.staged_app is None:
                updater.cleanup(workdir)

    def _on_progress(self, value: float) -> None:
        self.progress = value

    # -------------------------------------------------- 给网页调用的更新接口

    def update_state(self) -> Dict[str, Any]:
        return {
            "current": __version__,
            "packaged": resources.is_frozen(),
            "auto": self.config.auto_update,
            "info": self.update_info,
            "staged": self.staged_app is not None,
            "downloading": self.downloading,
            "progress": round(self.progress, 3),
            "onQuit": self.install_on_quit,
            "error": self.stage_error,
        }

    def pending_update(self) -> Optional[Dict[str, Any]]:
        return self.update_info

    def check_update_now(self) -> Dict[str, Any]:
        if not resources.is_frozen():
            return {"status": "source", "version": __version__}
        return self._check_update(force=True)

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

    def set_auto_update(self, enabled: bool) -> bool:
        self.config.auto_update = bool(enabled)
        self.config.save()
        if self.config.auto_update and self.update_info and self.staged_app is None:
            threading.Thread(target=self._stage_update, daemon=True).start()
        return self.config.auto_update

    def skip_update(self) -> bool:
        if not self.update_info:
            return False
        self.config.skip_version = self.update_info["version"]
        self.config.save()
        log.info("跳过版本 %s", self.config.skip_version)
        self.update_info = None
        return True

    def download_update(self) -> bool:
        """网页点「下载」时用；已经下好就直接返回。"""
        if self.staged_app is not None:
            return True
        return self._stage_update()

    def install_update(self, mode: str = "now") -> bool:
        """安装已经下好的更新。``mode`` 为 ``now`` 立刻重启，``quit`` 等退出时再装。

        不在这里下载：界面先调 download_update，这样进度条才有意义。
        """
        if self.updating or self.staged_app is None:
            return False
        bundle = resources.app_bundle()
        if bundle is None:
            return False
        if mode == "quit":
            self.install_on_quit = True
            log.info("退出时安装更新")
            return True

        self.updating = True
        self._save_before_exit()
        if not updater.swap_and_restart(self.staged_app, bundle, relaunch=True):
            self.updating = False
            return False

        # 替换脚本在等我们的进程消失，所以这里直接退干净。
        #
        # 不能走 window.destroy()：JS 接口的调用跑在自己的线程里，销毁窗口之后
        # 它还要 evaluate_js 把返回值送回页面，窗口没了就卡在那儿；而且这些线程
        # 不是守护线程，进程也退不掉。延迟一点点是为了让这次调用先返回给界面。
        log.info("更新已就绪，正在退出以完成替换")
        threading.Timer(0.3, lambda: os._exit(0)).start()
        return True

    def _save_before_exit(self) -> None:
        try:
            self.server.save_now()
        except (RuntimeError, TimeoutError) as exc:
            log.warning("退出前保存失败：%s", exc)

    def finish_pending_install(self) -> None:
        """窗口关闭时调用：装上之前下好的更新，但不再把应用打开。"""
        if not self.install_on_quit or self.staged_app is None or self.updating:
            return
        bundle = resources.app_bundle()
        if bundle is None:
            return
        self.updating = True
        updater.swap_and_restart(self.staged_app, bundle, relaunch=False)
        log.info("退出后将完成更新替换")

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

    def export_doc(self, board_id: str) -> Any:
        """文档板导出：服务端把笔迹合进原件，这边负责挑保存位置。

        返回保存后的路径；用户取消返回 None，失败返回 False。
        """
        import urllib.error
        import urllib.parse
        import urllib.request

        url = f"http://127.0.0.1:{self.server.port}/api/export/{board_id}"
        try:
            with urllib.request.urlopen(url, timeout=300) as response:
                disposition = response.headers.get("Content-Disposition", "")
                data = response.read()
        except urllib.error.HTTPError as exc:
            log.error("导出失败：%s %s", exc.code, exc.read()[:200].decode("utf-8", "replace"))
            return False
        except OSError as exc:
            log.error("导出失败：%s", exc)
            return False

        suggested = "whiteboard.pdf"
        marker = "filename*=UTF-8''"
        if marker in disposition:
            suggested = urllib.parse.unquote(disposition.split(marker, 1)[1].strip('"'))
        path = self._ask_save_path(suggested)
        if not path:
            return None
        Path(path).write_bytes(data)
        log.info("已导出文档板：%s（%.1f KB）", path, len(data) / 1024)
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
                self._message(f"打不开 {Path(path).name}：{exc}")
            except Exception:  # noqa: BLE001 - 一个文件出错不该挡住后面的
                log.exception("打开文件出错：%s", path)
                self._message(f"打不开 {Path(path).name}")

    def _folder(self) -> str:
        try:
            folder = self.window.evaluate_js("whiteboard.ui.dropFolder(whiteboard.state.id)")
        except Exception:  # noqa: BLE001 - 网页没准备好就放在最外层
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
        f"<p>{html.escape(message)}</p></body>"
    )


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
        # 1.x 的存储目录要转换格式，转换前的备份没做成：不启动，告诉用户原因
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
    api.start_update_check()

    def _on_closing() -> None:
        try:
            api.server.save_now()
        except (RuntimeError, TimeoutError) as exc:
            log.warning("退出前保存失败：%s", exc)
        api.finish_pending_install()

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
