"""其他应用：/apps/<应用>/ 的静态文件、/sdk/inkpad.js、iPad 首页和底图。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from aiohttp.test_utils import TestClient, TestServer

from whiteboard import models
from whiteboard.config import Config
from whiteboard.server import create_app, list_apps
from whiteboard.store import BoardStore


def run(coro):
    return asyncio.run(coro)


@asynccontextmanager
async def make_client(tmp_path):
    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"
    app_dir = config.data_dir / "apps" / "qb"
    app_dir.mkdir(parents=True)
    (app_dir / "index.html").write_text("<p>qb</p>", "utf-8")
    (app_dir / "img").mkdir()
    (app_dir / "img" / "q1.svg").write_text("<svg/>", "utf-8")
    (config.data_dir / "apps" / "empty").mkdir()  # 没有 index.html，不算应用
    (config.data_dir / "secret.txt").write_text("secret", "utf-8")
    client = TestClient(TestServer(create_app(config, BoardStore(config.data_dir))), )
    await client.start_server()
    try:
        yield client, config
    finally:
        await client.close()


def test_app_files_are_served_from_the_apps_folder(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, _config):
            response = await client.get("/apps/qb/")
            assert response.status == 200 and "qb" in await response.text()
            assert response.headers["Cache-Control"] == "no-cache"
            assert (await client.get("/apps/qb/img/q1.svg")).status == 200
            redirected = await client.get("/apps/qb", allow_redirects=False)
            assert redirected.status == 302 and redirected.headers["Location"] == "/apps/qb/"
            for path in ("/apps/qb/missing.js", "/apps/QB/", "/apps/empty/", "/apps/qb/../../secret.txt",
                         "/apps/qb/%2e%2e/%2e%2e/secret.txt"):
                assert (await client.get(path)).status == 404, path

    run(main())


def test_the_sdk_path_points_at_the_embed_module(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, _config):
            response = await client.get("/sdk/inkpad.js", allow_redirects=False)
            assert response.status == 302
            assert response.headers["Location"] == "/inksync/inkpad.js"
            module = await client.get("/sdk/inkpad.js")
            assert "export function createInkPad" in await module.text()

    run(main())


def test_the_ipad_home_redirects_only_the_ipad(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, config):
            assert (await (await client.get("/api/apps")).json()) == {"apps": ["qb"], "ipad_home": ""}
            assert (await client.get("/?role=ipad", allow_redirects=False)).status == 200

            config.ipad_home = "qb"
            ipad = await client.get("/?role=ipad", allow_redirects=False)
            assert ipad.status == 302 and ipad.headers["Location"] == "/apps/qb/"
            assert (await client.get("/?role=ipad&home=whiteboard", allow_redirects=False)).status == 200
            assert (await client.get("/?role=mac", allow_redirects=False)).status == 200

            # 应用被删掉之后不再跳转，免得 iPad 打开是一片 404
            config.ipad_home = "gone"
            assert (await client.get("/?role=ipad", allow_redirects=False)).status == 200

    run(main())


def test_list_apps_and_config_reject_bad_names(tmp_path):
    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"
    assert list_apps(config) == []
    try:
        config.ipad_home = "../x"
    except ValueError:
        pass
    else:
        raise AssertionError("不合法的应用名应当被拒绝")
    config.values["ipad_home"] = "Bad Name"
    assert config.ipad_home == ""


def test_image_layers_must_be_same_origin_paths():
    ok = models.sanitize_layers([{"src": "/apps/qb/img/q1.png", "x": 0, "y": 0, "width": 800}])
    assert ok == [{"src": "/apps/qb/img/q1.png", "x": 0.0, "y": 0.0, "width": 800.0}]
    for bad in (
        {"src": "https://example.com/a.png", "width": 800},
        {"src": "//example.com/a.png", "width": 800},
        {"src": "/apps/qb/../../config.json", "width": 800},
        {"src": "javascript:alert(1)", "width": 800},
        {"src": "/apps/qb/a.png", "width": 0},
        {"src": "/apps/qb/a.png", "width": "800"},
        {"src": "/apps/qb/a.png"},
        "nope",
    ):
        assert models.sanitize_layers([bad]) is None, bad
    # 1.0.x 的底图读进来是第一层
    meta = models.sanitize_meta({"id": "b1", "kind": "board", "underlay": {"src": "/apps/qb/a.png", "width": 500}})
    assert meta["layers"] == [{"src": "/apps/qb/a.png", "x": 0.0, "y": 0.0, "width": 500.0}]
    assert models.sanitize_meta({"id": "b1", "kind": "board", "underlay": {"src": "http://x", "width": 5}})["layers"] == []
