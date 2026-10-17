"""请求来源检查：挡住浏览器里其他网页发来的请求。

本机来的连接拥有全部权限（:func:`inksync.netinfo.is_local_request`），而浏览器里打开的
任何网页都能向 ``127.0.0.1`` 发 WebSocket 握手和 POST。只看对端地址分不出这是白板自己
的页面还是别的网站，所以每个请求再查两项：

* ``Host``：必须是 IP 地址、``localhost``、``*.local`` 或本机的主机名（另可用
  ``allowed_hosts`` 添加）。挡的是 DNS 重绑定：恶意域名解析到 127.0.0.1 之后，它的页面与本服务「同源」，
  只有 ``Host`` 还带着那个域名。
* ``Origin``：有这个头时，必须与本服务同源，或者列在 ``allowed_origins`` 中。浏览器在
  WebSocket 握手和跨站 POST 中总会带上它；``null``（沙盒 iframe、本地文件）一律拒绝。
  没有这个头的请求不是网页发的（脚本、命令行工具），照常处理。
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Any, Iterable, Optional, Tuple, Union
from urllib.parse import urlsplit

from aiohttp import web

log = logging.getLogger(__name__)

AllowedHosts = Union[None, str, Iterable[str]]

# 默认端口按「没写端口」处理：经 https 反向代理时 Origin 是 443，Host 里通常不写端口
_DEFAULT_PORTS = (None, 80, 443)


def _split(netloc: str) -> Tuple[Optional[str], Optional[int]]:
    """``host[:port]`` 拆成小写主机名和端口；拆不开时主机名为 None。"""
    try:
        parts = urlsplit("//" + netloc)
        port = parts.port
    except ValueError:
        return None, None
    host = (parts.hostname or "").lower() or None
    return host, (None if port in _DEFAULT_PORTS else port)


def normalize_origin(origin: str) -> Optional[str]:
    """``scheme://host[:port]`` 的规范写法（小写、去掉默认端口）；不是这种形式时返回 None。"""
    try:
        parts = urlsplit(origin.strip())
        port = parts.port
    except ValueError:
        return None
    if not parts.scheme or not parts.hostname or parts.path not in ("", "/"):
        return None
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default = {"http": 80, "https": 443, "ws": 80, "wss": 443}.get(parts.scheme.lower())
    suffix = f":{port}" if port is not None and port != default else ""
    return f"{parts.scheme.lower()}://{host}{suffix}"


def own_hostnames() -> frozenset:
    """本机的主机名（完整名和第一段，小写）：用 ``http://macbook:8848`` 打开也要能用。"""
    name = socket.gethostname().lower().rstrip(".")
    return frozenset(item for item in (name, name.split(".")[0]) if item)


def host_allowed(host: Optional[str], allowed_hosts: AllowedHosts = None) -> bool:
    """``Host`` 头里的主机名是否可以接受（端口不看）。"""
    if allowed_hosts == "*":
        return True
    name, _port = _split(host or "")
    if name is None:
        return False
    try:
        ipaddress.ip_address(name.split("%")[0])
        return True
    except ValueError:
        pass
    if name == "localhost" or name.endswith(".local") or name in own_hostnames():
        return True
    extra = [allowed_hosts] if isinstance(allowed_hosts, str) else list(allowed_hosts or ())
    return name in {item.lower() for item in extra}


def origin_allowed(origin: Optional[str], host: Optional[str], allowed_origins: Iterable[str] = ()) -> bool:
    """``Origin`` 头是否可以接受：没有、与 ``host`` 同源，或在 ``allowed_origins`` 中。"""
    if origin is None:
        return True
    if origin.strip().lower() == "null":
        return False
    normalized = normalize_origin(origin)
    if normalized is None:
        return False
    if normalized in {normalize_origin(item) for item in allowed_origins}:
        return True
    parts = urlsplit(normalized)
    origin_host, origin_port = _split(parts.netloc)
    request_host, request_port = _split(host or "")
    return origin_host is not None and origin_host == request_host and origin_port == request_port


def check(request: Any, allowed_origins: Iterable[str] = (), allowed_hosts: AllowedHosts = None) -> Optional[str]:
    """检查一个请求；可以接受时返回 None，否则返回拒绝的原因。"""
    host = request.headers.get("Host")
    if host is not None and not host_allowed(host, allowed_hosts):
        return f"Host 不被接受：{host}"
    origin = request.headers.get("Origin")
    if not origin_allowed(origin, host or request.host, allowed_origins):
        return f"来源不被接受：{origin}"
    return None


def reject(request: Any, reason: str) -> web.Response:
    log.warning("拒绝 %s %s（%s，对端 %s）", request.method, request.path, reason, request.remote)
    return web.Response(status=403, text="请求来源不被接受")


def guard_middleware(allowed_origins: Iterable[str] = (), allowed_hosts: AllowedHosts = None):
    """aiohttp 中间件：对所有路由做同样的检查。``allowed_origins`` 可以之后再改（传入列表或集合）。"""

    @web.middleware
    async def guard(request: web.Request, handler):
        reason = check(request, allowed_origins, allowed_hosts)
        if reason:
            return reject(request, reason)
        return await handler(request)

    return guard


__all__ = ["check", "guard_middleware", "host_allowed", "normalize_origin", "origin_allowed"]
