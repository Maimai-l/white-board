#!/usr/bin/env python3
"""白板启动入口。

    python run.py                # 打开 Mac 窗口（pywebview）并启动局域网服务
    python run.py --headless     # 只跑服务端，用浏览器访问
    python run.py --port 9000 --data-dir ~/Documents/白板
"""

from __future__ import annotations

import argparse
import sys

from whiteboard import __version__, resources
from whiteboard.config import Config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="局域网共享白板")
    parser.add_argument("--port", type=int, help="监听端口（默认 8848，占用时自动顺延）")
    parser.add_argument("--data-dir", help="白板存储目录")
    parser.add_argument("--headless", action="store_true", help="不开窗口，只跑服务端")
    parser.add_argument("--mdns", action="store_true", help="强制广播 mDNS 服务（macOS 默认交给系统）")
    parser.add_argument("--no-mdns", action="store_true", help="不广播 mDNS 服务")
    parser.add_argument("--debug", action="store_true", help="打开调试日志与开发者工具")
    parser.add_argument("--version", action="version", version=f"白板 {__version__}")

    # macOS 双击 .app 时系统会塞进来 -psn_0_xxx 之类的参数，别让 argparse 报错
    raw = list(sys.argv[1:] if argv is None else argv)
    raw = [item for item in raw if not item.startswith("-psn_")]
    args, unknown = parser.parse_known_args(raw)
    if unknown:
        print(f"忽略无法识别的参数：{' '.join(unknown)}", file=sys.stderr)

    log_file = resources.setup_logging(args.debug)
    if log_file:
        print(f"日志写在 {log_file}")

    config = Config()
    if args.port:
        config.port = args.port
    if args.data_dir:
        config.data_dir = args.data_dir
    config.save()

    from whiteboard.netinfo import mdns_default

    advertise = mdns_default()
    if args.mdns:
        advertise = True
    if args.no_mdns:
        advertise = False

    if args.headless:
        from whiteboard.netinfo import candidate_urls
        from whiteboard.runner import ServerThread

        server = ServerThread(config, advertise=advertise)
        server.start()
        print("白板服务已启动：")
        for url in candidate_urls(server.port):
            print(f"  {url}")
        print(f"  描述文件：http://{candidate_urls(server.port)[0].split('//')[1]}profile.mobileconfig")
        print("按 Ctrl+C 退出。")
        try:
            import time

            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n正在保存白板…")
        finally:
            server.save_now()
            server.stop()
        return 0

    try:
        from whiteboard.app import run as run_app
    except ImportError as exc:
        print(f"缺少 pywebview（{exc}）。可以先用 python run.py --headless 跑服务端。", file=sys.stderr)
        return 1
    run_app(config, debug=args.debug, advertise=advertise)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
