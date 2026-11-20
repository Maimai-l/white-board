#!/usr/bin/env python3
"""生成和核对依赖锁文件（docs/backlog/006-dependency-lock.md）。

人改的是输入文件（requirements.txt、requirements-package.txt、requirements-test.txt），
安装用的是锁文件（requirements.lock、requirements-test.lock）。锁文件用
``uv pip compile --universal`` 生成：一份锁同时适用于 macOS arm64（打包）和
Linux x86_64（测试、Playwright 容器），每个包都带哈希。

锁文件第一行记着输入的指纹（输入文件的内容加上生成参数的 sha256）。改了输入却没有
重新生成时，``--check`` 和 tests/test_dependency_lock.py 会失败。

    make lock                                         # 改了输入之后重新生成（已锁的版本尽量不动）
    make lock LOCK_ARGS="--upgrade-package aiohttp"   # 升级某个包
    make lock LOCK_ARGS="--upgrade"                   # 全部升级到最新
    python tools/lock_deps.py --check                 # 只核对，不联网
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 所有锁文件共用的参数：CI 和打包都是 Python 3.12；--universal 让一份锁覆盖各个平台
COMMON_ARGS = ("--universal", "--python-version", "3.12", "--generate-hashes")
FINGERPRINT_PREFIX = "# inputs-sha256: "


@dataclass(frozen=True)
class Lock:
    output: str
    # 传给 uv pip compile 的输入（含 -c 约束）
    args: tuple[str, ...]
    # 指纹覆盖的文件：输入本身和它们 -r / -c 间接用到的输入
    inputs: tuple[str, ...]


LOCKS: Sequence[Lock] = (
    Lock(
        "requirements.lock",
        args=("requirements-package.txt",),
        inputs=("requirements-package.txt", "requirements.txt"),
    ),
    # 测试的锁以打包的锁为约束：两份锁里共有的包版本一样，测的就是打进应用里的版本
    Lock(
        "requirements-test.lock",
        args=("requirements-test.txt", "-c", "requirements.lock"),
        inputs=("requirements-test.txt", "requirements-package.txt", "requirements.txt"),
    ),
)

PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)")


def fingerprint(lock: Lock, root: Path = ROOT) -> str:
    """输入文件内容 + 生成参数的 sha256。换行统一成 \\n，在 Windows 上检出也一样。"""
    digest = hashlib.sha256()
    digest.update(repr((COMMON_ARGS, lock.args)).encode())
    for name in lock.inputs:
        text = (root / name).read_bytes().replace(b"\r\n", b"\n")
        digest.update(b"\0" + name.encode() + b"\0" + text)
    return digest.hexdigest()


def recorded_fingerprint(lock: Lock, root: Path = ROOT) -> str:
    """锁文件第一行记下的指纹；没有锁文件或没有这一行时是空字符串。"""
    path = root / lock.output
    if not path.exists():
        return ""
    first = path.read_text(encoding="utf-8").splitlines()[:1]
    if first and first[0].startswith(FINGERPRINT_PREFIX):
        return first[0][len(FINGERPRINT_PREFIX) :].split()[0]
    return ""


def stale_locks(root: Path = ROOT) -> list[str]:
    """输入改过、锁文件没有重新生成的锁文件名。"""
    return [lock.output for lock in LOCKS if recorded_fingerprint(lock, root) != fingerprint(lock, root)]


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pins(path: Path) -> dict[str, str]:
    """锁文件里的 {规范化的包名: 版本}。"""
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = PIN_RE.match(line)
        if match:
            out[normalize(match.group(1))] = match.group(2)
    return out


def find_uv(explicit: str) -> list[str]:
    if explicit:
        return [explicit]
    if shutil.which("uv"):
        return ["uv"]
    probe = subprocess.run([sys.executable, "-m", "uv", "--version"], capture_output=True, check=False)
    if probe.returncode == 0:
        return [sys.executable, "-m", "uv"]
    raise SystemExit("[lock_deps] 找不到 uv：先装一个（pip install uv 或 brew install uv），或用 UV=路径 指定")


def generate(uv: list[str], extra: Sequence[str]) -> None:
    for lock in LOCKS:
        cmd = [
            *uv,
            "pip",
            "compile",
            *COMMON_ARGS,
            *lock.args,
            *extra,
            "--quiet",
            "--custom-compile-command",
            "make lock",
            "-o",
            lock.output,
        ]
        print("[lock_deps]", " ".join(cmd), flush=True)
        subprocess.run(cmd, cwd=ROOT, check=True)
        path = ROOT / lock.output
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        lines = [line for line in lines if not line.startswith(FINGERPRINT_PREFIX)]
        header = f"{FINGERPRINT_PREFIX}{fingerprint(lock)}  (tools/lock_deps.py; make lock)\n"
        path.write_text(header + "".join(lines), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="只核对锁文件是不是最新的，不联网")
    parser.add_argument("--uv", default=os.environ.get("UV", ""), help="uv 可执行文件")
    args, extra = parser.parse_known_args(argv)
    if not args.check:
        generate(find_uv(args.uv), extra)
    stale = stale_locks()
    for name in stale:
        print(f"[lock_deps] {name} 不是最新的：输入改过之后没有重新生成，跑 make lock")
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
