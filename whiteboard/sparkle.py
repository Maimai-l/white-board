"""Sparkle 更新：加载应用里带的 Sparkle.framework，交给它检查、下载和安装。

设计见 docs/design/ci-and-updates.zh-CN.md 第 3 节。要点：

- 只有打包好的应用、带着 Sparkle.framework、Info.plist 里写了 ``SUFeedURL`` 和
  ``SUPublicEDKey`` 时才用 Sparkle；其余情况（从源码运行、本地构建没嵌入、CI 没配公钥）
  ``Updater.available`` 为假，应用里没有自动更新：「检查更新」只说明原因。
  1.3.0 之前的自带更新器（查 GitHub Release、自己替换 .app）已经删掉。
- alpha 和正式版共用一个 appcast，靠 ``allowedChannelsForUpdater:`` 决定看不看
  alpha 条目。通道记在配置里，第一次运行时按这个构建自己的通道写入。
- Sparkle 的对象只能在主线程创建和调用；pywebview 的回调都在别的线程，所以经
  ``AppHelper.callAfter`` 转到主线程。
- 自动下载的更新在应用退出后由 Sparkle 的安装器装上：它盯的是进程退出，不依赖
  NSApplication 的退出通知（pywebview 关窗时用的是 ``stop:``，不会发那个通知）。
"""

from __future__ import annotations

import logging
import plistlib
from pathlib import Path
from typing import Any, Dict, Optional, Set

from . import __build__, __channel__, __version__, resources

log = logging.getLogger(__name__)

CHANNELS = ("alpha", "stable")


def resolve_channel(configured: str, build_channel: str) -> str:
    """配置里写了就用配置；没写时 alpha 构建是 alpha，其余都是 stable。"""
    if configured in CHANNELS:
        return configured
    return "alpha" if build_channel == "alpha" else "stable"


def allowed_channels(channel: str) -> Set[str]:
    """交给 Sparkle 的通道集合。默认通道（不带 channel 的正式版条目）Sparkle 总会看。"""
    return {"alpha"} if channel == "alpha" else set()


def bundle_info(bundle: Optional[Path]) -> Dict[str, Any]:
    if bundle is None:
        return {}
    try:
        with open(bundle / "Contents" / "Info.plist", "rb") as handle:
            return plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException) as exc:
        log.warning("读不了 Info.plist：%s", exc)
        return {}


def framework_path(bundle: Optional[Path]) -> Optional[Path]:
    if bundle is None:
        return None
    path = bundle / "Contents" / "Frameworks" / "Sparkle.framework"
    return path if path.is_dir() else None


def configured(info: Dict[str, Any]) -> bool:
    return bool(info.get("SUFeedURL")) and bool(info.get("SUPublicEDKey"))


_DELEGATE_CLASS = None


def _delegate_class():
    """SPUUpdaterDelegate 的实现。ObjC 类名在进程里只能定义一次，所以第一次用到时才建。"""
    global _DELEGATE_CLASS
    if _DELEGATE_CLASS is not None:
        return _DELEGATE_CLASS
    import objc
    from Foundation import NSObject, NSSet

    protocols = []
    try:
        protocols.append(objc.protocolNamed("SPUUpdaterDelegate"))
    except Exception as exc:  # noqa: BLE001 - 找不到协议也能用，只是签名靠默认推断
        log.warning("没找到 SPUUpdaterDelegate 协议：%s", exc)

    class WhiteboardUpdaterDelegate(NSObject, protocols=protocols):
        channels = objc.ivar()

        def allowedChannelsForUpdater_(self, updater):  # noqa: N802 - Objective-C 方法名
            return NSSet.setWithArray_(sorted(self.channels or []))

        def updater_didAbortWithError_(self, updater, error):  # noqa: N802
            log.warning("Sparkle 中止：%s", error)

        def updater_didFinishLoadingAppcast_(self, updater, appcast):  # noqa: N802
            log.info("Sparkle 已读取 appcast")

    _DELEGATE_CLASS = WhiteboardUpdaterDelegate
    return _DELEGATE_CLASS


class Updater:
    """包着 SPUStandardUpdaterController。``available`` 为假时其余方法什么也不做。"""

    def __init__(self, config, bundle: Optional[Path] = None):
        self.config = config
        self.bundle = bundle if bundle is not None else resources.app_bundle()
        self.info = bundle_info(self.bundle)
        self.framework = framework_path(self.bundle)
        self.controller = None
        self.delegate = None  # Sparkle 只弱引用 delegate，这里要拿住它
        self.error: Optional[str] = None

    @property
    def available(self) -> bool:
        return self.framework is not None and configured(self.info)

    @property
    def channel(self) -> str:
        return resolve_channel(self.config.update_channel, __channel__)

    def remember_channel(self) -> None:
        """第一次以打包应用运行时把这个构建的通道写进配置（第 3.1 节）。"""
        if self.config.update_channel or not self.available:
            return
        self.config.update_channel = self.channel
        self.config.save()
        log.info("更新通道：%s", self.channel)

    def start(self) -> bool:
        """在主线程调用：加载 framework，创建并启动 updater。失败只记日志，返回 False。"""
        if not self.available or self.controller is not None:
            return self.controller is not None
        try:
            import objc
            from Foundation import NSBundle

            bundle = NSBundle.bundleWithPath_(str(self.framework))
            if bundle is None or not bundle.load():
                raise RuntimeError(f"加载不了 {self.framework}")
            controller_class = objc.lookUpClass("SPUStandardUpdaterController")
            delegate = _delegate_class().alloc().init()
            delegate.channels = sorted(allowed_channels(self.channel))
            controller = controller_class.alloc().initWithStartingUpdater_updaterDelegate_userDriverDelegate_(
                True, delegate, None
            )
        except Exception as exc:  # noqa: BLE001 - 更新器起不来不能把应用带崩
            self.error = str(exc)
            log.exception("Sparkle 启动失败")
            return False
        self.delegate = delegate
        self.controller = controller
        log.info(
            "Sparkle 已启动：版本 %s（构建 %s），通道 %s，appcast %s",
            __version__,
            __build__ or "-",
            self.channel,
            self.info.get("SUFeedURL"),
        )
        return True

    def start_on_main_thread(self) -> None:
        if not self.available:
            return
        from PyObjCTools import AppHelper

        AppHelper.callAfter(self.start)

    def check_now(self) -> bool:
        """「检查更新」：打开 Sparkle 自己的对话框。返回 False 表示 Sparkle 不可用。"""
        if self.controller is None:
            return False
        from PyObjCTools import AppHelper

        AppHelper.callAfter(self.controller.checkForUpdates_, None)
        return True

    def describe(self) -> Dict[str, Any]:
        """给「关于」面板和 --check-updater 用的状态。"""
        return {
            "updater": "sparkle" if self.available else "none",
            "version": __version__,
            "build": __build__,
            "channel": self.channel,
            "feed": self.info.get("SUFeedURL", ""),
            "framework": str(self.framework) if self.framework else "",
            "public_key": bool(self.info.get("SUPublicEDKey")),
            "started": self.controller is not None,
            "error": self.error,
        }


def self_check(config) -> Dict[str, Any]:
    """打包后的冒烟测试（run.py --check-updater）：framework 能不能加载、配置全不全。

    不启动 updater，不联网。
    """
    updater = Updater(config)
    result = updater.describe()
    result["bundle"] = str(updater.bundle) if updater.bundle else ""
    result["loaded"] = False
    if updater.framework is not None:
        try:
            import objc
            from Foundation import NSBundle

            bundle = NSBundle.bundleWithPath_(str(updater.framework))
            result["loaded"] = bool(bundle is not None and bundle.load())
            result["controller_class"] = objc.lookUpClass("SPUStandardUpdaterController") is not None
        except Exception as exc:  # noqa: BLE001 - 原样报出去，由 CI 判定失败
            result["error"] = f"{type(exc).__name__}: {exc}"
    return result
