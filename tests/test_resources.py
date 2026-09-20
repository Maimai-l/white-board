"""打包后的路径与日志。"""

import logging

from whiteboard import resources


def test_web_resources_are_found():
    assert resources.web_dir().is_dir()
    assert (resources.web_dir() / "index.html").exists()
    assert (resources.web_dir() / "static" / "js" / "app.js").exists()


def test_not_frozen_in_source_checkout():
    assert resources.is_frozen() is False
    assert resources.app_bundle() is None


def test_log_path_follows_the_platform(tmp_path, monkeypatch):
    monkeypatch.setattr(resources.sys, "platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert resources.log_path() == tmp_path / "Whiteboard.log"

    monkeypatch.setattr(resources.sys, "platform", "darwin")
    assert resources.log_path().parts[-3:] == ("Library", "Logs", "Whiteboard.log")


def test_setup_logging_writes_a_file(tmp_path, monkeypatch):
    monkeypatch.setattr(resources.sys, "platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        path = resources.setup_logging(debug=False, to_file=True)
        logging.getLogger("whiteboard.test").info("你好")
        for handler in root.handlers:
            handler.flush()
        assert path is not None and path.exists()
        assert "你好" in path.read_text("utf-8")
    finally:
        for handler in list(root.handlers):
            if handler not in before:
                root.removeHandler(handler)
                handler.close()
