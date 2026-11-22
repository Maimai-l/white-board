"""Mac 窗口的标题栏：红绿灯和网页顶栏在同一行。

窗口用 pywebview 的 ``frameless=True``：内容铺满整个窗口（含标题栏那一条），标题栏透明、
标题隐藏，网页的顶栏（52 px）就是窗口的标题栏。pywebview 顺手把红绿灯藏起来了，这里把它们
放出来，摆到顶栏的垂直中心，左边离窗口 20 px（网页那边 ``--lights`` 让出这一块）。

为什么不留系统标题栏：网页里的拖动区域（``.pywebview-drag-region``）是 pywebview 用 JS
做的，它按「鼠标的屏幕坐标减去鼠标在网页里的坐标」放窗口，默认网页从窗口最顶上开始。带
系统标题栏时网页在标题栏下面，点一下拖动区域窗口就往下跳一个标题栏的高度。frameless 之后
网页和窗口同一个原点，这个算法就对了。

系统会在改变窗口大小、退出全屏后把红绿灯放回默认位置，所以每次都要重新摆。全屏时红绿灯
由系统管，网页那边把让出的空白收回来（``data-window="full"``）。

只在 macOS 的 pywebview 窗口里有意义；别处调用这里的函数什么也不做。所有 AppKit 调用都在
主线程上，出错只记日志：红绿灯摆不好也不影响用。
"""

from __future__ import annotations

import logging
import sys
from typing import Any, List, Sequence, Tuple

log = logging.getLogger(__name__)

# 和 app.css 里的 --top-inset、--lights 一致：顶栏 52 高，红绿灯离左边 20，三个按钮之间 8
BAR_HEIGHT = 52
LIGHTS_LEFT = 20
LIGHTS_GAP = 8
# 标题栏那层只留到绿灯右边这么宽：它盖在网页上面，太宽会挡住顶栏里的按钮
CONTAINER_PAD = 12

FULLSCREEN_MASK = 1 << 14  # NSWindowStyleMaskFullScreen


def light_origins(sizes: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """三个按钮（宽、高）在标题栏那层里的左下角坐标：从左往右排，垂直居中在 BAR_HEIGHT 里。"""
    origins = []
    x = float(LIGHTS_LEFT)
    for width, height in sizes:
        origins.append((x, (BAR_HEIGHT - height) / 2))
        x += width + LIGHTS_GAP
    return origins


def container_width(sizes: Sequence[Tuple[float, float]]) -> float:
    """标题栏那层的宽度：盖住三个按钮再多一点。"""
    origins = light_origins(sizes)
    if not origins:
        return 0.0
    return origins[-1][0] + sizes[-1][0] + CONTAINER_PAD


def window_mode(fullscreen: bool) -> str:
    """网页 <html data-window> 的值：平时给红绿灯让出位置，全屏时不用。"""
    return "full" if fullscreen else "inset"


def place_traffic_lights(ns_window: Any) -> bool:
    """在主线程调用：放出红绿灯，摆到顶栏的垂直中心。全屏时不动（系统管）。"""
    try:
        import AppKit

        if ns_window.styleMask() & FULLSCREEN_MASK:
            return False
        kinds = (AppKit.NSWindowCloseButton, AppKit.NSWindowMiniaturizeButton, AppKit.NSWindowZoomButton)
        buttons = [ns_window.standardWindowButton_(kind) for kind in kinds]
        if not all(buttons):
            log.warning("titlebar.place_traffic_lights：找不到红绿灯按钮")
            return False
        for button in buttons:
            button.setHidden_(False)  # frameless 会把它们藏起来
        sizes = [(b.frame().size.width, b.frame().size.height) for b in buttons]
        # 按钮所在的标题栏那层（NSTitlebarContainerView），默认 28 高、和窗口一样宽。
        # 拉到顶栏那么高，按钮挪下去才不会被裁掉；宽度只留到绿灯右边，不挡网页顶栏里的按钮。
        container = buttons[0].superview().superview()
        frame = container.frame()
        frame.size.height = BAR_HEIGHT
        frame.size.width = container_width(sizes)
        frame.origin.x = 0
        frame.origin.y = ns_window.frame().size.height - BAR_HEIGHT
        container.setFrame_(frame)
        for button, (x, y) in zip(buttons, light_origins(sizes)):
            button.setFrameOrigin_(AppKit.NSMakePoint(x, y))
        return True
    except Exception:  # noqa: BLE001 - 摆不好就留在系统默认的位置
        log.exception("titlebar.place_traffic_lights：摆放红绿灯失败")
        return False


def zoom_on_double_click(ns_window: Any) -> str:
    """在主线程调用：双击标题栏空白处，按系统设置放大或最小化（系统设置 → 桌面与程序坞）。"""
    import AppKit

    action = AppKit.NSUserDefaults.standardUserDefaults().stringForKey_("AppleActionOnDoubleClick")
    action = str(action) if action else "Maximize"
    if action == "Minimize":
        ns_window.performMiniaturize_(None)
    elif action != "None":
        ns_window.performZoom_(None)
    return action


def install(window: Any) -> None:
    """创建窗口之后、webview.start 之前调用：挂上摆放红绿灯和全屏切换的事件。"""
    if sys.platform != "darwin":
        return
    from PyObjCTools import AppHelper

    def place() -> None:
        if window.native is not None:
            AppHelper.callAfter(place_traffic_lights, window.native)

    def set_mode(fullscreen: bool) -> None:
        try:
            window.evaluate_js(f"document.documentElement.dataset.window = {window_mode(fullscreen)!r}")
        except Exception:  # noqa: BLE001 - 页面还没加载好时会失败，加载后页面自己按地址里的参数设
            log.exception("titlebar.install：通知页面全屏状态失败")

    def on_before_show() -> None:
        # 在主线程同步执行：窗口显示的时候红绿灯已经在位置上
        place_traffic_lights(window.native)

    def on_resized(width: int, height: int) -> None:
        place()

    def on_maximized() -> None:  # pywebview 的 maximized 是进入全屏
        set_mode(True)

    def on_restored() -> None:
        set_mode(False)
        place()

    window.events.before_show += on_before_show
    window.events.shown += place
    window.events.resized += on_resized
    window.events.maximized += on_maximized
    window.events.restored += on_restored
