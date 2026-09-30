"""inksync：手写白板的同步服务端。

白板数据格式（.wbz，docs/format.md）、操作日志与广播（Hub）和 WebSocket 同步协议
（docs/protocol.md）。和前端手写板（createInkPad）配合使用，可以挂到任何 aiohttp 应用上：

    from inksync import BoardStore, Hub, mount
    mount(app, Hub(BoardStore("data")))
"""

__version__ = "0.1.0"

from .hub import Hub  # noqa: E402
from .store import BoardStore  # noqa: E402
from .ws import mount, websocket_handler  # noqa: E402

__all__ = ["BoardStore", "Hub", "mount", "websocket_handler", "__version__"]
