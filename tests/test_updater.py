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


def test_check_reports_status_for_every_outcome():
    """界面要如实显示结果，所以 check 永远返回带 status 的字典。"""
    newer = updater.check("1.0.0", release(), "arm64")
    assert newer["status"] == "update" and newer["version"] == "2.0.0"
    assert newer["url"].endswith(".zip") and newer["notes"]

    assert updater.check("2.0.0", release(), "arm64")["status"] == "latest"
    assert updater.check("1.0.0", release(draft=True), "arm64")["status"] == "latest"
    assert updater.check("1.0.0", {}, "arm64")["status"] == "latest"

    # 有新版本但没有本机能用的包：这是错误，不能假装已是最新
    missing = updater.check("1.0.0", release(names=("a-arm64.zip", "b-arm64.zip")), "x86_64")
    assert missing["status"] == "error" and "安装包" in missing["message"]


def test_check_rejects_foreign_download_hosts():
    """更新包只认 GitHub 的地址，免得被重定向到别处。"""
    result = updater.check("1.0.0", release(host="evil.example.com"), "arm64")
    assert result["status"] == "error"
    assert not updater._host_allowed("http://github.com/a.zip")  # 必须是 https


def test_network_failure_is_reported_not_swallowed(monkeypatch):
    """查不到就要说为什么，不能让界面弹个对勾了事。"""
    monkeypatch.setattr(updater, "fetch_latest", lambda *a, **k: (None, "连不上 GitHub"))
    result = updater.check("1.0.0", None, "arm64")
    assert result == {"status": "error", "message": "连不上 GitHub"}


def test_ssl_fallback_only_kicks_in_for_certificate_errors(monkeypatch):
    """系统证书能用就用系统的；只有证书校验失败才退到 certifi。"""
    import ssl
    import urllib.error

    calls = []

    def fake_urlopen(request, timeout=None, context=None):
        calls.append(context)
        if context is None:
            raise urllib.error.URLError(ssl.SSLError("CERTIFICATE_VERIFY_FAILED"))
        return "ok"

    monkeypatch.setattr(updater.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(updater, "_certifi_context", lambda: "certifi-context")
    assert updater._urlopen(object(), 1.0) == "ok"
    assert calls == [None, "certifi-context"]

    # 不是证书问题就直接抛出去，不做无谓重试
    def plain_failure(request, timeout=None, context=None):
        raise urllib.error.URLError("连接被拒绝")

    monkeypatch.setattr(updater.urllib.request, "urlopen", plain_failure)
    try:
        updater._urlopen(object(), 1.0)
    except urllib.error.URLError:
        pass
    else:
        raise AssertionError("非证书错误应当直接抛出")


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
