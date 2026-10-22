"""发 Release 时从 CHANGELOG.md 取更新说明；CHANGELOG.md 本身要守 docs/changelog-style.md。"""

import importlib.util
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_notes", ROOT / "packaging" / "release_notes.py")
release_notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_notes)

CHANGELOG = ROOT / "CHANGELOG.md"
STYLE = ROOT / "docs" / "changelog-style.md"

SAMPLE = """# Changelog

## 1.1.0 (unreleased)

- Boards: A new entry.

## 1.0.0 (2026-09-29)

### Fixed

- Storage: An old entry.

## 0.9.0

- Mac: A heading without a date.
"""

# 规范第 3 节：小节只能是这几个，按这个顺序
SUBSECTIONS = ["Upgrade notes", "Added", "Changed", "Fixed", "Security", "Removed"]
MAX_ENTRY = 200


def areas():
    """规范第 4.2 节列出的所属部分。从规范里读，规范是唯一的来源。"""
    text = STYLE.read_text("utf-8")
    part = text.split("### 4.2 Area prefix", 1)[1].split("###", 1)[0]
    line = next(row for row in part.splitlines() if row.startswith("`"))
    return re.findall(r"`([^`]+)`", line)


def test_takes_only_its_own_section():
    assert release_notes.section(SAMPLE, "1.0.0") == "### Fixed\n\n- Storage: An old entry.\n"
    assert release_notes.section(SAMPLE, "1.1.0") == "- Boards: A new entry.\n"
    assert release_notes.section(SAMPLE, "0.9.0") == "- Mac: A heading without a date.\n"
    assert release_notes.section(SAMPLE, "2.0.0") is None
    assert release_notes.section(SAMPLE, "1.0") is None  # 不能匹配到 1.0.0 那一节


def test_a_prerelease_uses_its_release_section(capsys, monkeypatch, tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(SAMPLE, "utf-8")
    monkeypatch.setattr(release_notes, "CHANGELOG", changelog)
    assert release_notes.main(["x", "v1.0.0-rc.1"]) == 0
    assert "An old entry" in capsys.readouterr().out
    assert release_notes.main(["x", "3.0.0"]) == 1


def test_the_current_version_has_notes():
    from whiteboard import __version__

    assert release_notes.section(CHANGELOG.read_text("utf-8"), __version__.split("-", 1)[0])


def test_there_is_only_the_english_changelog():
    assert not (ROOT / "CHANGELOG.zh-CN.md").exists()


def test_the_changelog_has_no_chinese():
    for number, line in enumerate(CHANGELOG.read_text("utf-8").splitlines(), 1):
        assert not re.search(r"[　-〿一-鿿＀-￯]", line), f"第 {number} 行有中文：{line!r}"


def test_the_changelog_follows_the_style_guide():
    """规范里能机器检查的部分：标题、小节、每一条的格式。其余靠发版前对照清单人工检查。"""
    allowed = areas()
    assert "inksync" in allowed and "Tool picker" in allowed
    problems = []
    order = []
    for number, line in enumerate(CHANGELOG.read_text("utf-8").splitlines(), 1):
        where = f"第 {number} 行"
        if line.startswith("## "):
            if not re.fullmatch(r"## \d+\.\d+\.\d+ \((\d{4}-\d{2}-\d{2}|unreleased)\)", line):
                problems.append(f"{where}：版本标题应为 ## x.y.z (YYYY-MM-DD) 或 (unreleased)：{line!r}")
            order = []
        elif line.startswith("### "):
            name = line[4:]
            if name not in SUBSECTIONS:
                problems.append(f"{where}：小节只能是 {SUBSECTIONS}：{line!r}")
            elif order and SUBSECTIONS.index(name) <= SUBSECTIONS.index(order[-1]):
                problems.append(f"{where}：小节顺序不对或重复：{line!r}")
            else:
                order.append(name)
        elif line.startswith("  ") and line.strip():
            problems.append(f"{where}：续行（更新对话框按行显示，每一条只能占一行）：{line!r}")
        elif line.startswith("- "):
            entry = line[2:]
            area, sep, body = entry.partition(": ")
            if not sep or area not in allowed:
                problems.append(f"{where}：开头不是规范第 4.2 节列出的所属部分：{line!r}")
            if len(line) > MAX_ENTRY:
                problems.append(f"{where}：超过 {MAX_ENTRY} 个字符（{len(line)}）")
            if not line.endswith("."):
                problems.append(f"{where}：没有以句号结尾")
            if body[:1].islower():
                problems.append(f"{where}：冒号后应以大写字母开头")
            if re.search(r"\b(?:\w+n't|can't|won't)\b|!", line):
                problems.append(f"{where}：有缩写或感叹号")
    assert not problems, "\n".join(problems)


# 规范第 9 节从这个提交之后开始执行；它之前的历史已经改写成英文
COMMIT_RULES_SINCE = "01386ff883bb4beff6fcb20ef8091ffd3440bb5a"


def commits_to_check():
    """01386ff 之后、本地检出里有的提交。CI 的浅克隆里通常只有最新的一个。"""
    def git(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout

    if not git("rev-parse", "--git-dir").strip():
        return []
    shas = git("rev-list", "HEAD", "--not", COMMIT_RULES_SINCE).split()
    if not shas and git("cat-file", "-t", COMMIT_RULES_SINCE).strip() != "commit":
        shas = git("rev-list", "-n", "20", "HEAD").split()  # 截断的检出：只查检出里有的
    if COMMIT_RULES_SINCE in shas:
        shas = shas[: shas.index(COMMIT_RULES_SINCE)]
    return [(sha, git("log", "-1", "--format=%B", sha)) for sha in shas]


def test_new_commit_messages_follow_the_style_guide():
    problems = []
    for sha, message in commits_to_check():
        subject = message.split("\n", 1)[0]
        if subject.startswith("Merge "):
            continue  # GitHub 生成的合并提交
        if re.search(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]", message):
            problems.append(f"{sha[:7]}：提交说明里有中文")
        if len(subject) > 72:
            problems.append(f"{sha[:7]}：标题超过 72 个字符（{len(subject)}）：{subject!r}")
        if subject.endswith("."):
            problems.append(f"{sha[:7]}：标题不应以句号结尾：{subject!r}")
        if subject[:1].islower():
            problems.append(f"{sha[:7]}：标题应以大写字母开头：{subject!r}")
    assert not problems, "\n".join(problems)
