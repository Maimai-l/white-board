#!/bin/bash
# 双击运行：切到某个开发分支再启动，用来试还没合进主线的改动。
#
# 不带参数就列出远端的 claude/* 分支让你挑；带参数就直接用那个分支名。
# 回主线：./beta.command main
cd "$(dirname "$0")" || exit 1

if [ ! -d .git ]; then
  echo "这个目录不是 git 仓库，没法切分支。"
  read -r -p "按回车关闭窗口…"
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "本地有没提交的改动，先处理掉再切分支："
  git status --short
  echo
  echo "想丢掉这些改动就执行：git checkout -- ."
  read -r -p "按回车关闭窗口…"
  exit 1
fi

echo "正在取远端分支…"
git fetch --prune origin || { echo "取不到远端。"; read -r -p "按回车关闭窗口…"; exit 1; }

BRANCH="$1"
if [ -z "$BRANCH" ]; then
  # 列出远端的开发分支，按最近提交排序，最新的在最上面。
  # 不用 mapfile：macOS 自带的还是 bash 3.2，没有这个内建命令。
  BRANCHES=()
  while IFS= read -r line; do
    [ -n "$line" ] && BRANCHES+=("$line")
  done <<< "$(git for-each-ref --sort=-committerdate --format='%(refname:short)' \
    'refs/remotes/origin/claude/*' | sed 's|^origin/||')"
  if [ ${#BRANCHES[@]} -eq 0 ]; then
    echo "远端上没有 claude/* 分支。"
    read -r -p "按回车关闭窗口…"
    exit 1
  fi
  echo
  echo "远端上的开发分支（最近提交的排在最前）："
  for i in "${!BRANCHES[@]}"; do
    ref="origin/${BRANCHES[$i]}"
    printf "  %d) %-40s %s\n" "$((i + 1))" "${BRANCHES[$i]}" \
      "$(git log -1 --format='%cr  %s' "$ref" | cut -c1-60)"
  done
  echo "  0) main（回主线）"
  echo
  read -r -p "要跑哪一个？输入序号： " PICK
  if [ "$PICK" = "0" ]; then
    BRANCH="main"
  elif [[ "$PICK" =~ ^[0-9]+$ ]] && [ "$PICK" -ge 1 ] && [ "$PICK" -le ${#BRANCHES[@]} ]; then
    BRANCH="${BRANCHES[$((PICK - 1))]}"
  else
    echo "没听懂「$PICK」。"
    read -r -p "按回车关闭窗口…"
    exit 1
  fi
fi

echo
echo "切到 $BRANCH …"
if ! git checkout -q "$BRANCH" 2>/dev/null; then
  git checkout -q -b "$BRANCH" "origin/$BRANCH" || {
    echo "切不过去：远端没有 $BRANCH。"
    read -r -p "按回车关闭窗口…"
    exit 1
  }
fi
git pull -q --ff-only origin "$BRANCH" || echo "（拉取失败，先用本地这一份跑）"
echo "现在是 $(git rev-parse --abbrev-ref HEAD) $(git rev-parse --short HEAD)"
echo "  $(git log -1 --format='%s')"
echo

exec ./start-whiteboard.command
