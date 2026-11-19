#!/bin/bash
# 有自签名证书时（Secret MAC_CERT_P12 为 base64 的 .p12，MAC_CERT_PASSWORD 为密码）
# 导入一个临时钥匙串，把证书的 SHA-1 写进 $GITHUB_ENV 的 MAC_SIGN_IDENTITY。
# 没有证书就什么也不做，sign_app.sh 用 ad hoc 签名。私钥只在这个临时钥匙串里。
set -euo pipefail

if [ -z "${MAC_CERT_P12:-}" ]; then
  echo "没有 MAC_CERT_P12：用 ad hoc 签名"
  exit 0
fi

keychain="$RUNNER_TEMP/whiteboard-sign.keychain-db"
password="$(uuidgen)"
p12="$RUNNER_TEMP/whiteboard-sign.p12"
pem="$RUNNER_TEMP/whiteboard-sign.pem"

security create-keychain -p "$password" "$keychain"
security set-keychain-settings -lut 3600 "$keychain"
security unlock-keychain -p "$password" "$keychain"
printf '%s' "$MAC_CERT_P12" | base64 --decode > "$p12"
security import "$p12" -k "$keychain" -P "${MAC_CERT_PASSWORD:-}" -T /usr/bin/codesign
rm -f "$p12"
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$password" "$keychain" > /dev/null
# shellcheck disable=SC2046
security list-keychains -d user -s "$keychain" $(security list-keychains -d user | tr -d '"')

# 自签名证书要被信任为代码签名证书，codesign 才肯用
security find-certificate -a -p "$keychain" > "$pem"
sudo security add-trusted-cert -d -r trustRoot -p codeSign -k /Library/Keychains/System.keychain "$pem"
rm -f "$pem"

identity="$(security find-identity -p codesigning "$keychain" | awk '/\)/ {print $2; exit}')"
if [ -z "$identity" ]; then
  echo "::error::证书导入了，但钥匙串里找不到代码签名身份"
  exit 1
fi
echo "MAC_SIGN_IDENTITY=$identity" >> "$GITHUB_ENV"
echo "用证书 $identity 签名"
