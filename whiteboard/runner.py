"""在后台线程里跑 aiohttp 服务，主线程留给 pywebview 窗口。"""

from __future__ import annotations

import asyncio
import errno
import logging
import threading
from pathlib import Path
from typing import Optional

from aiohttp import web

from . import __version__, backup
from .config import Config
from .netinfo import BonjourService, MDNSAdvertiser, bonjour_default
from .server import HUB_KEY, create_app
from .store import BoardStore

log = logging.getLogger(__name__)

PORT_ATTEMPTS = 20


class ServerThread:
    """启动 / 停止服务端，并暴露实际使用的端口。"""

    def __init__(
        self,
        config: Config,
        store: Optional[BoardStore] = None,
        advertise: bool = True,
        bonjour: Optional[bool] = None,
    ):
        self.config = config
        self.store = store
        self.advertise = advertise
        # iPad 外壳靠 _whiteboard._tcp 找到这台 Mac，默认只在 macOS 上注册
        self.bonjour = bonjour_default() if bonjour is None else bonjour
        self.port: int = config.port
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._runner: Optional[web.AppRunner] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._error: Optional[BaseException] = None
        self._mdns: Optional[MDNSAdvertiser] = None
        self._mdns_task: Optional[asyncio.Task] = None
        self._bonjour: Optional[BonjourService] = None
        self._bonjour_task: Optional[asyncio.Task] = None
        self.app: Optional[web.Application] = None

    # --------------------------------------------------------------- 生命周期

    def start(self, timeout: float = 20.0) -> int:
        self._thread = threading.Thread(target=self._run, name="whiteboard-server", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            self.stop(timeout=2.0)
            raise RuntimeError("服务端启动超时")
        if self._error is not None:
            raise self._error
        return self.port

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve())
            loop.run_forever()
        except BaseException as exc:  # noqa: BLE001 - 交给主线程报告
            self._error = exc
            self._ready.set()
        finally:
            try:
                loop.run_until_complete(self._shutdown())
            finally:
                loop.close()

    async def _serve(self) -> None:
        # 换了版本先备份一次白板，再让新代码碰任何文件
        backup.backup_if_upgraded(self.config, __version__)
        self.app = create_app(self.config, self.store)
        # 兜底：万一还有连接没断干净，也不要让关闭卡在默认的 60 秒上
        self._runner = web.AppRunner(self.app, shutdown_timeout=2.0)
        await self._runner.setup()

        last_error: Optional[OSError] = None
        wanted = self.config.requested_port
        for offset in range(PORT_ATTEMPTS):
            port = wanted + offset
            site = web.TCPSite(self._runner, host="0.0.0.0", port=port, reuse_address=True)
            try:
                await site.start()
            except OSError as exc:
                if exc.errno not in (errno.EADDRINUSE, errno.EACCES):
                    raise
                last_error = exc
                continue
            self.port = port
            if port != wanted:
                log.warning("端口 %s 被占用，这次改用 %s（不写进配置，下次仍从 %s 开始）", wanted, port, wanted)
            # 页面上给 iPad 的地址、描述文件都读 config.port，要是实际的端口
            self.config.set_bound_port(port)
            break
        else:
            raise last_error or OSError("没有可用端口")

        log.info("服务端已就绪：http://0.0.0.0:%s", self.port)
        self._ready.set()

        # mDNS 放到启动完成之后的后台任务里：它是可选的装饰，失败或慢了都不该
        # 影响白板本身（同步版 zeroconf 会阻塞事件循环，绝不能在这里直接调）。
        if self.advertise:
            self._mdns = MDNSAdvertiser(self.port)
            self._mdns_task = asyncio.create_task(self._advertise())
        # 外壳的服务注册同样放在后台：端口已经定下来（顺延之后的值），TXT 里写的
        # 就是实际监听的端口
        if self.bonjour:
            self._bonjour = BonjourService(self.port, __version__)
            self._bonjour_task = asyncio.create_task(self._register_bonjour())

    async def _advertise(self) -> None:
        try:
            await self._mdns.start()
        except Exception:  # noqa: BLE001 - 广播异常不能冒泡到服务端
            log.exception("mDNS 广播异常（不影响使用）")

    async def _register_bonjour(self) -> None:
        try:
            await self._bonjour.start()
        except Exception:  # noqa: BLE001 - 注册异常不能冒泡到服务端
            log.exception("Bonjour 注册异常（不影响使用）")

    async def _shutdown(self) -> None:
        if self._bonjour_task is not None:
            self._bonjour_task.cancel()
            self._bonjour_task = None
        if self._bonjour is not None:
            await self._bonjour.stop()
            self._bonjour = None
        if self._mdns_task is not None:
            self._mdns_task.cancel()
            self._mdns_task = None
        if self._mdns is not None:
            await self._mdns.stop()
            self._mdns = None
        if self._runner is not None:
            await self._runner.cleanup()  # 触发 on_cleanup：保存所有白板
            self._runner = None

    def stop(self, timeout: float = 8.0) -> None:
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout)
        self._loop = None

    # ------------------------------------------------------------------ 工具

    def run_coroutine(self, coro):
        """从主线程往服务端事件循环里丢一个协程。"""
        if self._loop is None:
            raise RuntimeError("服务端未启动")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    @property
    def hub(self):
        return self.app[HUB_KEY] if self.app is not None else None

    def open_file(self, path, folder: str = "", timeout: float = 120.0):
        """在服务端打开一个本地文件（见 ``server.open_local_file``），返回新白板的 meta。"""
        from .server import open_local_file  # noqa: WPS433

        hub = self.hub
        if hub is None:
            raise RuntimeError("服务端未启动")
        return self.run_coroutine(open_local_file(hub, Path(path), folder)).result(timeout)

    def save_now(self) -> None:
        hub = self.hub
        if hub is not None:
            future = self.run_coroutine(_call_save(hub))
            future.result(timeout=10)


async def _call_save(hub) -> None:
    hub.save_all()
