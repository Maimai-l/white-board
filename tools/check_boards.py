#!/usr/bin/env python3
"""用当前代码把存储目录里的每块白板读一遍，报告读得全不全。**不改动原目录。**

新版本把「没能完整读出来」的白板锁成只读。升级之前先在自己的数据上跑一遍，
确认不会有白板被意外锁住：

    python tools/check_boards.py                       # 默认读配置里的存储目录
    python tools/check_boards.py ~/Documents/白板       # 指定目录

整个目录先复制到临时目录再读（打开存储时可能会重建索引），原目录一个字节都不碰。
退出码：0 = 全部完整；1 = 有白板会以只读方式打开；2 = 目录不存在。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from whiteboard.config import Config  # noqa: E402
from whiteboard.store import BoardStore  # noqa: E402

REASONS = {
    "unreadable": "文件打不开",
    "corrupt": "文件损坏，一笔都读不出来",
    "partial": "部分笔画读不出来",
    "newer": "由更新的版本写入",
}


def main(argv: list[str]) -> int:
    source = Path(argv[1]).expanduser() if len(argv) > 1 else Config().data_dir
    if not (source / "boards").is_dir():
        print(f"不是白板存储目录：{source}")
        return 2
    count = len(list((source / "boards").glob("*.wbz")))
    print(f"存储目录：{source}（{count} 个白板文件）")

    locked = 0
    with tempfile.TemporaryDirectory(prefix="whiteboard-check-") as tmp:
        copy = Path(tmp) / "data"
        # 只复制白板和索引，原件（docs/）可能很大，读白板用不到它们
        copy.mkdir()
        shutil.copytree(source / "boards", copy / "boards")
        if (source / "index.json").exists():
            shutil.copy2(source / "index.json", copy / "index.json")
        store = BoardStore(copy)
        for meta in store.list_metas():
            _, strokes, problem = store.open_board(meta["id"])
            name = meta.get("name") or "（未命名）"
            if problem is None:
                print(f"  完整  {meta['id']}  {name}  {len(strokes)} 笔")
                continue
            locked += 1
            detail = REASONS.get(problem["reason"], problem["reason"])
            extra = problem.get("detail") or problem.get("dropped") or problem.get("version") or ""
            print(f"  只读  {meta['id']}  {name}  {len(strokes)} 笔  —— {detail} {extra}")

    if locked:
        print(f"\n{locked} 块白板会以只读方式打开。请把上面的输出发给开发者。")
        return 1
    print("\n全部白板都能完整读出。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
