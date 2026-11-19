#!/bin/bash
# 给应用签名，由内向外（docs/design/ci-and-updates.zh-CN.md 第 3.6 节）。
#
#   sign_app.sh dist/Whiteboard.app            ad hoc（默认）
#   MAC_SIGN_IDENTITY=<证书 SHA-1> sign_app.sh dist/Whiteboard.app
#
# 1. --deep 签一遍应用里所有的代码（PyInstaller 收进来的 dylib 和 so）；
# 2. 再按 Sparkle 的要求由内向外签 Autoupdate、Updater.app、Sparkle.framework；
# 3. 最后签应用本身，让外层的封印包含前面签好的内容。
# 每一步都保留原有的 identifier、entitlements 和 flags。
set -euo pipefail

app="$1"
identity="${MAC_SIGN_IDENTITY:--}"
preserve=(--preserve-metadata=identifier,entitlements,flags)

codesign --force --deep "${preserve[@]}" --sign "$identity" "$app"

framework="$app/Contents/Frameworks/Sparkle.framework"
if [ -d "$framework" ]; then
  codesign --force "${preserve[@]}" --sign "$identity" "$framework/Versions/B/Autoupdate"
  codesign --force "${preserve[@]}" --sign "$identity" "$framework/Versions/B/Updater.app"
  codesign --force "${preserve[@]}" --sign "$identity" "$framework"
fi
codesign --force "${preserve[@]}" --sign "$identity" "$app"

# Python 运行时里有符号链接，--strict 会拒绝；和 Sparkle 更新时的检查一样，不加 --strict
codesign --verify --deep --verbose=2 "$app"
# ad hoc 签名打印的是「# designated => cdhash H"…"」，证书签名打印「designated => identifier …」
echo "designated requirement："
codesign -d -r- "$app" 2>&1 | grep 'designated =>' | sed 's/^/  /'
