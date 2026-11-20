"""Sparkle 更新的纯逻辑：通道、配置是否齐全、版本号写入（docs/design/ci-and-updates.zh-CN.md 第 3 节）。

加载 Sparkle.framework 本身只能在 Mac 上的打包应用里验证：CI 的 package job 跑
``--check-updater``，真机自动更新写在发版检查表里。
"""

import importlib.util
import plistlib
from pathlib import Path

import pytest

from whiteboard import sparkle
from whiteboard.config import Config

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("set_version", ROOT / "packaging" / "set_version.py")
set_version = importlib.util.module_from_spec(spec)
spec.loader.exec_module(set_version)


def test_channel_comes_from_the_config_once_it_is_set():
    assert sparkle.resolve_channel("stable", "alpha") == "stable"
    assert sparkle.resolve_channel("alpha", "stable") == "alpha"


def test_an_unset_channel_follows_the_build():
    assert sparkle.resolve_channel("", "alpha") == "alpha"
    assert sparkle.resolve_channel("", "stable") == "stable"
    assert sparkle.resolve_channel("", "source") == "stable"


def test_only_alpha_sees_alpha_items():
    assert sparkle.allowed_channels("alpha") == {"alpha"}
    assert sparkle.allowed_channels("stable") == set()


def make_bundle(tmp_path, info, framework=True):
    bundle = tmp_path / "Whiteboard.app"
    (bundle / "Contents").mkdir(parents=True)
    with open(bundle / "Contents" / "Info.plist", "wb") as handle:
        plistlib.dump(info, handle)
    if framework:
        (bundle / "Contents" / "Frameworks" / "Sparkle.framework").mkdir(parents=True)
    return bundle


FEED = {"SUFeedURL": "https://example.github.io/white-board/appcast.xml", "SUPublicEDKey": "abc="}


def test_sparkle_needs_the_framework_the_feed_and_the_key(tmp_path):
    config = Config(tmp_path / "config.json")
    assert sparkle.Updater(config, make_bundle(tmp_path / "a", FEED)).available
    assert not sparkle.Updater(config, make_bundle(tmp_path / "b", FEED, framework=False)).available
    assert not sparkle.Updater(config, make_bundle(tmp_path / "c", {"SUFeedURL": FEED["SUFeedURL"]})).available
    assert not sparkle.Updater(config, make_bundle(tmp_path / "d", {})).available


def test_running_from_source_has_no_updater(tmp_path):
    updater = sparkle.Updater(Config(tmp_path / "config.json"), bundle=None)
    updater.bundle = None
    updater.info = {}
    updater.framework = None
    assert not updater.available
    assert updater.describe()["updater"] == "none"
    assert updater.check_now() is False


def test_check_for_updates_says_where_updates_come_from(tmp_path, monkeypatch):
    """「检查更新」只有三种回答：交给 Sparkle、源码运行用 git、打包了却没有 Sparkle。"""
    from whiteboard import app, resources

    api = app.NativeApi(Config(tmp_path / "config.json"), server=None)
    assert api.check_update_now()["status"] == "source"

    monkeypatch.setattr(resources, "is_frozen", lambda: True)
    api.sparkle.error = "Sparkle.framework 加载失败"
    result = api.check_update_now()
    assert result["status"] == "unavailable" and result["message"] == "Sparkle.framework 加载失败"

    monkeypatch.setattr(api.sparkle, "check_now", lambda: True)
    assert api.check_update_now()["status"] == "sparkle"


def test_the_first_packaged_run_remembers_its_channel(tmp_path, monkeypatch):
    monkeypatch.setattr(sparkle, "__channel__", "alpha")
    config = Config(tmp_path / "config.json")
    updater = sparkle.Updater(config, make_bundle(tmp_path, FEED))
    updater.remember_channel()
    assert Config(tmp_path / "config.json").update_channel == "alpha"

    # 之后换装正式版构建，仍然留在 alpha
    monkeypatch.setattr(sparkle, "__channel__", "stable")
    assert sparkle.Updater(Config(tmp_path / "config.json"), make_bundle(tmp_path / "x", FEED)).channel == "alpha"


def test_config_rejects_unknown_channels(tmp_path):
    config = Config(tmp_path / "config.json")
    with pytest.raises(ValueError):
        config.update_channel = "beta"
    config.values["update_channel"] = "nightly"  # 手改坏了的配置文件
    assert config.update_channel == ""


INIT = '''"""x"""

__version__ = "1.3.0"
__build__ = ""
__channel__ = "source"
'''


def test_set_version_writes_version_build_and_channel():
    text = set_version.apply(INIT, "v1.3.0-alpha.20261119.153012", "20261119.153012", "alpha")
    assert '__version__ = "1.3.0-alpha.20261119.153012"' in text
    assert '__build__ = "20261119.153012"' in text
    assert '__channel__ = "alpha"' in text


def test_set_version_without_build_leaves_it_alone():
    text = set_version.apply(INIT, "1.3.1")
    assert '__version__ = "1.3.1"' in text and '__build__ = ""' in text


def test_the_package_has_the_fields_set_version_writes():
    text = (ROOT / "whiteboard" / "__init__.py").read_text("utf-8")
    set_version.apply(text, "9.9.9", "1", "alpha")  # 少了哪个字段会抛 ValueError
