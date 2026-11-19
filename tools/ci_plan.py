#!/usr/bin/env python3
"""CI 的决策点：这次运行跑哪些 job、版本号是什么、要不要发布更新。

规则见 docs/design/ci-and-updates.zh-CN.md 第 2.1 节。``plan()`` 是纯函数，
规则的白盒测试在 tests/test_ci_plan.py；``main()`` 只负责从 GitHub Actions 的
环境里收集输入，再把结果写进 $GITHUB_OUTPUT 和运行摘要。

本地看某次改动会跑什么：

    python tools/ci_plan.py --dry-run --changed whiteboard/app.py docs/sketch.md
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set

ROOT = Path(__file__).resolve().parents[1]

JOBS = ("checks", "unit", "e2e", "webkit", "js", "inksync", "ipad", "package", "extension")
ALL = frozenset(JOBS)

APP = {"unit", "e2e", "webkit", "package"}
WEB = APP | {"js"}

# 按顺序匹配，每个文件取第一条命中的规则。前缀以 / 结尾的是目录。
RULES: Sequence[tuple] = (
    # 改了 CI 自己：什么都要跑一遍，才知道新的 CI 是对的
    ((".github/", "tools/ci_plan.py", "tools/ci_summary.py", "Makefile", "requirements-test.txt"), ALL),
    (("ipad/",), {"ipad"}),
    (("packages/inksync/inksync/web/",), WEB | {"inksync"}),
    (("packages/inksync/",), APP | {"inksync"}),
    (("whiteboard/web/",), WEB),
    (("whiteboard/", "run.py"), APP),
    (("tests/js/",), {"js"}),
    (("tests/",), {"unit", "e2e", "webkit"}),
    (("packaging/", "requirements.txt"), {"unit", "package"}),
    (("extensions/",), {"extension", "e2e"}),
    (("examples/", "tools/"), {"unit"}),
    (("docs/",), set()),
)

# 不影响任何构建和测试的文件：只要 checks（CHANGELOG 的格式也在 checks 里查）
DOC_SUFFIXES = (".md", ".txt", ".command")
DOC_FILES = {".gitignore", ".gitattributes", "LICENSE"}

MESSAGE_RE = re.compile(r"\[ci:\s*([a-z0-9,\s]+)\]", re.I)


@dataclass
class Plan:
    jobs: Set[str]
    publish: str  # alpha | stable | dry-run | none
    reasons: List[str] = field(default_factory=list)

    def outputs(self) -> Dict[str, str]:
        out = {job: "true" if job in self.jobs else "false" for job in JOBS}
        out["publish"] = self.publish
        return out


def jobs_for_file(path: str) -> Set[str]:
    """一个改动的文件需要哪些 job（不含总是要跑的 checks）。认不出的路径跑全部。"""
    if path in DOC_FILES or (path.endswith(DOC_SUFFIXES) and not path.startswith("requirements")):
        return set()
    for prefixes, jobs in RULES:
        for prefix in prefixes:
            if path == prefix or (prefix.endswith("/") and path.startswith(prefix)):
                return set(jobs)
    return set(ALL)


def parse_job_list(text: str) -> Optional[Set[str]]:
    """``"e2e, webkit"`` → {"e2e", "webkit"}；``"all"`` → 全部；空 → None。名字不认识就报错。"""
    names = {part.strip().lower() for part in text.split(",") if part.strip()}
    if not names:
        return None
    if "all" in names:
        return set(ALL)
    unknown = names - ALL
    if unknown:
        raise ValueError(f"不认识的 job：{', '.join(sorted(unknown))}（可选：{', '.join(JOBS)}, all）")
    return names


def plan(
    event: str,
    ref: str,
    changed: Optional[Iterable[str]],
    message: str = "",
    jobs_input: str = "",
) -> Plan:
    """决定跑哪些 job。``changed`` 为 None 表示拿不到改动列表（比如新分支的第一次推送）。"""
    reasons: List[str] = []
    is_tag = ref.startswith("refs/tags/v")
    is_dev = event == "push" and ref == "refs/heads/dev"

    if is_tag or is_dev:
        jobs = set(ALL)
        reasons.append("tag：正式版要过完整门禁" if is_tag else "dev：alpha 要过完整门禁")
    elif event == "workflow_dispatch" and parse_job_list(jobs_input or "all") is not None:
        jobs = parse_job_list(jobs_input or "all")
        reasons.append(f"手动运行：{jobs_input or 'all'}")
    elif MESSAGE_RE.search(message or ""):
        jobs = parse_job_list(MESSAGE_RE.search(message).group(1)) or set()
        reasons.append(f"提交说明指定：{MESSAGE_RE.search(message).group(0)}")
    elif changed is None:
        jobs = set(ALL)
        reasons.append("拿不到改动列表（新分支的第一次推送）：全部")
    else:
        jobs = set()
        files = list(changed)
        for path in files:
            needed = jobs_for_file(path)
            if needed - jobs:
                reasons.append(f"`{path}` → {', '.join(sorted(needed - jobs))}")
            jobs |= needed
        if not files:
            reasons.append("没有改动的文件")

    jobs.add("checks")
    if "package" in jobs:
        jobs.add("ipad")  # 应用里要带同版本的 iPad 外壳

    if is_tag:
        # 预发布 tag（v1.3.0-rc.1）和以前一样不推给所有用户：只进 alpha 通道
        publish = "alpha" if "-" in ref else "stable"
    elif is_dev:
        publish = "alpha"
    elif "package" in jobs:
        publish = "dry-run"
    else:
        publish = "none"
    return Plan(jobs=jobs, publish=publish, reasons=reasons)


# ---------------------------------------------------------------- 版本号


def source_version(root: Path = ROOT) -> str:
    text = (root / "whiteboard" / "__init__.py").read_text("utf-8")
    match = re.search(r'^__version__ = "([^"]+)"', text, re.M)
    if not match:
        raise SystemExit("whiteboard/__init__.py 里没有 __version__")
    return match.group(1)


def versions(ref: str, now: _dt.datetime, base: str) -> Dict[str, str]:
    """版本号（第 3.2 节）。build 是构建时间，Sparkle 只比它；version 是给人看的。"""
    build = now.strftime("%Y%m%d.%H%M%S")
    if ref.startswith("refs/tags/v"):
        version = ref[len("refs/tags/v"):]
        channel = "stable"
    else:
        version = f"{base.split('-', 1)[0]}-alpha.{build}"
        channel = "alpha"
    marketing = re.sub(r"[^0-9.].*$", "", version) or "0.0.0"
    return {"version": version, "marketing": marketing, "build": build, "channel": channel}


def pages_site(repository: str) -> str:
    """GitHub Pages 的地址：appcast 和更新包都放在这里。主机名用小写。"""
    owner, _, name = repository.partition("/")
    return f"https://{owner.lower()}.github.io/{name}"


# ---------------------------------------------------------------- 在 Actions 里运行


def git_changed(base: str, head: str) -> Optional[List[str]]:
    if not base or set(base) == {"0"}:
        return None
    result = subprocess.run(
        ["git", "diff", "--name-only", base, head], cwd=ROOT, capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"::warning::git diff {base} {head} 失败：{result.stderr.strip()}", file=sys.stderr)
        return None
    return [line for line in result.stdout.splitlines() if line]


def summary(result: Plan, info: Dict[str, str]) -> str:
    lines = ["## 本次运行的计划", ""]
    lines.append("| job | 跑 |")
    lines.append("| --- | --- |")
    for job in JOBS:
        lines.append(f"| {job} | {'✓' if job in result.jobs else '—'} |")
    lines += ["", f"发布：**{result.publish}**　版本：`{info['version']}`（build `{info['build']}`）", ""]
    if result.reasons:
        lines += ["原因：", ""] + [f"- {reason}" for reason in result.reasons[:40]]
        if len(result.reasons) > 40:
            lines.append(f"- ……另外 {len(result.reasons) - 40} 条")
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写 GITHUB_OUTPUT")
    parser.add_argument("--changed", nargs="*", help="直接给出改动的文件（本地试规则用）")
    args = parser.parse_args(argv)

    env = os.environ
    event = env.get("EVENT", "push")
    ref = env.get("REF", "refs/heads/local")
    if args.changed is not None:
        changed: Optional[List[str]] = args.changed
    elif event == "pull_request":
        changed = git_changed(env.get("BASE", ""), env.get("SHA", "HEAD"))
    else:
        changed = git_changed(env.get("BEFORE", ""), env.get("SHA", "HEAD"))
    try:
        result = plan(event, ref, changed, env.get("MESSAGE", ""), env.get("JOBS_INPUT", ""))
    except ValueError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    info = versions(ref, _dt.datetime.now(_dt.timezone.utc), source_version())
    info["site"] = pages_site(env.get("GITHUB_REPOSITORY", "owner/repo"))
    outputs = {**result.outputs(), **info}

    text = summary(result, info)
    if args.dry_run or not env.get("GITHUB_OUTPUT"):
        print(text)
        for key, value in outputs.items():
            print(f"{key}={value}")
        return 0
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            handle.write(f"{key}={value}\n")
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
