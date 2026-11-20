"""依赖锁文件（docs/backlog/006-dependency-lock.md）。

安装都从锁文件来（make deps、CI 的各个 job、打包）。这里核对：
改了输入文件就必须重新生成锁文件；锁文件里每个包都固定了版本和哈希；
测试和打包装的共有的包版本相同；Playwright 镜像版本和锁里的 playwright 一致。
不联网，所以放在 checks 里每次都跑。
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("lock_deps", ROOT / "tools" / "lock_deps.py")
lock_deps = importlib.util.module_from_spec(spec)
sys.modules["lock_deps"] = lock_deps  # dataclass 要在 sys.modules 里找到所在的模块
spec.loader.exec_module(lock_deps)

LOCK_NAMES = [lock.output for lock in lock_deps.LOCKS]


def test_every_lock_was_regenerated_after_its_inputs_changed():
    stale = lock_deps.stale_locks()
    assert not stale, f"输入改过但锁文件没有重新生成：{', '.join(stale)}。跑 make lock 再提交"


def test_editing_an_input_without_relocking_is_caught(tmp_path):
    for name in {n for lock in lock_deps.LOCKS for n in lock.inputs}:
        shutil.copy(ROOT / name, tmp_path / name)
    # 刚生成过的锁文件：只有记下的指纹要紧
    for lock in lock_deps.LOCKS:
        (tmp_path / lock.output).write_text(
            f"{lock_deps.FINGERPRINT_PREFIX}{lock_deps.fingerprint(lock, tmp_path)}  (make lock)\naiohttp==3.9.0\n",
            encoding="utf-8",
        )
    assert lock_deps.stale_locks(tmp_path) == []

    with open(tmp_path / "requirements.txt", "a", encoding="utf-8") as fh:
        fh.write("requests>=2\n")
    # requirements.txt 是两份锁的输入（测试的锁以打包的锁为约束）
    assert lock_deps.stale_locks(tmp_path) == LOCK_NAMES

    shutil.copy(ROOT / "requirements.txt", tmp_path / "requirements.txt")
    with open(tmp_path / "requirements-test.txt", "a", encoding="utf-8") as fh:
        fh.write("requests>=2\n")
    assert lock_deps.stale_locks(tmp_path) == ["requirements-test.lock"]


@pytest.mark.parametrize("name", LOCK_NAMES)
def test_every_requirement_in_a_lock_is_pinned_with_hashes(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    # 一条要求：起始行（包名==版本 [; 标记] \）加上后面缩进的 --hash 行
    entries = re.split(r"\n(?=[^\s#])", text)
    requirements = [e for e in entries if e.strip() and not e.lstrip().startswith("#")]
    assert requirements, f"{name} 里没有任何包"
    for entry in requirements:
        head = entry.splitlines()[0]
        assert lock_deps.PIN_RE.match(head), f"{name}：没有用 == 固定版本：{head}"
        assert "--hash=sha256:" in entry, f"{name}：没有哈希：{head}"


def test_packages_shared_by_tests_and_packaging_have_the_same_version():
    app = lock_deps.pins(ROOT / "requirements.lock")
    test = lock_deps.pins(ROOT / "requirements-test.lock")
    shared = sorted(set(app) & set(test))
    assert "aiohttp" in shared and "pillow" in shared
    differ = {name: (app[name], test[name]) for name in shared if app[name] != test[name]}
    assert not differ, f"测试和打包装的版本不一样（打包, 测试）：{differ}"


def test_packaging_lock_has_the_build_and_signing_tools():
    app = lock_deps.pins(ROOT / "requirements.lock")
    for name in ("pyinstaller", "cryptography", "pywebview", "pyobjc-framework-webkit", "aiohttp"):
        assert name in app, f"requirements.lock 里没有 {name}"


def test_playwright_image_version_matches_the_locked_playwright():
    pinned = re.search(r"^playwright==(\S+)", (ROOT / "requirements-test.txt").read_text(encoding="utf-8"), re.MULTILINE)
    assert pinned, "requirements-test.txt 里的 playwright 要用 == 固定（镜像版本跟着它）"
    assert lock_deps.pins(ROOT / "requirements-test.lock")["playwright"] == pinned.group(1)
    action = (ROOT / ".github/actions/playwright/action.yml").read_text(encoding="utf-8")
    assert "-r requirements-test.lock" in action
    assert re.search(r"version=.*playwright==.*requirements-test\.lock", action), "镜像版本要从锁文件读"
