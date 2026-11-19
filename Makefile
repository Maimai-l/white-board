# 本地和 CI 跑同一套命令：CI 的每个 job 调用这里同名的目标
# （docs/design/ci-and-updates.zh-CN.md 第 2.5 节）。
#
#   make deps    装测试依赖和 Chromium
#   make fast    不开浏览器的检查（checks + unit + js），一分钟以内
#   make test    全部测试（不含 webkit 和 iPad 外壳）
#   make e2e PYTEST_ARGS="-k sync -x"   给 pytest 加参数

PY ?= python3
PYTEST_ARGS ?=
REPORTS ?= reports

# iPad 上是 Safari（WebKit）：挑一组覆盖主要流程的用例再用 WebKit 跑一遍
WEBKIT_CASES = test_drawing_syncs_both_ways or test_undo_erase_and_clear \
  or test_touch_pans_by_default_and_only_pencil_draws \
  or test_strokes_survive_a_reload_without_id_collisions \
  or test_board_switch_and_settings_follow \
  or test_offline_drawing_is_flushed_after_server_restart \
  or test_picker_is_only_on_ipad or test_pencilkit_picker_drives_the_board \
  or test_undo_and_redo_round_trip or test_bite_syncs_and_undoes \
  or test_a_board_that_cannot_be_read_opens_read_only_until_unlocked \
  or test_text_fields_can_take_typing_on_the_ipad \
  or test_tapping_a_card_opens_that_board \
  or test_a_board_can_be_renamed_by_typing_on_the_ipad \
  or test_ipad_follows_a_sketch_and_returns \
  or test_sketch_page_starts_over_when_the_sketch_is_ended_elsewhere \
  or test_a_board_can_be_filed_and_unfiled_from_the_folder_dialog_on_the_ipad \
  or test_shortcuts_do_not_reach_the_board_while_typing

.PHONY: deps checks unit e2e webkit js inksync fast test plan

deps:
	$(PY) -m pip install -r requirements-test.txt
	$(PY) -m playwright install chromium

# 只开「真错误」类规则（语法错误、未定义的名字）；格式统一是另一条待办
checks:
	$(PY) -m ruff check --select E9,F63,F7,F82 .
	$(PY) -m pytest tests/test_release_notes.py tests/test_ci_plan.py -q $(PYTEST_ARGS)

unit:
	$(PY) -m pytest tests --ignore=tests/e2e -q --junitxml=$(REPORTS)/unit.xml $(PYTEST_ARGS)

e2e:
	$(PY) -m pytest tests/e2e -q --junitxml=$(REPORTS)/e2e.xml $(PYTEST_ARGS)

webkit:
	WB_BROWSER=webkit $(PY) -m pytest tests/e2e -q -k "$(WEBKIT_CASES)" --junitxml=$(REPORTS)/webkit.xml $(PYTEST_ARGS)

js:
	node --test tests/js/*.test.mjs

# inksync 单独安装、单独测：确认它不依赖白板应用（CI 里装的是 ./packages/inksync）
inksync:
	cd packages/inksync && $(PY) -m pytest tests -q --junitxml=../../$(REPORTS)/inksync.xml $(PYTEST_ARGS)

fast: checks unit js

test: checks unit js inksync e2e

# 看某些改动在 CI 里会跑哪些 job：make plan FILES="whiteboard/app.py docs/a.md"
plan:
	$(PY) tools/ci_plan.py --dry-run --changed $(FILES)
