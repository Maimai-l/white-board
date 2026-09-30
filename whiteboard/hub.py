"""白板应用的同步中心：在 :class:`inksync.hub.Hub` 上加文档板的建立。"""

from __future__ import annotations

from typing import Any, Dict

from inksync.hub import (  # noqa: F401 - 白板应用和测试沿用这些名字
    AUTOSAVE_INTERVAL,
    OPS_HISTORY,
    BoardRuntime,
    Client,
)
from inksync.hub import Hub as CoreHub


class Hub(CoreHub):
    def import_doc(self, data: bytes, filename: str) -> Dict[str, Any]:
        """由一份 PDF / 图片新建文档板，并切到它上面。"""
        return self.add_doc(self.store.prepare_doc(data, filename))

    def add_doc(self, prepared: Dict[str, Any]) -> Dict[str, Any]:
        """为 ``store.prepare_doc`` 准备好的原件建板并切过去。

        和其余 Hub 方法一样只能在事件循环线程上调用：它要改索引和内存里的白板。
        """
        self.save_all()
        meta = self.store.register_doc(prepared)
        self._boards[meta["id"]] = BoardRuntime(meta, [])
        self.current_id = meta["id"]
        return meta
