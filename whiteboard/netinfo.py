"""局域网地址与 mDNS 广播。

iPad 通过 Mac 的 ``<主机名>.local`` 访问，不依赖固定 IP；同时注册一个
``_http._tcp`` 服务，方便在别的设备上直接发现。
"""

from __future__ import annotations

import logging
import socket
from typing import List, Optional

log = logging.getLogger(__name__)


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


class MDNSAdvertiser:
    """注册 ``_http._tcp`` 服务；没装 zeroconf 时静默降级。"""

    def __init__(self, port: int, name: str = "Whiteboard"):
        self.port = port
        self.name = name
        self._zeroconf = None
        self._info = None

    def start(self) -> None:
        try:
            from zeroconf import ServiceInfo, Zeroconf
        except ImportError:
            log.info("未安装 zeroconf，跳过 mDNS 广播（.local 主机名仍可用）")
            return
        ip = lan_ip()
        if not ip:
            log.info("未找到局域网地址，跳过 mDNS 广播")
            return
        try:
            self._zeroconf = Zeroconf()
            self._info = ServiceInfo(
                "_http._tcp.local.",
                f"{self.name}._http._tcp.local.",
                addresses=[socket.inet_aton(ip)],
                port=self.port,
                properties={"path": "/"},
                server=local_hostname() + ".",
            )
            self._zeroconf.register_service(self._info)
            log.info("mDNS 已广播 %s:%s", local_hostname(), self.port)
        except OSError as exc:
            log.warning("mDNS 广播失败：%s", exc)
            self.stop()

    def stop(self) -> None:
        if self._zeroconf is not None:
            try:
                if self._info is not None:
                    self._zeroconf.unregister_service(self._info)
                self._zeroconf.close()
            except OSError:
                pass
        self._zeroconf = None
        self._info = None
