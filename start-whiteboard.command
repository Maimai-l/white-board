#!/bin/bash
# 双击运行：首次会自动建好运行环境并安装依赖，之后直接启动白板。
cd "$(dirname "$0")" || exit 1

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "没有找到 python3。请先从 https://www.python.org/downloads/ 安装 Python 3.10 以上版本。"
  echo
  read -r -p "按回车关闭窗口…"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "首次启动：正在创建运行环境（只需要这一次）…"
  "$PY" -m venv .venv || { echo "创建运行环境失败。"; read -r -p "按回车关闭窗口…"; exit 1; }
fi

# shellcheck disable=SC1091
source .venv/bin/activate

STAMP=".venv/.deps-$(shasum requirements.txt | awk '{print $1}')"
if [ ! -f "$STAMP" ]; then
  echo "正在安装依赖…"
  python -m pip install --quiet --upgrade pip
  if python -m pip install --quiet -r requirements.txt; then
    touch "$STAMP"
  else
    echo "依赖安装失败，请检查网络后重试。"
    read -r -p "按回车关闭窗口…"
    exit 1
  fi
fi

echo "正在启动白板…（关掉窗口即退出程序）"
python run.py "$@"
status=$?
if [ $status -ne 0 ]; then
  echo
  echo "白板异常退出（代码 $status）。上面的信息可以直接反馈。"
  read -r -p "按回车关闭窗口…"
fi
