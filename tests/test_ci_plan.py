"""tools/ci_plan.py：哪些改动跑哪些 job（docs/design/ci-and-updates.zh-CN.md 第 2.1 节）。"""

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ci_plan", ROOT / "tools" / "ci_plan.py")
ci_plan = importlib.util.module_from_spec(spec)
sys.modules["ci_plan"] = ci_plan  # dataclass 要在 sys.modules 里找到所在的模块
spec.loader.exec_module(ci_plan)

ALL = set(ci_plan.JOBS)


def jobs(changed, event="push", ref="refs/heads/claude/x", message="", jobs_input=""):
    return ci_plan.plan(event, ref, changed, message, jobs_input).jobs


def test_docs_only_runs_just_the_checks():
    assert jobs(["docs/sketch.md", "README.zh-CN.md", "CHANGELOG.md", "GETTING-STARTED.txt"]) == {"checks"}


def test_python_app_code_runs_tests_and_packaging():
    assert jobs(["whiteboard/server.py"]) == {"checks", "unit", "e2e", "webkit", "package", "ipad"}


def test_front_end_code_also_runs_the_js_tests():
    assert "js" in jobs(["whiteboard/web/static/js/app.js"])
    assert {"js", "inksync"} <= jobs(["packages/inksync/inksync/web/renderer.js"])


def test_inksync_python_runs_its_standalone_job():
    assert "inksync" in jobs(["packages/inksync/inksync/hub.py"])
    assert "js" not in jobs(["packages/inksync/inksync/hub.py"])


def test_js_tests_alone_do_not_open_a_browser():
    assert jobs(["tests/js/stroke.test.mjs"]) == {"checks", "js"}


def test_e2e_tests_run_without_packaging():
    assert jobs(["tests/e2e/test_sync.py"]) == {"checks", "unit", "e2e", "webkit"}


def test_ipad_shell_alone_compiles_only_the_shell():
    assert jobs(["ipad/Whiteboard/MacAddress.swift"]) == {"checks", "ipad"}


def test_packaging_always_brings_the_ipad_shell():
    assert {"package", "ipad"} <= jobs(["packaging/whiteboard.spec"])


def test_changing_ci_itself_runs_everything():
    assert jobs([".github/workflows/ci.yml"]) == ALL
    assert jobs(["tools/ci_plan.py"]) == ALL
    assert jobs(["Makefile"]) == ALL


def test_unknown_paths_run_everything():
    assert jobs(["something/new.cfg"]) == ALL


def test_new_branch_without_a_previous_commit_runs_everything():
    assert jobs(None) == ALL


def test_dev_and_tags_always_run_everything():
    assert jobs(["docs/sketch.md"], ref="refs/heads/dev") == ALL
    assert jobs(["docs/sketch.md"], ref="refs/tags/v1.3.0") == ALL


def test_commit_message_can_pick_jobs():
    assert jobs(["whiteboard/server.py"], message="Fix x\n\n[ci: e2e, js]") == {"checks", "e2e", "js"}
    assert jobs(["docs/a.md"], message="Docs [ci: all]") == ALL


def test_manual_run_picks_jobs():
    assert jobs([], event="workflow_dispatch", jobs_input="webkit") == {"checks", "webkit"}
    assert jobs([], event="workflow_dispatch", jobs_input="") == ALL


def test_unknown_job_names_are_rejected():
    with pytest.raises(ValueError, match="nope"):
        jobs([], event="workflow_dispatch", jobs_input="e2e,nope")


def test_publish_mode_follows_the_ref():
    assert ci_plan.plan("push", "refs/heads/dev", []).publish == "alpha"
    assert ci_plan.plan("push", "refs/tags/v1.3.0", []).publish == "stable"
    # 预发布 tag 只给 alpha 通道，正式版用户收不到
    assert ci_plan.plan("push", "refs/tags/v1.3.0-rc.1", []).publish == "alpha"
    assert ci_plan.plan("push", "refs/heads/claude/x", ["packaging/x.py"]).publish == "dry-run"
    assert ci_plan.plan("push", "refs/heads/claude/x", ["docs/a.md"]).publish == "none"
    # PR 打出来的包也只演练，不发布
    assert ci_plan.plan("pull_request", "refs/pull/3/merge", ["whiteboard/a.py"]).publish == "dry-run"


def test_reasons_name_the_file_that_pulled_a_job_in():
    result = ci_plan.plan("push", "refs/heads/claude/x", ["docs/a.md", "whiteboard/web/static/js/ui.js"])
    assert any("whiteboard/web/static/js/ui.js" in reason and "js" in reason for reason in result.reasons)


NOW = dt.datetime(2026, 11, 19, 15, 30, 12, tzinfo=dt.timezone.utc)


def test_alpha_version_carries_the_build_time():
    info = ci_plan.versions("refs/heads/dev", NOW, "1.3.0")
    assert info == {
        "version": "1.3.0-alpha.20261119.153012",
        "marketing": "1.3.0",
        "build": "20261119.153012",
        "channel": "alpha",
    }


def test_tag_version_is_the_tag():
    info = ci_plan.versions("refs/tags/v1.3.0-rc.1", NOW, "1.3.0")
    assert info["version"] == "1.3.0-rc.1"
    assert info["marketing"] == "1.3.0"
    assert info["channel"] == "stable"


def test_build_numbers_grow_with_time():
    """Sparkle 只比 CFBundleVersion：后构建的必须更大，按数字逐段比较。"""
    earlier = ci_plan.versions("refs/heads/dev", NOW, "1.3.0")["build"]
    later = ci_plan.versions("refs/heads/dev", NOW + dt.timedelta(seconds=1), "1.3.0")["build"]
    next_day = ci_plan.versions("refs/heads/dev", NOW + dt.timedelta(hours=9), "1.3.0")["build"]

    def key(build):
        return tuple(int(part) for part in build.split("."))

    assert key(earlier) < key(later) < key(next_day)


def test_source_version_reads_the_package():
    from whiteboard import __version__

    assert ci_plan.source_version() == __version__


def test_pages_site_uses_a_lowercase_host():
    assert ci_plan.pages_site("Maimai-l/white-board") == "https://maimai-l.github.io/white-board"
