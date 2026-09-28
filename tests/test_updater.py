"""应用内更新：版本比较、选包、解包与安全检查。"""

import os
import stat
import time
import zipfile

from whiteboard import updater


def app_zip(path, *, link_to="A", executable=True):
    """造一个像 .app 的压缩包：带符号链接，二进制有执行权限。"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Whiteboard.app/Contents/Info.plist", "<plist/>")
        binary = zipfile.ZipInfo("Whiteboard.app/Contents/MacOS/Whiteboard")
        binary.create_system = 3  # Unix，外部属性里才有权限位
        binary.external_attr = (0o100755 if executable else 0o100644) << 16
        archive.writestr(binary, "#!/bin/sh\nexit 0\n")
        link = zipfile.ZipInfo("Whiteboard.app/Contents/Frameworks/Current")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, link_to)
    return path


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


def test_unpack_keeps_permissions_and_symlinks(tmp_path):
    """extractall 会丢执行权限和符号链接，丢了 macOS 就打不开这个 .app。"""
    app = updater.unpack(app_zip(tmp_path / "pkg.zip"), tmp_path / "out")
    assert app is not None
    binary = app / "Contents" / "MacOS" / "Whiteboard"
    assert os.access(binary, os.X_OK)
    link = app / "Contents" / "Frameworks" / "Current"
    assert link.is_symlink() and os.readlink(link) == "A"


def test_unpack_rejects_symlink_pointing_outside(tmp_path):
    bad = app_zip(tmp_path / "bad.zip", link_to="../../../../etc/passwd")
    assert updater.unpack(bad, tmp_path / "out") is None


def test_verify_bundle_accepts_a_normal_app(tmp_path):
    app = updater.unpack(app_zip(tmp_path / "pkg.zip"), tmp_path / "out")
    assert updater.verify_bundle(app) is None


def test_verify_bundle_rejects_a_broken_app(tmp_path):
    app = updater.unpack(app_zip(tmp_path / "pkg.zip", executable=False), tmp_path / "out")
    assert "执行权限" in updater.verify_bundle(app)

    (app / "Contents" / "Info.plist").unlink()
    assert "Info.plist" in updater.verify_bundle(app)


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


class _FakeServer:
    def __init__(self):
        self.saved = False

    def save_now(self):
        self.saved = True


def make_api(tmp_path, monkeypatch, *, auto=False, frozen=True):
    """造一个不碰 pywebview 的 NativeApi。"""
    from whiteboard import app as app_module
    from whiteboard import resources
    from whiteboard.config import Config

    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"
    config.auto_update = auto
    monkeypatch.setattr(resources, "is_frozen", lambda: frozen)
    monkeypatch.setattr(app_module.resources, "is_frozen", lambda: frozen)
    return app_module.NativeApi(config, _FakeServer())


def test_manual_check_never_downloads(tmp_path, monkeypatch):
    """点「检查更新」只该拿版本信息，不能偷偷开始下载。"""
    from whiteboard import app as app_module

    staged = []
    monkeypatch.setattr(
        app_module.updater,
        "check",
        lambda: {"status": "update", "version": "9.9.9", "url": "https://github.com/a.zip",
                 "name": "a-arm64.zip", "notes": ""},
    )
    api = make_api(tmp_path, monkeypatch, auto=True)
    monkeypatch.setattr(api, "_stage_update", lambda: staged.append(True))

    result = api.check_update_now()
    assert result["status"] == "update"
    assert staged == [], "手动检查不该触发下载"


def test_automatic_check_downloads_only_when_opted_in(tmp_path, monkeypatch):
    from whiteboard import app as app_module

    monkeypatch.setattr(
        app_module.updater,
        "check",
        lambda: {"status": "update", "version": "9.9.9", "url": "https://github.com/a.zip",
                 "name": "a-arm64.zip", "notes": ""},
    )
    for auto, expected in ((False, 0), (True, 1)):
        api = make_api(tmp_path, monkeypatch, auto=auto)
        staged = []
        monkeypatch.setattr(api, "_stage_update", lambda: staged.append(True))
        api._check_update(force=False)
        # 自动下载是在后台线程里跑的，给它一点时间
        for _ in range(50):
            if len(staged) >= expected:
                break
            time.sleep(0.01)
        assert len(staged) == expected


def test_install_requires_a_staged_package(tmp_path, monkeypatch):
    """安装只负责替换，不在这一步下载——否则界面没法显示进度。"""
    api = make_api(tmp_path, monkeypatch)
    api.update_info = {"status": "update", "version": "9.9.9", "url": "https://github.com/a.zip"}
    assert api.install_update("now") is False
    assert api.install_update("quit") is False
    assert api.install_on_quit is False


def test_install_on_quit_only_flags_it(tmp_path, monkeypatch):
    from whiteboard import app as app_module

    api = make_api(tmp_path, monkeypatch)
    api.staged_app = tmp_path / "Whiteboard.app"
    api.staged_app.mkdir()
    monkeypatch.setattr(app_module.resources, "app_bundle", lambda: tmp_path / "current.app")
    swaps = []
    monkeypatch.setattr(app_module.updater, "swap_and_restart", lambda *a, **k: swaps.append(a))

    assert api.install_update("quit") is True
    assert api.install_on_quit is True
    assert swaps == [], "退出时安装不该当场替换"

    api.finish_pending_install()
    assert len(swaps) == 1
