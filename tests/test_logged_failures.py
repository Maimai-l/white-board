"""以前悄悄吞掉的错误现在会在日志里留一行（docs/backlog/008-swallowed-errors.md）。

这几处出错时界面上看不出来，只有日志能告诉人发生了什么，所以白盒地看日志。
"""

from __future__ import annotations

import logging
import subprocess

from whiteboard import server
from whiteboard.config import DEFAULT_PORT, Config


def test_an_unreadable_apps_folder_is_logged(tmp_path, caplog):
    """apps 读不出来（这里是同名的文件）时应用列表为空，日志里要说为什么。"""
    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"
    config.data_dir.mkdir()
    (config.data_dir / "apps").write_text("不是文件夹", "utf-8")

    with caplog.at_level(logging.WARNING, logger="whiteboard.server"):
        assert server.list_apps(config) == []
    assert any("list_apps" in r.getMessage() and "apps" in r.getMessage() for r in caplog.records), caplog.text


def test_a_missing_apps_folder_is_not_an_error(tmp_path, caplog):
    """还没装过应用（没有 apps 文件夹）是常态，不该记警告。"""
    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"

    with caplog.at_level(logging.WARNING, logger="whiteboard.server"):
        assert server.list_apps(config) == []
    assert not caplog.records, caplog.text


def test_a_bad_port_in_the_config_is_logged(tmp_path, caplog):
    """配置文件里的端口不是数字时退回默认端口，并在日志里写明。"""
    path = tmp_path / "config.json"
    path.write_text('{"port": "八八四八"}', "utf-8")

    with caplog.at_level(logging.WARNING, logger="whiteboard.config"):
        assert Config(path=path).requested_port == DEFAULT_PORT
    assert any("八八四八" in r.getMessage() for r in caplog.records), caplog.text


def test_running_build_logs_why_git_was_not_used(monkeypatch, caplog):
    """读不了 git 时显示版本号，日志里说明为什么没读到分支和 commit。"""

    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", no_git)
    with caplog.at_level(logging.DEBUG, logger="whiteboard.server"):
        assert server.running_build() == server.__version__
    assert any("running_build" in r.getMessage() for r in caplog.records), caplog.text
