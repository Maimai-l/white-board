"""发 Release 时从 CHANGELOG.zh-CN.md 取更新说明。"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_notes", ROOT / "packaging" / "release_notes.py")
release_notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_notes)

SAMPLE = """# 更新日志

## 1.1.0

- 新的一条

## 1.0.0

### 数据安全

- 旧的一条
"""


def test_takes_only_its_own_section():
    assert release_notes.section(SAMPLE, "1.0.0") == "### 数据安全\n\n- 旧的一条\n"
    assert release_notes.section(SAMPLE, "1.1.0") == "- 新的一条\n"
    assert release_notes.section(SAMPLE, "2.0.0") is None


def test_a_prerelease_uses_its_release_section(capsys, monkeypatch, tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(SAMPLE, "utf-8")
    monkeypatch.setattr(release_notes, "CHANGELOG", changelog)
    assert release_notes.main(["x", "v1.0.0-rc.1"]) == 0
    assert "旧的一条" in capsys.readouterr().out
    assert release_notes.main(["x", "3.0.0"]) == 1


def test_every_bullet_in_the_changelog_is_one_line():
    """更新对话框按行显示，续行会变成另起一段。中英两版都守这条，内容才对得齐。"""
    for name in ("CHANGELOG.zh-CN.md", "CHANGELOG.md"):
        for line in (ROOT / name).read_text("utf-8").splitlines():
            if line.startswith("  ") and line.strip():
                raise AssertionError(f"{name} 续行：{line!r}")


def test_the_current_version_has_notes():
    from whiteboard import __version__

    text = (ROOT / "CHANGELOG.zh-CN.md").read_text("utf-8")
    assert release_notes.section(text, __version__.split("-", 1)[0])
