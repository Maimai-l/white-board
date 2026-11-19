#!/bin/bash
# 下载固定版本的 Sparkle，校验 SHA-256 后解到目标目录（默认 build/sparkle）。
# 升级 Sparkle：改 VERSION 和 SHA256 两行，一起提交。
set -euo pipefail

VERSION=2.10.1
SHA256=1a856d2a9edb8e628de88121d7be04cf0e2167f8641650b78fca00b14244cf27
DEST="${1:-build/sparkle}"

mkdir -p "$DEST"
archive="$DEST/Sparkle-$VERSION.tar.xz"
if [ ! -f "$archive" ]; then
  curl -fsSL --retry 3 -o "$archive" \
    "https://github.com/sparkle-project/Sparkle/releases/download/$VERSION/Sparkle-$VERSION.tar.xz"
fi
echo "$SHA256  $archive" | shasum -a 256 -c -
tar -xJf "$archive" -C "$DEST"
echo "Sparkle $VERSION 在 $DEST"
