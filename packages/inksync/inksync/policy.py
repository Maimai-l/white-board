"""身份与规则：谁可以打开、新建、书写、清空哪块白板。

``authenticate(request) -> Principal`` 在连接建立时调用一次；``Policy`` 的方法在每次
动作时调用，不缓存。使用者继承 :class:`DefaultPolicy`，覆盖需要的方法。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, FrozenSet, Mapping, Optional, Tuple

from . import models, netinfo


@dataclass(frozen=True)
class Principal:
    """一条连接的身份。``id`` 由使用者定义（例如用户 id），None 表示匿名。"""

    id: Optional[str]
    local: bool
    address: str = ""
    attrs: Mapping[str, Any] = field(default_factory=dict)


def local_principal(request) -> Principal:
    """默认的身份：只判断是否来自本机。"""
    return Principal(
        id=None,
        local=netinfo.is_local_request(request),
        address=getattr(request, "remote", None) or "",
    )


class Policy:
    """规则的接口。每个方法都有默认实现，见 :class:`DefaultPolicy`。"""

    protected_data_keys: FrozenSet[str] = frozenset()

    def can_open(self, who: Principal, board_id: str, meta: Optional[Dict[str, Any]]) -> bool:
        return True

    def can_create(self, who: Principal, board_id: str, spec: Dict[str, Any]) -> bool:
        return True

    def can_write(self, who: Principal, meta: Dict[str, Any]) -> bool:
        return True

    def can_clear(self, who: Principal, meta: Dict[str, Any]) -> bool:
        return True

    def can_edit_meta(self, who: Principal, meta: Dict[str, Any], patch: Dict[str, Any]) -> bool:
        return who.local

    def can_unlock(self, who: Principal, meta: Dict[str, Any]) -> bool:
        return who.local

    def create_limit(self, who: Principal) -> Optional[Tuple[int, float]]:
        return None if who.local else (60, 60.0)

    def validate_data(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return data

    def allow_src(self, src: str) -> bool:
        return models.default_allow_src(src)

    def caps(self, who: Principal, meta: Dict[str, Any], readonly: bool = False) -> Dict[str, bool]:
        write = not readonly and self.can_write(who, meta)
        return {
            "write": write,
            "clear": write and self.can_clear(who, meta),
            "meta": not readonly and self.can_edit_meta(who, meta, {}),
            "unlock": self.can_unlock(who, meta),
        }


class DefaultPolicy(Policy):
    """默认规则：任何设备都能打开、新建（本机以外有速率限制）、书写和清空；
    只有本机能修改元数据和解除只读。"""


class RateLimiter:
    """按身份计数的新建速率限制（滑动窗口）。"""

    def __init__(self) -> None:
        self._events: Dict[str, Deque[float]] = {}

    def allow(self, key: str, limit: Optional[Tuple[int, float]]) -> bool:
        if limit is None:
            return True
        count, window = limit
        stamp = time.monotonic()
        events = self._events.setdefault(key, deque())
        while events and stamp - events[0] > window:
            events.popleft()
        if len(events) >= count:
            return False
        events.append(stamp)
        if len(self._events) > 10000:  # 防止无数个匿名地址把字典撑大
            for stale in [k for k, v in self._events.items() if not v or stamp - v[-1] > window]:
                self._events.pop(stale, None)
        return True

    @staticmethod
    def key(who: Principal) -> str:
        return f"id:{who.id}" if who.id is not None else f"addr:{who.address}"
