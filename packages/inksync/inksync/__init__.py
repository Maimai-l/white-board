"""inksync：手写白板的同步组件。

服务端保存白板（.wbz）、同步操作、按规则控制访问；前端手写板（``createInkPad``）
随包分发，由 :func:`serve_sdk` 提供。可以挂到任何 aiohttp 应用上::

    from inksync import FileStorage, Hub, mount, serve_sdk
    mount(app, Hub(FileStorage("data")), path="/ws")
    serve_sdk(app, prefix="/inksync/")

接口见 docs/design/inksync-2-interface.zh-CN.md。
"""

__version__ = "2.0.0"

from .hub import Hub  # noqa: E402
from .policy import DefaultPolicy, Policy, Principal  # noqa: E402
from .server import Spaces, mount, serve_sdk  # noqa: E402
from .storage import BoardFileError, FileStorage  # noqa: E402

__all__ = [
    "BoardFileError",
    "DefaultPolicy",
    "FileStorage",
    "Hub",
    "Policy",
    "Principal",
    "Spaces",
    "mount",
    "serve_sdk",
    "__version__",
]
