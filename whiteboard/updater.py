"""应用内更新：查 GitHub Release、下载、替换自身、重启。

只在打包成 .app 之后有意义；源码运行时用 git 更新即可。

流程刻意保守：
下载到临时目录 → 解出 .app 并检查像样 → 交给一个脱离本进程的小脚本，
等本进程退出后把旧 bundle 挪开、拷新的过去，失败就把旧的放回来。
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

from . import __version__

log = logging.getLogger(__name__)

REPO = "Maimai-l/white-board"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
ALLOWED_HOSTS = ("github.com", "api.github.com", "objects.githubusercontent.com")
MAX_DOWNLOAD = 400 * 1024 * 1024

_NUMBER_RE = re.compile(r"\d+")


def _certifi_context() -> Optional[ssl.SSLContext]:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - 没装 certifi 就算了
        return None


def _urlopen(request, timeout: float):
    """先按系统证书走；打包后的应用里系统证书链可能不可用，再用 certifi 兜底。

    反过来不行：公司代理之类的自签证书只在系统钥匙串里，一上来就用 certifi
    会把本来正常的环境弄挂。
    """
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.URLError as exc:
        if not isinstance(getattr(exc, "reason", None), ssl.SSLError):
            raise
        context = _certifi_context()
        if context is None:
            raise
        log.info("系统证书验证失败，改用 certifi 重试")
        return urllib.request.urlopen(request, timeout=timeout, context=context)


def parse_version(text: Any) -> tuple:
    """`v1.2.3` / `1.2.3-dev` → (1, 2, 3)，无法解析时返回 (0,)。"""
    if not isinstance(text, str):
        return (0,)
    numbers = _NUMBER_RE.findall(text.split("+")[0])
    if not numbers:
        return (0,)
    return tuple(int(n) for n in numbers[:4])


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def arch_tag(machine: Optional[str] = None) -> str:
    machine = (machine or platform.machine() or "").lower()
    return "arm64" if machine in ("arm64", "aarch64") else "x86_64"


def pick_asset(release: Dict[str, Any], machine: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """挑与本机架构匹配的 zip；只有一个 zip 时就用它。"""
    assets = [a for a in release.get("assets", []) if str(a.get("name", "")).endswith(".zip")]
    if not assets:
        return None
    tag = arch_tag(machine)
    for asset in assets:
        if tag in str(asset.get("name", "")).lower():
            return asset
    return assets[0] if len(assets) == 1 else None


def fetch_latest(timeout: float = 6.0, url: str = LATEST_URL):
    """返回 ``(release, 失败原因)``，成功时原因为 None。"""
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Whiteboard/{__version__}",
        },
    )
    try:
        with _urlopen(request, timeout) as response:
            return json.loads(response.read(2 * 1024 * 1024).decode("utf-8")), None
    except urllib.error.HTTPError as exc:
        reason = f"GitHub 返回 {exc.code}"
        if exc.code == 403:
            reason += "（可能是访问太频繁）"
        elif exc.code == 404:
            reason += "（仓库还没有发布过版本）"
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        reason = str(getattr(exc, "reason", exc)) or exc.__class__.__name__
    log.info("检查更新失败：%s", reason)
    return None, reason


def check(
    current: str = __version__,
    release: Optional[Dict[str, Any]] = None,
    machine: Optional[str] = None,
) -> Dict[str, Any]:
    """检查更新。

    永远返回一个带 ``status`` 的字典，方便界面如实告诉用户结果：

    * ``update``  有新版本，附带下载地址与更新说明
    * ``latest``  已经是最新
    * ``error``   没查成（网络、证书、限流……），``message`` 是原因
    """
    error = None
    if release is None:
        release, error = fetch_latest()
    if release is None:
        return {"status": "error", "message": error or "没能连上 GitHub"}
    if not isinstance(release, dict):
        return {"status": "error", "message": "GitHub 返回的内容看不懂"}
    if release.get("draft"):
        return {"status": "latest", "version": current}

    tag = release.get("tag_name") or release.get("name") or ""
    if not is_newer(str(tag), current):
        return {"status": "latest", "version": current}

    asset = pick_asset(release, machine)
    if not asset:
        return {"status": "error", "message": f"{tag} 没有适配本机的安装包"}
    url = str(asset.get("browser_download_url", ""))
    if not _host_allowed(url):
        log.warning("更新包地址不在白名单里，已忽略：%s", url)
        return {"status": "error", "message": "更新包地址不可信"}
    return {
        "status": "update",
        "version": str(tag).lstrip("vV"),
        "url": url,
        "name": str(asset.get("name", "")),
        "notes": str(release.get("body", ""))[:2000],
    }


def _host_allowed(url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS


def download(url: str, into: Path, timeout: float = 60.0, on_progress=None) -> Optional[Path]:
    if not _host_allowed(url):
        return None
    into.mkdir(parents=True, exist_ok=True)
    target = into / "update.zip"
    request = urllib.request.Request(url, headers={"User-Agent": f"Whiteboard/{__version__}"})
    try:
        with _urlopen(request, timeout) as response, target.open("wb") as out:
            try:
                total = int(response.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                total = 0
            written = 0
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_DOWNLOAD:
                    raise OSError("更新包过大")
                out.write(chunk)
                if on_progress is not None and total > 0:
                    on_progress(min(1.0, written / total))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log.error("下载更新失败：%s", exc)
        target.unlink(missing_ok=True)
        return None
    return target


def _safe_members(archive: zipfile.ZipFile, into: Path) -> bool:
    """检查压缩包里没有越界路径。"""
    root = into.resolve()
    for member in archive.namelist():
        destination = (into / member).resolve()
        if destination != root and root not in destination.parents:
            log.error("更新包里有越界路径：%s", member)
            return False
    return True


def _extract(archive: zipfile.ZipFile, into: Path) -> bool:
    """自己解压：``ZipFile.extractall`` 会丢掉权限位和符号链接。

    .app 里两样都有（``Contents/MacOS`` 下的可执行文件、Frameworks 里的
    ``Versions/Current`` 之类），丢了之后 macOS 会直接拒绝打开，
    报「应用程序“…”无法打开」。
    """
    root = into.resolve()
    for info in archive.infolist():
        target = into / info.filename
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            link = archive.read(info).decode("utf-8", "replace")
            resolved = (target.parent / link).resolve()
            if os.path.isabs(link) or (resolved != root and root not in resolved.parents):
                log.error("更新包里的符号链接指向包外：%s -> %s", info.filename, link)
                return False
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink() or target.exists():
                target.unlink()
            os.symlink(link, target)
            continue
        extracted = Path(archive.extract(info, into))
        if extracted.is_file() and mode & 0o111:
            extracted.chmod(extracted.stat().st_mode | 0o111)
    return True


def unpack(zip_path: Path, into: Path) -> Optional[Path]:
    """解压并返回里面的 .app 路径。"""
    into.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            if not _safe_members(archive, into):
                return None
            # macOS 上优先用 ditto：权限、符号链接、扩展属性和代码签名都保得住，
            # 这也是 Sparkle / electron-updater 的做法。
            if sys.platform == "darwin" and _ditto(zip_path, into):
                pass
            elif not _extract(archive, into):
                return None
    except (zipfile.BadZipFile, OSError) as exc:
        log.error("解压更新失败：%s", exc)
        return None
    apps = sorted(into.glob("*.app")) or sorted(into.glob("*/*.app"))
    if not apps:
        log.error("更新包里没有 .app")
        return None
    return apps[0]


def _ditto(zip_path: Path, into: Path) -> bool:
    try:
        result = subprocess.run(
            ["/usr/bin/ditto", "-x", "-k", str(zip_path), str(into)],
            capture_output=True,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("ditto 解压失败，改用内置解压：%s", exc)
        return False
    if result.returncode != 0:
        log.warning("ditto 解压失败（%s），改用内置解压", result.returncode)
        return False
    return True


def verify_bundle(app: Path) -> Optional[str]:
    """检查解出来的 .app 能不能用；能用返回 None，不能用返回原因。

    宁可在这里挡下来，也不要把一个打不开的 .app 换到用户的应用程序里。
    """
    if not (app / "Contents" / "Info.plist").is_file():
        return "缺少 Info.plist"
    macos = app / "Contents" / "MacOS"
    if not macos.is_dir():
        return "缺少 Contents/MacOS"
    if not any(entry.is_file() and os.access(entry, os.X_OK) for entry in macos.iterdir()):
        return "可执行文件没有执行权限"
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            ["/usr/bin/codesign", "--verify", "--strict", str(app)],
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("没法校验代码签名：%s", exc)
        return None
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        return "代码签名校验不通过：" + (detail[-1] if detail else "未知原因")
    return None


SWAP_SCRIPT = """#!/bin/sh
pid="$1"; staged="$2"; target="$3"; relaunch="$4"
i=0
while kill -0 "$pid" 2>/dev/null && [ "$i" -lt 240 ]; do sleep 0.5; i=$((i+1)); done
backup="$target.old"
rm -rf "$backup"
mv "$target" "$backup" 2>/dev/null
# 换完再验一次签名：验不过就把旧版本原样放回去，宁可没更新也不要留个打不开的
if ditto "$staged" "$target" && /usr/bin/codesign --verify --strict "$target"; then
  rm -rf "$backup"
else
  rm -rf "$target"
  mv "$backup" "$target"
fi
xattr -dr com.apple.quarantine "$target" 2>/dev/null
[ "$relaunch" = "1" ] && open "$target"
exit 0
"""


def swap_and_restart(staged_app: Path, bundle: Path, relaunch: bool = True) -> bool:
    """本进程退出后替换 bundle；``relaunch`` 为真时再把新版本打开。

    失败会把旧版本原样放回去。
    """
    if not staged_app.exists() or not bundle.exists():
        return False
    script = Path(tempfile.mkdtemp(prefix="whiteboard-update-")) / "swap.sh"
    script.write_text(SWAP_SCRIPT, "utf-8")
    script.chmod(0o755)
    try:
        subprocess.Popen(
            [
                "/bin/sh",
                str(script),
                str(os.getpid()),
                str(staged_app),
                str(bundle),
                "1" if relaunch else "0",
            ],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        log.error("启动替换脚本失败：%s", exc)
        return False
    return True


def cleanup(*paths: Path) -> None:
    for path in paths:
        shutil.rmtree(path, ignore_errors=True)
