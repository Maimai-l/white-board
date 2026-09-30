"""最小的刷题服务端：只用 inksync 的公开接口，不依赖白板应用。

    python examples/qb-server/server.py            # http://<本机>.local:8900/
    iPad 外壳「来源」填 @qb-demo，外壳找到这台电脑并打开页面

演示的内容：

* 每道题一块白板，白板 id 带用户标识（``u<用户>-<题号>``）；
* 身份来自 Cookie（``/login?user=42`` 设置），规则只允许打开自己的白板；
* 题图是固定画布上的一个图片层，题号记在 ``data`` 里，分数 ``score`` 只能由服务端写；
* 批改接口：``hub.strokes`` 读笔画，``hub.edit_meta`` 写分数。
"""

from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web
from inksync import DefaultPolicy, FileStorage, Hub, Principal, mount, serve_sdk
from inksync.netinfo import advertise, is_local_request

ROOT = Path(__file__).resolve().parent
PORT = 8900
USER_COOKIE = "qb_user"
HUB_KEY: "web.AppKey[Hub]" = web.AppKey("hub")


def authenticate(request: web.Request) -> Principal:
    user = request.cookies.get(USER_COOKIE)
    return Principal(
        id=user if user and user.isdigit() else None,
        local=is_local_request(request),
        address=request.remote or "",
    )


class QbPolicy(DefaultPolicy):
    """只能打开和新建自己的白板（本机不限）；分数只能由服务端写。"""

    protected_data_keys = frozenset({"score"})

    def can_open(self, who, board_id, meta):
        return who.local or (who.id is not None and board_id.startswith(f"u{who.id}-"))

    def can_create(self, who, board_id, spec):
        return self.can_open(who, board_id, None)

    def can_edit_meta(self, who, meta, patch):
        # 学生可以在 data 里记自己的东西（例如「已提交」），分数由 protected_data_keys 挡住
        return True


async def login(request: web.Request) -> web.Response:
    user = request.query.get("user", "")
    if not user.isdigit():
        raise web.HTTPBadRequest(text="user 必须是数字")
    response = web.HTTPFound("/")
    response.set_cookie(USER_COOKIE, user, httponly=True, samesite="Strict")
    raise response


async def index(request: web.Request) -> web.StreamResponse:
    if not request.cookies.get(USER_COOKIE):
        raise web.HTTPFound("/login?user=1")
    return web.FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-cache"})


async def whoami(request: web.Request) -> web.Response:
    return web.json_response({"id": authenticate(request).id})


async def grade(request: web.Request) -> web.Response:
    """批改示例：本机调用，按笔画数给分（真实项目在这里接自己的批改流程）。"""
    if not is_local_request(request):
        raise web.HTTPForbidden()
    hub: Hub = request.app[HUB_KEY]
    board_id = request.match_info["board"]
    strokes = await hub.strokes(board_id)
    if strokes is None:
        raise web.HTTPNotFound()
    score = min(10, len(strokes))
    meta = await hub.edit_meta(board_id, {"data": {"score": score}})
    return web.json_response({"board": board_id, "strokes": len(strokes), "score": score, "meta": meta})


def create_app(data_dir: Path = ROOT / "data") -> web.Application:
    app = web.Application()
    hub = Hub(FileStorage(data_dir / "ink"), policy=QbPolicy())
    hub.on("saved", lambda board_id, meta: logging.getLogger("qb").info("已保存 %s", board_id))
    app[HUB_KEY] = hub
    mount(app, hub, path="/ws", authenticate=authenticate)
    serve_sdk(app, prefix="/inksync/")
    app.router.add_get("/", index)
    app.router.add_get("/login", login)
    app.router.add_get("/whoami", whoami)
    app.router.add_post("/grade/{board}", grade)
    app.router.add_static("/static/", ROOT / "static")
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    application = create_app()
    advertise(application, port=PORT, source="qb-demo", path="/")
    web.run_app(application, port=PORT)
