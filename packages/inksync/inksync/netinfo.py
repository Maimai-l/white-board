"""局域网地址与 mDNS 广播。

iPad 通过 Mac 的 ``<主机名>.local`` 访问，不依赖固定 IP。

macOS 自带的 mDNSResponder 已经在发布本机的 ``.local`` 主机名，所以这里的
``_http._tcp`` 注册只是给别的工具做服务发现用，属于可有可无的装饰：默认在
macOS 上关闭，其余平台打开，而且**无论如何都不能影响服务端启动**。

``_whiteboard._tcp`` 是另一回事：iPad 外壳靠它自动找到可以连接的服务
（docs/ipad-shell.md 8.4 节），在 macOS 上默认打开，走系统的 mDNSResponder，见
``BonjourService``。白板和其他项目（例如刷题）注册同一种服务，用 TXT 记录里的
``source`` 区分；其他项目用 :func:`advertise` 注册。
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import sys
import time
from typing import Dict, List, Optional, Tuple

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


_LAN_IP_CACHE: Tuple[float, Optional[str]] = (0.0, None)
LAN_IP_TTL = 5.0


def cached_lan_ip() -> Optional[str]:
    """``lan_ip()`` 的短缓存：请求路径上会反复问，地址又不会一秒一变。"""
    global _LAN_IP_CACHE
    now = time.monotonic()
    if now - _LAN_IP_CACHE[0] < LAN_IP_TTL:
        return _LAN_IP_CACHE[1]
    _LAN_IP_CACHE = (now, lan_ip())
    return _LAN_IP_CACHE[1]


def is_own_address(remote: Optional[str]) -> bool:
    """这个连接是不是来自本机。

    依据是 TCP 对端地址，不是客户端自己声称的身份——那个随便填。回环之外还认
    本机自己的局域网地址：Mac 上点「在浏览器打开」走的就是那个地址，仍然算本机。
    取不到地址就当成外部，宁可少给权限。
    """
    if not remote:
        return False
    try:
        address = ipaddress.ip_address(remote.split("%")[0])
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None and mapped.is_loopback:
        return True
    own = cached_lan_ip()
    return bool(own and str(address) == own)


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
    """用 zeroconf 注册一个服务，默认是 ``_http._tcp``。

    必须用 zeroconf 的**异步** API：同步 API 会阻塞调用方所在的事件循环，
    在 asyncio 里调用会抛 ``EventLoopBlocked``，进而把服务端启动拖超时。

    zeroconf 会自己监听 mDNS 端口，在 macOS 上等于再起一个响应程序，所以 macOS
    上不用它，见 ``mdns_default`` 和 ``BonjourService``。
    """

    def __init__(
        self,
        port: int,
        name: str = "Whiteboard",
        service_type: str = "_http._tcp",
        properties: Optional[Dict[str, str]] = None,
    ):
        self.port = port
        self.name = name
        self.service_type = service_type
        self.properties = properties if properties is not None else {"path": "/"}
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
                f"{self.service_type}.local.",
                f"{self.name}.{self.service_type}.local.",
                addresses=[socket.inet_aton(ip)],
                port=self.port,
                properties=self.properties,
            )
            await asyncio.wait_for(
                self._azc.async_register_service(self._info), timeout=REGISTER_TIMEOUT
            )
        except Exception as exc:  # noqa: BLE001 - 广播失败不该影响白板本身
            log.warning("mDNS 广播失败（不影响使用）：%s", exc)
            await self.stop()
            return False
        log.info("mDNS 已广播 %s %s:%s", self.service_type, local_hostname(), self.port)
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


# ------------------------------------------------------------ iPad 外壳的发现

BONJOUR_TYPE = "_whiteboard._tcp"


def bonjour_default() -> bool:
    """``_whiteboard._tcp`` 默认只在 macOS 上注册：外壳要找的就是 Mac。"""
    return sys.platform == "darwin"


def computer_name() -> str:
    """显示给用户的电脑名称（系统设置 → 通用 → 关于本机 → 名称）。"""
    if sys.platform == "darwin":
        import subprocess

        try:
            name = subprocess.run(
                ["scutil", "--get", "ComputerName"], capture_output=True, text=True, timeout=2
            ).stdout.strip()
            if name:
                return name
        except (OSError, subprocess.SubprocessError):
            pass
    return local_hostname()[: -len(".local")]


def txt_record(fields: Dict[str, str]) -> bytes:
    """DNS-SD 的 TXT 记录：每一项是一个字节的长度加 ``key=value``，单项最长 255 字节。"""
    out = bytearray()
    for key, value in fields.items():
        item = f"{key}={value}".encode("utf-8")[:255]
        out.append(len(item))
        out.extend(item)
    return bytes(out)


# 白板自己的服务名和入口；其他服务在 advertise 里给自己的
DEFAULT_SOURCE = "whiteboard"
DEFAULT_PATH = "/?role=ipad"


def bonjour_fields(
    port: int,
    version: str,
    source: str = DEFAULT_SOURCE,
    path: str = DEFAULT_PATH,
    name: Optional[str] = None,
) -> Dict[str, str]:
    """TXT 记录的字段。外壳直接用 host 和 port 拼地址，不再另做解析；``source`` 是
    外壳按名字选择服务时用的名字，``path`` 是连上之后打开的页面。"""
    return {
        "host": local_hostname(),
        "port": str(port),
        "version": version,
        "name": name or computer_name(),
        "source": source,
        "path": path,
    }


class _DNSSD:
    """通过 ctypes 调 libSystem 里的 ``DNSServiceRegister``。

    注册请求直接交给系统的 mDNSResponder，不需要当前线程跑 run loop，所以无窗口
    模式（``--headless``，主线程只是在 sleep）下同样生效。回调传 NULL：结果不用
    回读，注册在 ``DNSServiceRefDeallocate`` 之前一直有效。
    """

    def __init__(self):
        import ctypes
        import ctypes.util

        path = ctypes.util.find_library("System") or "/usr/lib/libSystem.B.dylib"
        lib = ctypes.CDLL(path)
        self._ctypes = ctypes
        self._register = lib.DNSServiceRegister
        self._register.restype = ctypes.c_int32
        self._register.argtypes = [
            ctypes.POINTER(ctypes.c_void_p),  # DNSServiceRef *sdRef
            ctypes.c_uint32,  # flags
            ctypes.c_uint32,  # interfaceIndex，0 表示所有接口
            ctypes.c_char_p,  # name
            ctypes.c_char_p,  # regtype
            ctypes.c_char_p,  # domain，NULL 表示默认（local.）
            ctypes.c_char_p,  # host，NULL 表示本机
            ctypes.c_uint16,  # port，网络字节序
            ctypes.c_uint16,  # txtLen
            ctypes.c_void_p,  # txtRecord
            ctypes.c_void_p,  # callBack
            ctypes.c_void_p,  # context
        ]
        self._deallocate = lib.DNSServiceRefDeallocate
        self._deallocate.restype = None
        self._deallocate.argtypes = [ctypes.c_void_p]

    def register(self, name: str, regtype: str, port: int, txt: bytes):
        ctypes = self._ctypes
        ref = ctypes.c_void_p()
        buffer = ctypes.create_string_buffer(txt, len(txt))
        error = self._register(
            ctypes.byref(ref), 0, 0, name.encode("utf-8"), regtype.encode("ascii"),
            None, None, socket.htons(port), len(txt), ctypes.cast(buffer, ctypes.c_void_p),
            None, None,
        )
        if error != 0:
            raise OSError(f"DNSServiceRegister 返回 {error}")
        return ref

    def deallocate(self, ref) -> None:
        self._deallocate(ref)


class BonjourService:
    """注册 ``_whiteboard._tcp``，iPad 外壳靠它找到这个服务。

    macOS 上必须通过系统的 mDNSResponder 注册（``DNSServiceRegister``），不能再起
    第二个 mDNS 响应程序；其他平台用 zeroconf。和 ``MDNSAdvertiser`` 一样，注册
    失败只写日志，不影响服务启动。
    """

    def __init__(
        self,
        port: int,
        version: str,
        source: str = DEFAULT_SOURCE,
        path: str = DEFAULT_PATH,
        name: Optional[str] = None,
    ):
        self.port = port
        self.version = version
        self.source = source
        self.path = path
        self.name = name
        self.fields: Dict[str, str] = {}
        self._dnssd: Optional[_DNSSD] = None
        self._ref = None
        self._zeroconf: Optional[MDNSAdvertiser] = None

    async def start(self) -> bool:
        try:
            self.fields = await asyncio.to_thread(
                bonjour_fields, self.port, self.version, self.source, self.path, self.name
            )
            if sys.platform == "darwin":
                await asyncio.wait_for(
                    asyncio.to_thread(self._register_system), timeout=REGISTER_TIMEOUT
                )
            else:
                self._zeroconf = MDNSAdvertiser(
                    self.port, self.fields["name"], BONJOUR_TYPE, dict(self.fields)
                )
                if not await self._zeroconf.start():
                    self._zeroconf = None
                    return False
        except Exception as exc:  # noqa: BLE001 - 注册失败不该影响服务本身
            log.warning("Bonjour 注册 %s 失败（外壳可以改用安装页连接）：%s", BONJOUR_TYPE, exc)
            await self.stop()
            return False
        log.info("Bonjour 已注册 %s：%s", BONJOUR_TYPE, self.fields)
        return True

    def _register_system(self) -> None:
        self._dnssd = _DNSSD()
        self._ref = self._dnssd.register(
            self.fields["name"], BONJOUR_TYPE, self.port, txt_record(self.fields)
        )

    async def stop(self) -> None:
        ref, dnssd = self._ref, self._dnssd
        self._ref = None
        if ref is not None and dnssd is not None:
            try:
                dnssd.deallocate(ref)
            except Exception as exc:  # noqa: BLE001
                log.debug("Bonjour 注销失败：%s", exc)
        zc = self._zeroconf
        self._zeroconf = None
        if zc is not None:
            await zc.stop()


SOURCE_RE = re.compile(r"^[a-z0-9-]{1,32}$")


def advertise(app, port: int, source: str, path: str = "/", name: Optional[str] = None, version: str = "") -> None:
    """让 iPad 外壳能按名字找到这个服务：在 ``app`` 启动时注册、关闭时注销。

    ``source`` 是服务名（外壳「设置」里填 ``@<source>``），``path`` 是外壳连上之后打开
    的页面，``port`` 是实际监听的端口。注册失败只写日志，不影响服务启动。
    """
    if not SOURCE_RE.match(source):
        raise ValueError(f"服务名只能由小写字母、数字和 - 组成：{source!r}")
    if not path.startswith("/"):
        raise ValueError(f"入口路径必须以 / 开头：{path!r}")
    service = BonjourService(port, version, source=source, path=path, name=name)

    async def _on_startup(_app) -> None:
        await service.start()

    async def _on_cleanup(_app) -> None:
        await service.stop()

    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
