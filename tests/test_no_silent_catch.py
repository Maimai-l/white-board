"""出错不能悄悄吞掉（CLAUDE.md「完成标准」，docs/backlog/008-swallowed-errors.md）。

捕获异常的地方要么记一行日志（Python 用模块的 ``log``，页面脚本用 ``reportError`` 或
``console.*``），要么把异常交出去（``raise`` / ``throw``，或者把异常对象存下来交给别处报告），
要么在同一处写一行 ``忽略：为什么可以不管`` 的注释。

ruff 的 S110 只认 ``except Exception: pass``，管不到 ``except OSError: return None`` 这类
同样没有声音的写法，页面脚本更没有现成的检查，所以这里自己扫一遍。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PY_FILES = sorted(
    [
        *(ROOT / "whiteboard").rglob("*.py"),
        *(ROOT / "packages" / "inksync" / "inksync").rglob("*.py"),
        ROOT / "run.py",
        *(ROOT / "packaging").glob("*.py"),
    ]
)
JS_FILES = sorted(
    path
    for folder in (ROOT / "whiteboard" / "web" / "static" / "js", ROOT / "packages" / "inksync" / "inksync" / "web")
    for path in folder.rglob("*.js")
    if "vendor" not in path.relative_to(folder).parts
)

MARK = "忽略："
LOGGERS = {"log", "logging", "logger"}


def _rel(path: Path, root: Path) -> str:
    return str(path.relative_to(root))


# ------------------------------------------------------------------ Python


def _reports(handler: ast.ExceptHandler) -> bool:
    """这个 except 有没有把异常交代出去：记日志、打印、再抛，或者用到了异常对象。"""
    for node in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in LOGGERS:
                return True
            if isinstance(func, ast.Name) and func.id == "print":
                return True
        if handler.name and isinstance(node, ast.Name) and node.id == handler.name:
            return True
    return False


def silent_python_handlers(path: Path, root: Path = ROOT) -> list:
    source = path.read_text("utf-8")
    lines = source.splitlines()
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ExceptHandler) or _reports(node):
            continue
        span = lines[node.lineno - 1 : node.end_lineno]
        if any(MARK in line.partition("#")[2] for line in span):
            continue
        found.append(f"{_rel(path, root)}:{node.lineno}: {lines[node.lineno - 1].strip()}")
    return found


def test_python_handlers_log_or_say_why_ignoring_is_safe():
    silent = [item for path in PY_FILES for item in silent_python_handlers(path)]
    assert not silent, "这些 except 既不记日志也没写「# 忽略：…」：\n" + "\n".join(silent)


# ------------------------------------------------------------------ 页面脚本

CATCH_BLOCK = re.compile(r"\bcatch\s*(?:\([^)]*\))?\s*\{")
CATCH_CALL = re.compile(r"\.catch\(")
JS_REPORTS = re.compile(r"\b(?:reportError|console\.\w+|throw|reject)\b")


def _strip_comments(code: str) -> str:
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    return re.sub(r"(?m)//.*$", "", code)


def _matching(source: str, start: int, open_ch: str, close_ch: str) -> int:
    """``source[start - 1]`` 是 ``open_ch``，返回和它配对的 ``close_ch`` 的位置。"""
    depth = 1
    i = start
    while depth:
        depth += {open_ch: 1, close_ch: -1}.get(source[i], 0)
        i += 1
    return i - 1


def silent_js_catches(path: Path, root: Path = ROOT) -> list:
    source = path.read_text("utf-8")
    lines = source.splitlines()
    found = []
    spans = [(m.start(), _matching(source, m.end(), "{", "}")) for m in CATCH_BLOCK.finditer(source)]
    spans += [(m.start(), _matching(source, m.end(), "(", ")")) for m in CATCH_CALL.finditer(source)]
    for start, end in spans:
        if JS_REPORTS.search(_strip_comments(source[start:end])):
            continue
        line = source.count("\n", 0, start)
        # 说明可以写在 catch 里面，也可以写在 catch 那一行或上一行
        nearby = "\n".join(lines[max(0, line - 1) : line + 1]) + source[start:end]
        if MARK in nearby:
            continue
        found.append(f"{_rel(path, root)}:{line + 1}: {lines[line].strip()}")
    return found


def test_js_catches_report_or_say_why_ignoring_is_safe():
    silent = [item for path in JS_FILES for item in silent_js_catches(path)]
    assert not silent, "这些 catch 既不上报也没写「// 忽略：…」：\n" + "\n".join(silent)


def test_the_scanners_see_what_they_should(tmp_path):
    """扫描本身别失灵：没声音的认得出来，有日志或有说明的放过。"""
    py = tmp_path / "sample.py"
    py.write_text(
        "try:\n    x()\nexcept OSError:\n    pass\n"
        "try:\n    x()\nexcept OSError as exc:\n    log.warning('x：%s', exc)\n"
        "try:\n    x()\nexcept ValueError:  # 忽略：不是数字就用默认值\n    y = 0\n",
        "utf-8",
    )
    silent = silent_python_handlers(py, tmp_path)
    assert len(silent) == 1 and ":3:" in silent[0], silent

    js = tmp_path / "sample.js"
    js.write_text(
        "try { a(); } catch (err) {}\n"
        "p.catch(() => {});\n"
        "try { a(); } catch (err) { reportError('a', { message: String(err) }); }\n"
        "try { a(); } catch (err) {\n  /* 忽略：本来就要关 */\n}\n"
        "// 忽略：上报失败没有别处可报\n"
        "q.catch(() => {});\n",
        "utf-8",
    )
    silent = silent_js_catches(js, tmp_path)
    assert [item.split(":")[1] for item in silent] == ["1", "2"], silent

