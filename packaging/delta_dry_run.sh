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

# generate_appcast 只给 Info.plist 里带着匹配的 SUPublicEDKey 的包签名，也只从带公钥的
# 旧版本做增量包（旧版本没有公钥就验不了新版本）。分支上的构建没有正式公钥，
# 所以两个版本都写入这次的临时公钥，再重新签名。
python3 "$here/appcast.py" keygen "$work/keys"
public_key="$(cat "$work/keys/public.key")"
for side in old new; do
  plutil -replace SUPublicEDKey -string "$public_key" "$work/$side/Whiteboard.app/Contents/Info.plist"
  plutil -replace SUFeedURL -string "https://example.invalid/appcast.xml" "$work/$side/Whiteboard.app/Contents/Info.plist"
done

plist="$work/new/Whiteboard.app/Contents/Info.plist"
old_build="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$plist")"
new_build="${old_build}9"   # 最后一段多一位数字：一定比旧的大
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $new_build" "$plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString dry-run-$new_build" "$plist"
date -u > "$work/new/Whiteboard.app/Contents/Resources/dry-run.txt"
"$here/sign_app.sh" "$work/old/Whiteboard.app" > /dev/null
"$here/sign_app.sh" "$work/new/Whiteboard.app" > /dev/null

ditto -c -k --keepParent "$work/old/Whiteboard.app" "$work/feed/Whiteboard-old.zip"
ditto -c -k --keepParent "$work/new/Whiteboard.app" "$work/feed/Whiteboard-new.zip"
"$sparkle/bin/generate_appcast" --ed-key-file - --maximum-deltas 3 \
  --download-url-prefix "https://example.invalid/" "$work/feed" < "$work/keys/private.key"
rm -f "$work/keys/private.key"
echo "生成的 appcast："
cat "$work/feed/appcast.xml"
python3 "$here/appcast.py" verify "$work/feed" "$work/keys/public.key"

delta="$(ls "$work/feed"/*.delta | head -n 1)"
ls -l "$work/feed"
"$sparkle/bin/BinaryDelta" apply "$work/old/Whiteboard.app" "$work/applied/Whiteboard.app" "$delta"
# 不用 diff -r：macOS 26 的 diff 跳过包内的目录链接还返回 0（见 appcast.py treediff）
if ! python3 "$here/appcast.py" treediff "$work/new/Whiteboard.app" "$work/applied/Whiteboard.app"; then
  echo "::error::增量包应用之后和新版本不一样"
  exit 1
fi
full=$(stat -f %z "$work/feed/Whiteboard-new.zip")
part=$(stat -f %z "$delta")
echo "增量包可用：$((part / 1024)) KB，完整包 $((full / 1024 / 1024)) MB"
