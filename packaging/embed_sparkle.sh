#!/bin/bash
# 把 Sparkle.framework 放进应用的 Contents/Frameworks。
# 应用不是沙盒应用，Sparkle 的 XPC 服务用不上（只有 Info.plist 里开了
# SUEnableInstallerLauncherService / SUEnableDownloaderService 才会用），删掉；
# 删了之后 framework 的签名失效，由 sign_app.sh 重新签。
set -euo pipefail

app="$1"
sparkle="${2:-build/sparkle}"
frameworks="$app/Contents/Frameworks"

mkdir -p "$frameworks"
rm -rf "$frameworks/Sparkle.framework"
ditto "$sparkle/Sparkle.framework" "$frameworks/Sparkle.framework"
rm -rf "$frameworks/Sparkle.framework/Versions/B/XPCServices" "$frameworks/Sparkle.framework/XPCServices"
echo "已放入 $frameworks/Sparkle.framework"
