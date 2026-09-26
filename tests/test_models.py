import re
from pathlib import Path

from whiteboard import models


def test_meta_is_sanitized():
    meta = models.sanitize_meta({"id": "abc", "background": "hack", "name": 5})
    assert meta["background"] == "grid"
    assert meta["name"] == ""
    assert meta["id"] == "abc"


def test_meta_drops_legacy_board_size():
    """老文件里可能还留着 cols / rows / unit：画布已经无限，直接丢掉。"""
    meta = models.sanitize_meta({"id": "abc", "cols": 3, "rows": 3, "unit": [1180, 820]})
    assert "cols" not in meta and "rows" not in meta and "unit" not in meta


def test_stroke_validation():
    good = models.sanitize_stroke(
        {"id": "a-1", "tool": "marker", "color": "#ABCDEF", "w": 4, "p": [0, 0, 0.5, 1, 1, 0.5]}
    )
    assert good["tool"] == "marker" and good["color"] == "#ABCDEF"
    assert models.sanitize_stroke({"id": "a b", "p": [0, 0, 0.5]}) is None
    assert models.sanitize_stroke({"id": "a", "p": [0, 0]}) is None
    assert models.sanitize_stroke({"id": "a", "p": [0, 0, float("nan")]}) is None
    assert models.sanitize_stroke("nope") is None


def test_stroke_defaults_and_limits():
    stroke = models.sanitize_stroke({"id": "a", "tool": "x", "w": 1e6, "p": [0, 0, 2]})
    assert stroke["tool"] == "pen"
    assert stroke["w"] == models.MAX_WIDTH
    assert stroke["color"].startswith("#")


def test_sanitize_ids_filters():
    assert models.sanitize_ids(["ok-1", 3, "bad id", None]) == ["ok-1"]
    assert models.sanitize_ids("nope") == []


def _client_mask_limit() -> int:
    """前端一条笔画最多挂多少段胶囊（stroke.js 的 MASK_LIMIT）。"""
    src = (Path(__file__).resolve().parent.parent
           / "whiteboard/web/static/js/stroke.js").read_text("utf-8")
    return int(re.search(r"export const MASK_LIMIT = (\d+)", src).group(1))


def test_mask_limit_leaves_room_for_what_the_client_can_send():
    """服务端的遮罩上限要能装下前端合法产出的遮罩，而且是同一个口径。

    以前服务端按「最多 64 条链、每条链最多 256 个点」截，前端按总段数管
    （MASK_LIMIT）。两个口径对不上，前端合法的遮罩到服务端会被悄悄截断，截完的
    结果既广播给对端也顺着回执盖回发送端自己——擦掉的墨过一会儿自己又回来一部分。
    真机录像里一次擦除攒出 113 条链，截到 64，丢掉 43%。
    """
    limit = _client_mask_limit()
    assert models.MAX_MASK_SEGMENTS >= limit

    # 两种极端形状：全是一段一条链，和全挤在一条链里。都得原样留下
    many = [[3.0, float(i), 0.0, float(i) + 1, 0.0] for i in range(limit)]
    assert models.sanitize_mask(many) == many

    one = [[3.0] + [v for i in range(limit + 1) for v in (float(i), 0.0)]]
    assert models.sanitize_mask(one) == one


def test_mask_budget_is_counted_in_segments():
    """上限按总段数算，不管遮罩是怎么分成链的。"""
    over = [[3.0, float(i), 0.0, float(i) + 1, 0.0]
            for i in range(models.MAX_MASK_SEGMENTS + 50)]
    kept = models.sanitize_mask(over)
    assert sum(max(1, (len(c) - 3) // 2) for c in kept) == models.MAX_MASK_SEGMENTS
    # 坏数据照旧整条丢掉，不是截一半
    assert models.sanitize_mask([[3.0, 0.0, 0.0, float("nan"), 0.0]]) == []
    assert models.sanitize_mask([[-1.0, 0.0, 0.0, 1.0, 0.0]]) == []
    assert models.sanitize_mask("nope") == []


def test_stroke_point_limit_matches_what_the_client_will_send():
    """前端切分超长笔画的阈值不能超过服务端肯收的上限。

    服务端对超长点列是**整条丢掉**，不是截断。两边对不上的话，画得够久的一笔
    本机看得见、对端和存档里没有，而且只有重新载入才看得出来——和遮罩被截断
    是同一类问题。
    """
    src = (Path(__file__).resolve().parent.parent
           / "whiteboard/web/static/js/stroke.js").read_text("utf-8")
    client = int(re.search(r"export const MAX_STROKE_POINTS = (\d+)", src).group(1))
    assert client <= models.MAX_POINTS_PER_STROKE
    # 正好卡在上限的一笔要收下
    at_limit = {"id": "a", "p": [0.0, 0.0, 0.5] * client}
    assert models.sanitize_stroke(at_limit) is not None
