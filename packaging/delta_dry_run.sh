#!/bin/bash
# 发布演练（docs/design/ci-and-updates.zh-CN.md 第 3.4 节）：不发布任何东西，
# 用一对临时密钥把这次打出来的包当「旧版本」、改一处后的副本当「新版本」，
# 跑一遍 generate_appcast，核对签名，再把增量包应用到旧版本上，确认和新版本一样。
#
#   delta_dry_run.sh <应用 zip> <Sparkle 目录> <工作目录>
set -euo pipefail

zip="$1"
sparkle="$2"
work="$3"
here="$(cd "$(dirname "$0")" && pwd)"

rm -rf "$work"
mkdir -p "$work/old" "$work/new" "$work/feed" "$work/applied"
ditto -x -k "$zip" "$work/old"
ditto "$work/old/Whiteboard.app" "$work/new/Whiteboard.app"

plist="$work/new/Whiteboard.app/Contents/Info.plist"
old_build="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$plist")"
new_build="${old_build}9"   # 最后一段多一位数字：一定比旧的大
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $new_build" "$plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString dry-run-$new_build" "$plist"
date -u > "$work/new/Whiteboard.app/Contents/Resources/dry-run.txt"
"$here/sign_app.sh" "$work/new/Whiteboard.app" > /dev/null

ditto -c -k --keepParent "$work/old/Whiteboard.app" "$work/feed/Whiteboard-old.zip"
ditto -c -k --keepParent "$work/new/Whiteboard.app" "$work/feed/Whiteboard-new.zip"

python3 "$here/appcast.py" keygen "$work/keys"
"$sparkle/bin/generate_appcast" --ed-key-file - --maximum-deltas 3 \
  --download-url-prefix "https://example.invalid/" "$work/feed" < "$work/keys/private.key"
rm -f "$work/keys/private.key"
python3 "$here/appcast.py" verify "$work/feed" "$work/keys/public.key"

delta="$(ls "$work/feed"/*.delta | head -n 1)"
ls -l "$work/feed"
"$sparkle/bin/BinaryDelta" apply "$work/old/Whiteboard.app" "$work/applied/Whiteboard.app" "$delta"
if ! diff -r "$work/new/Whiteboard.app" "$work/applied/Whiteboard.app" > "$work/diff.txt"; then
  echo "::error::增量包应用之后和新版本不一样"
  head -n 40 "$work/diff.txt"
  exit 1
fi
full=$(stat -f %z "$work/feed/Whiteboard-new.zip")
part=$(stat -f %z "$delta")
echo "增量包可用：$((part / 1024)) KB，完整包 $((full / 1024 / 1024)) MB"
