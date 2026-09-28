"""换版本之前备份白板。

新版本第一次启动时（以及降级回旧版本时），在它碰任何白板文件之前，先把
``boards/`` 和 ``index.json`` 原样复制一份到 ``backups/upgrade/``。新版本里要是有
没发现的问题把白板写坏了，还能从这里找回来。

``docs/``（文档板的原件）不备份：程序从不改写它们，而且可能很大。
缩略图也不备份，丢了会重新生成。只保留最近几份。
"""

from __future__ import annotations

import logging
import re
import shutil
import time
from pathlib import Path
from typing import Optional

from .config import Config

log = logging.getLogger(__name__)

KEEP = 5


def backup_if_upgraded(config: Config, version: str) -> Optional[Path]:
    """版本和上次运行时不一样就备份一次，返回备份目录；不需要备份时返回 None。

    备份失败只记日志，不挡住启动；但这种情况下不记下新版本号，下次启动再试。
    """
    previous = config.last_version
    if previous == version:
        return None
    data_dir = config.data_dir
    boards = data_dir / "boards"
    target: Optional[Path] = None
    if boards.is_dir() and any(boards.iterdir()):
        try:
            target = _copy(data_dir, previous or "unknown", version)
        except OSError as exc:
            log.error("换版本前备份白板失败：%s", exc)
            return None
        log.info("版本 %s → %s，白板已备份到 %s", previous or "未知", version, target)
        _prune(target.parent)
    config.last_version = version
    config.save()
    return target


def _copy(data_dir: Path, previous: str, version: str) -> Path:
    root = data_dir / "backups" / "upgrade"
    # 目录名开头是时间，按名字排序就是按时间排序（清理旧备份靠这一点），
    # 所以精确到纳秒：同一秒里的几份也分得出先后
    now = time.time_ns()
    stamp = f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(now / 1e9))}.{now % 10**9:09d}"
    name = f"{stamp}_{_safe(previous)}_to_{_safe(version)}"
    target = root / name
    counter = 1
    while target.exists():
        target = root / f"{name}-{counter}"
        counter += 1
    tmp = root / f".partial-{target.name}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        shutil.copytree(data_dir / "boards", tmp / "boards")
        index = data_dir / "index.json"
        if index.exists():
            shutil.copy2(index, tmp / "index.json")
        # 整份复制完才改成正式的名字，半截的备份不会被当成可用的
        tmp.rename(target)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return target


def _prune(root: Path) -> None:
    done = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    for old in done[:-KEEP]:
        shutil.rmtree(old, ignore_errors=True)


def _safe(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]", "_", text)[:32] or "unknown"
