"""在后台线程里跑 aiohttp 服务，主线程留给 pywebview 窗口。"""

from __future__ import annotations

import asyncio
import errno
import logging
import threading
from typing import Optional

from aiohttp import web

from .config import Config
from .netinfo import MDNSAdvertiser
from .server import HUB_KEY, create_app
from .store import BoardStore

log = logging.getLogger(__name__)

PORT_ATTEMPTS = 20


class ServerThread:
    """启动 / 停止服务端，并暴露实际使用的端口。"""

    def __init__(self, config: Config, store: Optional[BoardStore] = None, advertise: bool = True):
        self.config = config
        self.store = store
        self.advertise = advertise
        self.port: int = config.port
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._runner: Optional[web.AppRunner] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._error: Optional[BaseException] = None
        self._mdns: Optional[MDNSAdvertiser] = None
        self.app: Optional[web.Application] = None

    # --------------------------------------------------------------- 生命周期

    def start(self, timeout: float = 15.0) -> int:
        self._thread = threading.Thread(target=self._run, name="whiteboard-server", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
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
        self.app = create_app(self.config, self.store)
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()

        last_error: Optional[OSError] = None
        for offset in range(PORT_ATTEMPTS):
            port = self.config.port + offset
            site = web.TCPSite(self._runner, host="0.0.0.0", port=port, reuse_address=True)
            try:
                await site.start()
            except OSError as exc:
                if exc.errno not in (errno.EADDRINUSE, errno.EACCES):
                    raise
                last_error = exc
                continue
            self.port = port
            if port != self.config.port:
                log.warning("端口 %s 被占用，改用 %s", self.config.port, port)
                self.config.port = port
            break
        else:
            raise last_error or OSError("没有可用端口")

        if self.advertise:
            self._mdns = MDNSAdvertiser(self.port)
            self._mdns.start()
        log.info("服务端已就绪：http://0.0.0.0:%s", self.port)
        self._ready.set()

    async def _shutdown(self) -> None:
        if self._mdns is not None:
            self._mdns.stop()
            self._mdns = None
        if self._runner is not None:
            await self._runner.cleanup()  # 触发 on_cleanup：保存所有白板
            self._runner = None

    def stop(self, timeout: float = 10.0) -> None:
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

    def save_now(self) -> None:
        hub = self.hub
        if hub is not None:
            future = self.run_coroutine(_call_save(hub))
            future.result(timeout=10)


async def _call_save(hub) -> None:
    hub.save_all()
