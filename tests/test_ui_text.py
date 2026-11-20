"""界面文字按 docs/ui-text.md 写：不用全角括号和引号，短语里不加标点。

扫描页面脚本里的字符串字面量和 HTML 页面里的文字，找出 docs/ui-text.md 不允许的全角符号。
注释、以及 reportError(…) / console.*(…) 的参数是给开发者看的日志，不算界面文字。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "whiteboard" / "web"
SCRIPTS = sorted((WEB / "static" / "js").glob("*.js")) + sorted(
    (ROOT / "packages" / "inksync" / "inksync" / "web").glob("*.js")
)
PAGES = sorted(WEB.glob("*.html"))

# 括号和引号在界面上一律不用；冒号、逗号、句号说明文字写成了一句话，也不用
FORBIDDEN = re.compile("[「」『』《》（）：，。]")

# 参数是日志、不显示在界面上的调用
LOG_CALLS = re.compile(r"^(reportError|console\.\w+)$")

# 正则字面量不扫（page-range.js 的分隔符要认用户敲的全角逗号和顿号），所以不需要例外名单。


def _callee(code: str, end: int) -> str:
    """``(`` 前面紧挨着的名字，例如 ``console.warn``。"""
    m = re.search(r"([A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*)\s*$", code[max(0, end - 200) : end])
    return re.sub(r"\s+", "", m.group(1)) if m else ""


_REGEX_BEFORE = set("(,=:[!&|?{};+-*%<>~^")
_REGEX_KEYWORDS = {"return", "typeof", "case", "in", "of", "new", "delete", "void", "throw"}


def js_strings(source: str) -> list[tuple[int, str, bool]]:
    """JS 源码里的字符串字面量：(行号, 文字, 是否在日志调用的参数里)。

    模板字符串只取文字部分，``${…}`` 里的表达式照常扫描。正则字面量跳过。
    """
    out: list[tuple[int, str, bool]] = []
    stack: list[str] = []  # 每层括号：调用名（"(" 才有）或 "{" / "[" / "${"
    i, n = 0, len(source)

    def in_log() -> bool:
        return any(LOG_CALLS.match(s) for s in stack)

    def prev_significant(pos: int) -> str:
        j = pos - 1
        while j >= 0 and source[j] in " \t\r\n":
            j -= 1
        if j < 0:
            return "("
        if source[j].isalnum() or source[j] in "_$":
            m = re.search(r"[A-Za-z_$][\w$]*$", source[max(0, j - 63) : j + 1])
            return m.group(0) if m else "x"
        return source[j]

    def read_template(pos: int) -> int:
        """从 ` 后面开始读，读到 ` 或 ${ 为止，返回停下的位置。"""
        start = pos
        buf = []
        while pos < n:
            c = source[pos]
            if c == "\\":
                buf.append(source[pos : pos + 2])
                pos += 2
                continue
            if c == "`":
                out.append((source.count("\n", 0, start) + 1, "".join(buf), in_log()))
                return pos + 1
            if c == "$" and source[pos + 1 : pos + 2] == "{":
                out.append((source.count("\n", 0, start) + 1, "".join(buf), in_log()))
                stack.append("${")
                return pos + 2
            buf.append(c)
            pos += 1
        return pos

    while i < n:
        c = source[i]
        nxt = source[i + 1 : i + 2]
        if c == "/" and nxt == "/":
            i = source.find("\n", i)
            i = n if i < 0 else i
        elif c == "/" and nxt == "*":
            i = source.find("*/", i + 2)
            i = n if i < 0 else i + 2
        elif c == "/" and (prev_significant(i) in _REGEX_BEFORE or prev_significant(i) in _REGEX_KEYWORDS):
            j, in_class = i + 1, False
            while j < n:
                if source[j] == "\\":
                    j += 2
                    continue
                if source[j] == "[":
                    in_class = True
                elif source[j] == "]":
                    in_class = False
                elif source[j] == "/" and not in_class:
                    break
                elif source[j] == "\n":
                    break
                j += 1
            i = j + 1
        elif c in "\"'":
            j = i + 1
            while j < n and source[j] != c:
                j += 2 if source[j] == "\\" else 1
            out.append((source.count("\n", 0, i) + 1, source[i + 1 : j], in_log()))
            i = j + 1
        elif c == "`":
            i = read_template(i + 1)
        elif c == "(":
            stack.append(_callee(source, i) or "(")
            i += 1
        elif c in "[{":
            stack.append(c)
            i += 1
        elif c in ")]}":
            top = stack.pop() if stack else ""
            i += 1
            if c == "}" and top == "${":
                i = read_template(i)
        else:
            i += 1
    return out


class _Text(HTMLParser):
    """HTML 里显示出来的文字和会读出来或显示的属性。脚本和样式另算。"""

    ATTRS = {"title", "aria-label", "placeholder", "alt", "value", "content"}

    def __init__(self) -> None:
        super().__init__()
        self.items: list[tuple[int, str]] = []
        self.skip = 0
        self.scripts: list[tuple[int, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        for name, value in attrs:
            if name in self.ATTRS and value:
                self.items.append((self.getpos()[0], value))

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1

    def handle_data(self, data):
        if self.skip:
            if data.strip():
                self.scripts.append((self.getpos()[0], data))
        elif data.strip():
            self.items.append((self.getpos()[0], data))


def violations() -> list[str]:
    found = []
    for path in SCRIPTS:
        for line, text, logged in js_strings(path.read_text(encoding="utf-8")):
            if logged:
                continue
            if FORBIDDEN.search(text):
                found.append(f"{path.relative_to(ROOT)}:{line}: {text!r}")
    for path in PAGES:
        parser = _Text()
        parser.feed(path.read_text(encoding="utf-8"))
        texts = list(parser.items)
        for line, script in parser.scripts:
            texts += [(line + ln - 1, t) for ln, t, logged in js_strings(script) if not logged]
        for line, text in texts:
            if FORBIDDEN.search(text):
                found.append(f"{path.relative_to(ROOT)}:{line}: {text.strip()!r}")
    return found


def test_interface_text_has_no_full_width_brackets_or_sentence_punctuation():
    found = violations()
    assert not found, "界面文字不合 docs/ui-text.md：\n" + "\n".join(found)


@pytest.mark.parametrize(
    "source, expected",
    [
        ('a("导出失败");', [("导出失败", False)]),
        ('reportError("导出失败：x", { m: "y" });', [("导出失败：x", True), ("y", True)]),
        ("console.warn(`读不到 ${f('k')}：x`);", [("读不到 ", True), ("k", True), ("：x", True)]),
        ("x = /[，、]/; y = '已选';", [("已选", False)]),
        ("// 注释：'a'\nz = 'b';", [("b", False)]),
        ("t = `${n} 页`;", [("", False), (" 页", False)]),
    ],
)
def test_js_strings_finds_literals_and_log_arguments(source, expected):
    assert [(text, logged) for _, text, logged in js_strings(source)] == expected
