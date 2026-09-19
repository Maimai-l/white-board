#!/bin/bash
# 不开程序窗口，只在局域网上跑服务，用 Safari / Chrome 访问。
cd "$(dirname "$0")" || exit 1
exec "./启动白板.command" --headless
