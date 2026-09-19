"""局域网地址与 mDNS 广播。

iPad 通过 Mac 的 ``<主机名>.local`` 访问，不依赖固定 IP。

macOS 自带的 mDNSResponder 已经在发布本机的 ``.local`` 主机名，所以这里的
``_http._tcp`` 注册只是给别的工具做服务发现用，属于可有可无的装饰：默认在
macOS 上关闭，其余平台打开，而且**无论如何都不能影响服务端启动**。
"""

from __future__ import annotations

import asyncio
import logging
import socket
import sys
from typing import List, Optional

log = logging.getLogger(__name__)

# 注册 / 注销 mDNS 的等待上限，超时就放弃广播，绝不拖住启动或退出。
REGISTER_TIMEOUT = 5.0


def local_hostname() -> str:
    """返回 ``xxx.local`` 形式的 mDNS 主机名。"""
    name = socket.gethostname()
    if name.endswith(".local"):
        return name
    # macOS 上 gethostname() 可能返回 FQDN 或带空格的计算机名。
    name = name.split(".")[0].replace(" ", "-").replace("'", "")
    return f"{name}.local"


def lan_ip() -> Optional[str]:
    """取本机在局域网上的 IPv4 地址（不会真正发包）。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.168.255.255", 9))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def candidate_urls(port: int) -> List[str]:
    urls = [f"http://{local_hostname()}:{port}/"]
    ip = lan_ip()
    if ip:
        urls.append(f"http://{ip}:{port}/")
    return urls


def mdns_default() -> bool:
    """macOS 上交给系统的 Bonjour，不自己再注册一遍。"""
    return sys.platform != "darwin"


class MDNSAdvertiser:
    """注册 ``_http._tcp`` 服务。

    必须用 zeroconf 的**异步** API：同步 API 会阻塞调用方所在的事件循环，
    在 asyncio 里调用会抛 ``EventLoopBlocked``，进而把服务端启动拖超时。
    """

    def __init__(self, port: int, name: str = "Whiteboard"):
        self.port = port
        self.name = name
        self._azc = None
        self._info = None

    async def start(self) -> bool:
        """注册服务；任何失败都只记日志，返回 False。"""
        try:
            from zeroconf import ServiceInfo
            from zeroconf.asyncio import AsyncZeroconf
        except ImportError:
            log.info("未安装 zeroconf，跳过 mDNS 广播（.local 主机名仍可用）")
            return False
        ip = lan_ip()
        if not ip:
            log.info("未找到局域网地址，跳过 mDNS 广播")
            return False
        try:
            self._azc = AsyncZeroconf()
            self._info = ServiceInfo(
                "_http._tcp.local.",
                f"{self.name}._http._tcp.local.",
                addresses=[socket.inet_aton(ip)],
                port=self.port,
                properties={"path": "/"},
            )
            await asyncio.wait_for(
                self._azc.async_register_service(self._info), timeout=REGISTER_TIMEOUT
            )
        except Exception as exc:  # noqa: BLE001 - 广播失败不该影响白板本身
            log.warning("mDNS 广播失败（不影响使用）：%s", exc)
            await self.stop()
            return False
        log.info("mDNS 已广播 %s:%s", local_hostname(), self.port)
        return True

    async def stop(self) -> None:
        azc, info = self._azc, self._info
        self._azc = None
        self._info = None
        if azc is None:
            return
        try:
            if info is not None:
                await asyncio.wait_for(azc.async_unregister_service(info), timeout=REGISTER_TIMEOUT)
            await asyncio.wait_for(azc.async_close(), timeout=REGISTER_TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            log.debug("mDNS 注销失败：%s", exc)
