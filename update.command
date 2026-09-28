#!/bin/bash
# 双击更新到最新版本（git pull），依赖有变化的话下次启动会自动补装。
cd "$(dirname "$0")" || exit 1

if [ ! -d .git ]; then
  echo "这个目录不是 git 仓库，没法自动更新。"
  echo "可以用下面这行把仓库克隆下来，之后双击 update.command 就能更新："
  echo "  git clone https://github.com/Maimai-l/white-board.git"
  echo
  read -r -p "按回车关闭窗口…"
  exit 1
fi

echo "当前版本：$(git rev-parse --short HEAD)"
if ! git pull --ff-only; then
  echo
  echo "更新失败。若本地改过代码，可以先执行 git stash 再重试。"
  read -r -p "按回车关闭窗口…"
  exit 1
fi
echo "已更新到：$(git rev-parse --short HEAD)"
echo "双击 start-whiteboard.command 即可启动（依赖有变化会自动补装）。"
echo
read -r -p "按回车关闭窗口…"
