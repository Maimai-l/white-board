"""换版本之前备份白板。

新版本第一次启动时（以及降级回旧版本时），在它碰任何白板文件之前，先把
``boards/``、索引（1.x 的 ``index.json``，2.0 的 ``index.sqlite``）、``space.json``
和各应用空间 ``spaces/`` 原样复制一份到 ``backups/upgrade/``。新版本里要是有
没发现的问题把白板写坏了，还能从这里找回来。

1.x 的存储目录第一次由 2.0 打开时要转换格式（docs/design/inksync-2.zh-CN.md 4.6 节），
这时备份失败就不启动（:class:`BackupError`），不做任何转换。

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
# 除 boards/ 以外要备份的文件
FILES = ("index.json", "index.sqlite", "index.sqlite-wal", "space.json")


class BackupError(RuntimeError):
    """转换格式之前的备份没做成：不能启动。"""


def needs_conversion(data_dir: Path) -> bool:
    """1.x 的存储目录：有 index.json，还没有 2.0 的 index.sqlite。"""
    return (data_dir / "index.json").exists() and not (data_dir / "index.sqlite").exists()


def backup_if_upgraded(config: Config, version: str) -> Optional[Path]:
    """版本和上次运行时不一样就备份一次，返回备份目录；不需要备份时返回 None。

    备份失败时不记下新版本号，下次启动再试；存储目录还需要从 1.x 转换时抛
    :class:`BackupError`，调用方不能继续启动。其余情况只记日志。
    """
    data_dir = config.data_dir
    convert = needs_conversion(data_dir)
    previous = config.last_version
    if previous == version and not convert:
        return None
    boards = data_dir / "boards"
    target: Optional[Path] = None
    if boards.is_dir() and any(boards.iterdir()):
        try:
            target = _copy(data_dir, previous or "unknown", version)
        except OSError as exc:
            log.error("换版本前备份白板失败：%s", exc)
            if convert:
                raise BackupError(f"升级前备份白板失败（{exc.strerror or exc}），为保护白板没有启动。"
                                  f"请检查存储目录所在磁盘的剩余空间后重新打开。") from exc
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
        for name in FILES:
            if (data_dir / name).exists():
                shutil.copy2(data_dir / name, tmp / name)
        spaces = data_dir / "spaces"
        if spaces.is_dir():
            shutil.copytree(spaces, tmp / "spaces")
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
