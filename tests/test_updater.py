"""应用内更新：版本比较、选包、解包与安全检查。"""

import zipfile

from whiteboard import updater


def release(tag="v2.0.0", names=("Whiteboard-2.0.0-macos-arm64.zip",), host="github.com", **extra):
    payload = {
        "tag_name": tag,
        "body": "更新说明",
        "assets": [
            {"name": name, "browser_download_url": f"https://{host}/a/b/releases/{name}"}
            for name in names
        ],
    }
    payload.update(extra)
    return payload


def test_version_parsing_and_comparison():
    assert updater.parse_version("v1.2.3") == (1, 2, 3)
    assert updater.parse_version("1.2.3-dev") == (1, 2, 3)
    assert updater.parse_version(None) == (0,)
    assert updater.is_newer("v1.0.1", "1.0.0")
    assert not updater.is_newer("1.0.0", "1.0.0")
    assert not updater.is_newer("0.9.9", "1.0.0")


def test_arch_tag():
    assert updater.arch_tag("arm64") == "arm64"
    assert updater.arch_tag("aarch64") == "arm64"
    assert updater.arch_tag("x86_64") == "x86_64"


def test_pick_asset_matches_architecture():
    names = ("Whiteboard-2.0.0-macos-arm64.zip", "Whiteboard-2.0.0-macos-x86_64.zip")
    assert "arm64" in updater.pick_asset(release(names=names), "arm64")["name"]
    assert "x86_64" in updater.pick_asset(release(names=names), "x86_64")["name"]
    # 只有一个包时就用它
    assert updater.pick_asset(release(names=("Whiteboard.zip",)), "arm64")["name"] == "Whiteboard.zip"
    # 架构对不上又有多个包：宁可不更新
    assert updater.pick_asset(release(names=("a-arm64.zip", "b-arm64.zip")), "x86_64") is None
    assert updater.pick_asset(release(names=()), "arm64") is None


def test_check_only_reports_newer_releases():
    assert updater.check("1.0.0", release(), "arm64")["version"] == "2.0.0"
    assert updater.check("2.0.0", release(), "arm64") is None
    assert updater.check("1.0.0", release(draft=True), "arm64") is None
    assert updater.check("1.0.0", {}, "arm64") is None


def test_check_rejects_foreign_download_hosts():
    """更新包只认 GitHub 的地址，免得被重定向到别处。"""
    assert updater.check("1.0.0", release(host="evil.example.com"), "arm64") is None
    assert not updater._host_allowed("http://github.com/a.zip")  # 必须是 https


def test_unpack_finds_the_app(tmp_path):
    staged = tmp_path / "pkg.zip"
    with zipfile.ZipFile(staged, "w") as archive:
        archive.writestr("Whiteboard.app/Contents/MacOS/Whiteboard", "binary")
    app = updater.unpack(staged, tmp_path / "out")
    assert app is not None and app.name == "Whiteboard.app"


def test_unpack_rejects_paths_outside_the_target(tmp_path):
    staged = tmp_path / "bad.zip"
    with zipfile.ZipFile(staged, "w") as archive:
        archive.writestr(zipfile.ZipInfo("../escaped.txt"), "nope")
    assert updater.unpack(staged, tmp_path / "out") is None
    assert not (tmp_path / "escaped.txt").exists()


def test_unpack_handles_broken_archive(tmp_path):
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"not a zip")
    assert updater.unpack(broken, tmp_path / "out") is None


def test_unpack_without_app_returns_none(tmp_path):
    staged = tmp_path / "pkg.zip"
    with zipfile.ZipFile(staged, "w") as archive:
        archive.writestr("readme.txt", "hi")
    assert updater.unpack(staged, tmp_path / "out") is None


def test_swap_needs_both_sides(tmp_path):
    assert updater.swap_and_restart(tmp_path / "missing.app", tmp_path) is False
    assert updater.swap_and_restart(tmp_path, tmp_path / "missing.app") is False


def test_download_rejects_bad_url(tmp_path):
    assert updater.download("http://evil.example.com/a.zip", tmp_path) is None
