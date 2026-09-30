"""aiohttp 服务端：静态页面、描述文件下载与 WebSocket 同步通道。"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional

from aiohttp import web
from inksync import DefaultPolicy, FileStorage, Principal, Spaces, mount, serve_sdk
from inksync.hub import Hub as CoreHub
from inksync import server as inksync_server
from inksync.server import MAX_WS_MESSAGE  # noqa: F401

from . import __version__, ipadshell, models, netinfo, profile, resources, updater
from .config import REMOTE_PERMISSIONS, Config
from .hub import Hub, perms_of
from .store import BoardStore

log = logging.getLogger(__name__)

WEB_DIR = resources.web_dir()
MAX_THUMB_BYTES = 512 * 1024
# 一次录制几千条事件，一条一百来字节；给到 32 MB 足够长时间连续录
MAX_RECORDING_BYTES = 32 * 1024 * 1024
# recordings/ 里最多留几份、总共多大；超出时删掉最旧的
MAX_RECORDINGS = 50
MAX_RECORDINGS_BYTES = 256 * 1024 * 1024
MAX_DOC_BYTES = 256 * 1024 * 1024
# 同时最多渲染两页：渲染走线程池，再多也只是互相抢 CPU。
RENDER_LIMIT = 2

# aiohttp 的类型安全键，避免字符串键的命名冲突。
CONFIG_KEY: "web.AppKey[Config]" = web.AppKey("config")
HUB_KEY: "web.AppKey[Hub]" = web.AppKey("hub")
SPACES_KEY: "web.AppKey[Spaces]" = web.AppKey("spaces")
RENDER_KEY: "web.AppKey[asyncio.Semaphore]" = web.AppKey("render_lock")


def server_info(request: web.Request) -> Dict[str, Any]:
    """握手时发给客户端的服务端信息。

    版本号由服务端给，不靠 pywebview 那条本地接口：那边取到的只在 Mac 窗口里有，
    而且每来一条 switch / sync 都要重新取一次。
    """
    config: Config = request.app[CONFIG_KEY]
    return {
        "version": __version__,
        "hostname": netinfo.local_hostname(),
        "port": config.port,
        "urls": netinfo.candidate_urls(config.port),
        "data_dir": str(config.data_dir),
    }


def permissions(request: web.Request) -> FrozenSet[str]:
    """这个请求被允许做哪几件事。

    本机全给；局域网上的别的设备默认什么都不给，只能写字——换白板、改设置、
    导出都走不通。判断看的是 TCP 对端地址，不是 ``?role=`` 或者握手里那个
    role：那两样客户端想填什么填什么。要放开哪一项，在 Mac 的白板设置里
    一项一项开，见 ``config.REMOTE_PERMISSIONS``。

    检查更新和选存储目录不在这里：它们走 pywebview 的本地接口，别的设备本来
    就够不着。
    """
    if netinfo.is_local_request(request):
        return frozenset(REMOTE_PERMISSIONS)
    config: Config = request.app[CONFIG_KEY]
    return frozenset(name for name, on in config.remote_permissions.items() if on)


def detect_role(user_agent: str, override: Optional[str] = None) -> str:
    """区分访问设备：Mac 端拿到完整 GUI，iPad 端只有书写界面。"""
    if override in ("mac", "ipad"):
        return override
    ua = (user_agent or "").lower()
    if "ipad" in ua or "iphone" in ua or "ipod" in ua:
        return "ipad"
    return "mac"


def authenticate(request: web.Request) -> Principal:
    """连接的身份：是否本机，以及四项权限（docs/protocol.md「权限」）。"""
    return Principal(
        id=None,
        local=netinfo.is_local_request(request),
        address=getattr(request, "remote", None) or "",
        attrs={"perms": permissions(request)},
    )


class AppPolicy(DefaultPolicy):
    """已安装应用的空间：能写字就能打开和新建（有速率限制），改元数据和解除只读要「管理白板」。"""

    def can_edit_meta(self, who, meta, patch):
        return "manage" in perms_of(who)

    def can_unlock(self, who, meta):
        return "manage" in perms_of(who)

    def create_limit(self, who):
        return None if "manage" in perms_of(who) else (60, 60.0)


def space_factory(config: Config, hub: Hub):
    """空串是用户自己的白板；已安装应用的名字各是一个空间（spaces/<应用名>/）。"""

    def factory(name: str):
        if name == "":
            return hub.core
        if not models.APP_RE.match(name) or name not in list_apps(config):
            return None
        return CoreHub(FileStorage(config.data_dir / "spaces" / name), policy=AppPolicy())

    return factory


async def refresh_permissions(app: web.Application) -> None:
    """Mac 上改了其他设备的权限：已连接的设备立即按新设置生效，界面入口随之更新。"""
    hub: Hub = app[HUB_KEY]
    changed = await hub.core.reauthenticate(authenticate)
    await hub.send_perms(changed)
    for name, space in app[SPACES_KEY].hubs().items():
        if name:
            await space.reauthenticate(authenticate)


def require(request: web.Request, permission: str) -> None:
    if permission not in permissions(request):
        raise web.HTTPForbidden(text="这台设备没有这个权限")


# --------------------------------------------------------------------- 页面

async def handle_index(request: web.Request) -> web.Response:
    role = detect_role(request.headers.get("User-Agent", ""), request.query.get("role"))
    # Mac 上设了「iPad 首页」时，iPad（外壳或主屏图标）打开就进入那个应用。
    # 带 ?home=… 的地址不跳转，应用靠它回到白板。
    config: Config = request.app[CONFIG_KEY]
    home = config.ipad_home
    if role == "ipad" and home and "home" not in request.query and home in list_apps(config):
        raise web.HTTPFound(f"/apps/{home}/")
    # 界面按这份清单决定露出哪些管理入口；真正的拦截在服务端，这里只是别画出来。
    html = (WEB_DIR / "index.html").read_text("utf-8")
    html = html.replace("{{ROLE}}", role)
    html = html.replace("{{PERMS}}", " ".join(sorted(permissions(request))))
    # 每个客户端都要拿得到，所以写在页面上而不是放进 /api/info——那个要 manage 权限，
    # iPad 通常没有
    html = html.replace("{{BUILD}}", running_build())
    return web.Response(
        text=html,
        content_type="text/html",
        headers={"Cache-Control": "no-cache"},
    )


async def handle_profile(request: web.Request) -> web.Response:
    config: Config = request.app[CONFIG_KEY]
    host = request.query.get("host") or netinfo.local_hostname()
    url = f"http://{host}:{config.port}/"
    return web.Response(
        body=profile.build_profile(url),
        content_type="application/x-apple-aspen-config",
        headers={
            "Content-Disposition": 'attachment; filename="whiteboard.mobileconfig"',
            "Cache-Control": "no-store",
        },
    )


# ------------------------------------------------------------- iPad 外壳

def _shell_host(request: web.Request) -> str:
    """安装页上写的主机名，取值与描述文件相同。"""
    return request.query.get("host") or netinfo.local_hostname()


async def handle_ipad_page(request: web.Request) -> web.Response:
    """外壳的安装页：安装外壳，或者把这台 Mac 的地址交给已经装好的外壳。"""
    config: Config = request.app[CONFIG_KEY]
    page = ipadshell.install_page(
        _shell_host(request), config.port, f"https://github.com/{updater.REPO}/releases"
    )
    return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-store"})


async def handle_ipad_version(request: web.Request) -> web.Response:
    """外壳每次启动和回到前台时来问：Mac 上带的外壳是哪个版本。"""
    return web.json_response(ipadshell.version_info(), headers={"Cache-Control": "no-store"})


async def handle_ipad_ipa(request: web.Request) -> web.StreamResponse:
    path = ipadshell.ipa_path()
    if path is None:
        raise web.HTTPNotFound(text="这个版本没有附带外壳")
    return web.FileResponse(
        path,
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Disposition": f'attachment; filename="{ipadshell.IPA_NAME}"',
            "Cache-Control": "no-store",
        },
    )


async def handle_icon(request: web.Request) -> web.Response:
    return web.Response(body=profile.icon_png(180), content_type="image/png")


def running_build() -> str:
    """当前跑的是哪一份代码。

    打包之后的 app 不是 git 仓库，显示版本号——平时就是这一种；源码运行时从
    git 读分支名和短 commit。诊断面板上显示这一行，是因为截图里看不出跑的是
    哪个版本，对着一张图讨论问题，先得确定两边说的是同一份代码。
    """
    try:
        root = Path(__file__).resolve().parent.parent
        run = lambda *a: subprocess.run(
            a, cwd=root, capture_output=True, text=True, timeout=2
        ).stdout.strip()
        branch = run("git", "rev-parse", "--abbrev-ref", "HEAD")
        commit = run("git", "rev-parse", "--short", "HEAD")
        if branch and commit:
            return f"{branch}@{commit}"
    except Exception:
        pass
    return __version__


async def handle_info(request: web.Request) -> web.Response:
    require(request, "manage")
    config: Config = request.app[CONFIG_KEY]
    hub: Hub = request.app[HUB_KEY]
    return web.json_response(
        {
            "build": running_build(),
            "hostname": netinfo.local_hostname(),
            "port": config.port,
            "urls": netinfo.candidate_urls(config.port),
            "data_dir": str(config.data_dir),
            "current": hub.current_id,
            "clients": [
                {"id": c.id, "role": c.device, "since": c.connected_at}
                for c in hub.clients.values()
            ],
        }
    )


async def _read_body(request: web.Request, limit: int) -> bytes:
    """按块读完请求体。``StreamReader.read(n)`` 只保证「至多 n 字节」，
    大文件必须自己循环，否则会读成半截。"""
    chunks = []
    total = 0
    while True:
        chunk = await request.content.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise web.HTTPRequestEntityTooLarge(max_size=limit, actual_size=total)
        chunks.append(chunk)
    return b"".join(chunks)


async def handle_debug(request: web.Request) -> web.Response:
    """诊断模式下客户端上报的卡顿数据，直接打到服务端日志里。

    iPad 上看不到开发者工具，这样用户把 Mac 终端里的输出贴出来就够定位了。
    """
    try:
        payload = await request.json()
    except (ValueError, TypeError):
        raise web.HTTPBadRequest()
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest()
    fields = {k: payload[k] for k in list(payload)[:20] if isinstance(k, str)}
    log.warning("[诊断] %s", json.dumps(fields, ensure_ascii=False)[:1000])
    return web.json_response({"ok": True})


async def handle_recording(request: web.Request) -> web.Response:
    """iPad 上录下来的原始输入，存到 Mac 的数据目录里。

    和 /api/debug 一个道理：iPad 上够不着控制台也够不着文件系统，录像得有地方落。
    存成文件而不是打日志，是因为一次录制有几千条事件，日志装不下也没法回放。
    """
    body = await _read_body(request, MAX_RECORDING_BYTES)
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        raise web.HTTPBadRequest()
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise web.HTTPBadRequest()
    hub: Hub = request.app[HUB_KEY]
    folder = hub.store.data_dir / "recordings"
    folder.mkdir(parents=True, exist_ok=True)
    name = str(payload.get("name") or "")
    safe = "".join(ch for ch in name if ch.isalnum() or ch in "-_")[:40]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = folder / f"{stamp}{'-' + safe if safe else ''}.json"
    path.write_bytes(body)
    prune_recordings(folder)
    log.warning("[录制] %s，%d 条事件", path, len(payload["events"]))
    return web.json_response({"ok": True, "path": str(path), "events": len(payload["events"])})


def prune_recordings(folder: Path) -> None:
    """录像只留最近的一些：任何设备都能上传录像，不限的话可以把磁盘写满。"""
    files = sorted(folder.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    total = 0
    for index, item in enumerate(files):
        total += item.stat().st_size
        # 最新的那一份（刚上传的）总是留着，哪怕它自己就超过了总大小
        if index > 0 and (index >= MAX_RECORDINGS or total > MAX_RECORDINGS_BYTES):
            item.unlink(missing_ok=True)


async def handle_boards(request: web.Request) -> web.Response:
    require(request, "manage")
    hub: Hub = request.app[HUB_KEY]
    payload = hub.boards_payload()
    return web.json_response({**payload, "current": hub.current_id})


_BLANK_THUMB = profile._png(3, 2, [bytearray(b"\xff" * 12) for _ in range(2)])


async def handle_thumb_get(request: web.Request) -> web.StreamResponse:
    require(request, "manage")  # 缩略图只有白板选择界面在用
    hub: Hub = request.app[HUB_KEY]
    board_id = request.match_info["board_id"]
    path = hub.store.thumb_path(board_id)
    if not path.exists():
        thumb = await _doc_thumb(request.app, board_id)
        if thumb is not None:
            body, content_type = thumb
            return web.Response(
                body=body, content_type=content_type, headers={"Cache-Control": "no-cache"}
            )
        # 还没生成缩略图的白板返回一张空白图，界面上就是一块空白板。
        return web.Response(
            body=_BLANK_THUMB, content_type="image/png", headers={"Cache-Control": "no-store"}
        )
    return web.FileResponse(path, headers={"Cache-Control": "no-cache"})


async def _doc_thumb(app: web.Application, board_id: str):
    """文档板没有上传过缩略图时，直接拿原件首页当封面。"""
    from . import docs

    hub: Hub = app[HUB_KEY]
    meta = hub.store.get_meta(board_id)
    if not meta or models.doc_of(meta) is None:
        return None
    path = hub.store.doc_path(board_id)
    if path is None:
        return None
    try:
        async with app[RENDER_KEY]:
            return await asyncio.to_thread(docs.render_page, path, 0, 420)
    except Exception as exc:  # noqa: BLE001 - 封面画不出来不影响选板
        log.warning("文档板封面渲染失败 %s：%s", board_id, exc)
        return None


async def handle_thumb_post(request: web.Request) -> web.Response:
    """Mac 端在切换 / 关闭白板时上传缩略图，用于无文字的白板选择界面。"""
    require(request, "manage")
    hub: Hub = request.app[HUB_KEY]
    board_id = request.match_info["board_id"]
    if not hub.core.exists(board_id):
        raise web.HTTPNotFound()
    body = await _read_body(request, MAX_THUMB_BYTES)
    try:
        hub.store.save_thumb(board_id, body)
    except (ValueError, OSError) as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc
    return web.json_response({"ok": True})


# ------------------------------------------------------------- 嵌入与应用

def apps_dir(config: Config) -> Path:
    """其他应用的静态文件：存储目录下的 apps/，每个应用一个文件夹。"""
    return config.data_dir / "apps"


def list_apps(config: Config) -> list:
    """装好的应用：apps/ 下名字合法、带 index.html 的文件夹。"""
    root = apps_dir(config)
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return []
    return [
        entry.name
        for entry in entries
        if entry.is_dir() and models.APP_RE.match(entry.name) and (entry / "index.html").is_file()
    ]


async def handle_sdk(request: web.Request) -> web.Response:
    """嵌入用的手写板模块（1.0.1 的地址）。跳转到 inksync 提供的入口。"""
    raise web.HTTPFound("/inksync/inkpad.js")


async def handle_apps(request: web.Request) -> web.Response:
    """已装的应用和当前的 iPad 首页。只是名字，不需要权限。"""
    config: Config = request.app[CONFIG_KEY]
    return web.json_response({"apps": list_apps(config), "ipad_home": config.ipad_home})


async def handle_space_boards(request: web.Request) -> web.Response:
    """``/api/spaces/<应用>/boards?offset=&limit=``：某个应用空间里的白板，按更新时间从新到旧。"""
    require(request, "manage")
    name = request.match_info["name"]
    space = request.app[SPACES_KEY].get(name) if name else None
    if space is None:
        raise web.HTTPNotFound()
    try:
        offset = max(0, int(request.query.get("offset", "0")))
        limit = max(1, min(500, int(request.query.get("limit", "100"))))
    except ValueError:
        raise web.HTTPBadRequest()
    return web.json_response(
        {"boards": space.list_boards(offset=offset, limit=limit), "total": space.count()}
    )


async def handle_app_file(request: web.Request) -> web.StreamResponse:
    """``/apps/<应用>/<路径>``：应用的静态文件。目录返回其中的 index.html。"""
    config: Config = request.app[CONFIG_KEY]
    name = request.match_info["name"]
    if not models.APP_RE.match(name):
        raise web.HTTPNotFound()
    root = (apps_dir(config) / name).resolve()
    tail = request.match_info.get("tail", "")
    if not tail and not request.path.endswith("/"):
        raise web.HTTPFound(f"/apps/{name}/")
    target = (root / tail).resolve()
    # 不能用 .. 或符号链接跑出这个应用自己的文件夹
    if target != root and root not in target.parents:
        raise web.HTTPNotFound()
    if target.is_dir():
        target = target / "index.html"
    if not target.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(target, headers={"Cache-Control": "no-cache"})


# ------------------------------------------------------------- 文档板（beta）

async def handle_doc_upload(request: web.Request) -> web.Response:
    """上传一份 PDF / 图片，新建文档板并切过去。"""
    require(request, "manage")  # 建板算管理操作
    hub: Hub = request.app[HUB_KEY]
    filename = request.query.get("name") or request.headers.get("X-Filename") or ""
    # 在某个文件夹里导入的原件，新建出来的文档板就落在那个文件夹里
    folder = models.sanitize_folder(request.query.get("folder"))
    body = await _read_body(request, MAX_DOC_BYTES)
    try:
        meta = await _create_doc(hub, body, filename, folder)
    except OpenFileError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc
    return web.json_response({"board": meta})


class OpenFileError(Exception):
    """文件打不开或不能建板；消息直接给用户看。"""


async def _create_doc(hub: Hub, body: bytes, filename: str, folder: str) -> Dict[str, Any]:
    """由一份 PDF / 图片新建文档板，放进 ``folder``（存在的话），并让所有设备切过去。"""
    from . import docs

    try:
        # 读 PDF 可能要好一会儿，放到工作线程；建板要改索引和 hub 的状态，
        # 必须回到事件循环上做，否则会和自动保存、别的连接同时改同一份数据
        prepared = await asyncio.to_thread(hub.store.prepare_doc, body, filename)
        meta = await hub.add_doc(prepared, folder)
    except docs.DocError as exc:
        log.warning("导入文档失败：%s", exc)
        raise OpenFileError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - 坏文件不该把服务端带崩
        log.exception("导入文档出错")
        raise OpenFileError("文件读不出来") from exc
    await hub.broadcast_switch()
    return meta


async def _file_and_switch(hub: Hub, meta: Dict[str, Any], folder: str) -> Dict[str, Any]:
    if folder and folder in hub.store.folders():
        await hub.move_board(meta["id"], folder)
        meta = hub.core.board_meta(meta["id"]) or meta
    await hub.broadcast_switch()
    return meta


async def open_local_file(hub: Hub, path: Path, folder: str = "") -> Dict[str, Any]:
    """Mac 上用「打开方式」或双击打开的本地文件。

    * PDF / 图片：新建文档板，和拖进窗口相同；
    * 本存储目录里的 ``.wbz``：切到那块白板；
    * 其他 ``.wbz``（例如备份）：复制成一块新白板。

    新建的白板放进 ``folder``。出错时抛 :class:`OpenFileError`。
    """
    from . import docs

    folder = models.sanitize_folder(folder)
    suffix = path.suffix.lower()
    if suffix in docs.SUFFIXES:
        try:
            size = path.stat().st_size
            if size > MAX_DOC_BYTES:
                raise OpenFileError(f"文件太大（上限 {MAX_DOC_BYTES // (1024 * 1024)} MB）")
            body = await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            raise OpenFileError(f"文件打不开：{exc.strerror or exc}") from exc
        return await _create_doc(hub, body, path.name, folder)
    if suffix == ".wbz":
        own = hub.store.board_id_of(path)
        if own is not None:
            if not await hub.select_board(own):
                await hub.broadcast_switch()
            return hub.core.board_meta(own) or {}
        try:
            meta = await hub.import_board_file(path)
        except ValueError as exc:  # BoardFileError
            log.warning("导入白板文件失败：%s", exc)
            raise OpenFileError(str(exc)) from exc
        return await _file_and_switch(hub, meta, folder)
    raise OpenFileError(f"不支持的文件类型：{suffix or '无扩展名'}")


async def handle_doc_page(request: web.Request) -> web.StreamResponse:
    """渲染文档板的某一页。原件不会变，所以可以长期缓存。"""
    from . import docs

    hub: Hub = request.app[HUB_KEY]
    board_id = request.match_info["board_id"]
    # 只能写字的设备只看得到正在用的那块白板的页面；别的文档板要「管理白板」权限
    if board_id != hub.current_id:
        require(request, "manage")
    meta = hub.store.get_meta(board_id)
    if not meta or models.doc_of(meta) is None:
        raise web.HTTPNotFound()
    path = hub.store.doc_path(board_id)
    if path is None:
        raise web.HTTPNotFound(text="原件已丢失")
    try:
        index = int(request.match_info["index"])
        width = int(request.query.get("w", "1200"))
    except ValueError:
        raise web.HTTPBadRequest()

    width = max(docs.MIN_RENDER_WIDTH, min(docs.MAX_RENDER_WIDTH, width))
    etag = f'"{board_id}-{index}-{width}"'
    if request.headers.get("If-None-Match") == etag:
        return web.Response(status=304, headers={"ETag": etag})

    try:
        async with request.app[RENDER_KEY]:
            body, content_type = await asyncio.to_thread(docs.render_page, path, index, width)
    except docs.DocError as exc:
        raise web.HTTPNotFound(text=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("渲染文档页失败")
        raise web.HTTPInternalServerError(text="渲染失败") from exc
    return web.Response(
        body=body,
        content_type=content_type,
        headers={"ETag": etag, "Cache-Control": "private, max-age=31536000, immutable"},
    )


async def handle_doc_export(request: web.Request) -> web.StreamResponse:
    """把笔迹合进原件导出；PDF 走矢量叠加，图片按原分辨率栅格化。"""
    from . import docs

    require(request, "export")  # 导出能把任意一块白板整份取走
    hub: Hub = request.app[HUB_KEY]
    board_id = request.match_info["board_id"]
    meta = hub.core.board_meta(board_id)
    if not meta or models.doc_of(meta) is None:
        raise web.HTTPNotFound()
    path = hub.store.doc_path(board_id)
    if path is None:
        raise web.HTTPNotFound(text="原件已丢失")

    strokes = await hub.strokes_of(board_id)
    name = docs.default_export_name(meta)
    out_dir = Path(tempfile.mkdtemp(prefix="whiteboard-export-"))
    out = out_dir / name
    try:
        await asyncio.to_thread(docs.export, path, strokes, out)
        body = out.read_bytes()
    except docs.DocError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("导出文档失败")
        raise web.HTTPInternalServerError(text="导出失败") from exc
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)

    quoted = urllib.parse.quote(name)
    log.info("导出文档板 %s：%d 笔，%.1f KB", board_id, len(strokes), len(body) / 1024)
    return web.Response(
        body=body,
        content_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quoted}",
            "Cache-Control": "no-store",
        },
    )


# --------------------------------------------------------------------- 应用

@web.middleware
async def revalidate_static(request: web.Request, handler):
    """前端文件必须每次回源确认。

    否则 Safari 会按启发式规则把 js / css 缓存住，Mac 上更新了代码，
    iPad 的主屏图标点开还是旧的。局域网里多一次 304 的开销可以忽略。
    """
    response = await handler(request)
    if request.path.startswith(("/static/", "/apps/")):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


def create_app(config: Config, store: Optional[BoardStore] = None) -> web.Application:
    store = store or BoardStore(config.data_dir)
    app = web.Application(
        client_max_size=MAX_THUMB_BYTES + 4096, middlewares=[revalidate_static]
    )
    app[CONFIG_KEY] = config
    # 前端据构建号判断页面是不是旧的（服务端升级之后 iPad 上还开着的页面会重新载入）
    inksync_server.BUILD = running_build()
    hub = Hub(store)
    app[HUB_KEY] = hub
    app[RENDER_KEY] = asyncio.Semaphore(RENDER_LIMIT)

    app.router.add_get("/", handle_index)
    # authenticate 每次按名字取，而不是在这里存下函数本身：测试会替换 permissions
    spaces = mount(
        app,
        Spaces(space_factory(config, hub)),
        path="/ws",
        authenticate=lambda request: authenticate(request),
        info=server_info,
    )
    app[SPACES_KEY] = spaces
    app.router.add_get("/profile.mobileconfig", handle_profile)
    app.router.add_get("/icon.png", handle_icon)
    app.router.add_get("/ipad", handle_ipad_page)
    app.router.add_get("/ipad/version", handle_ipad_version)
    app.router.add_get(f"/ipad/{ipadshell.IPA_NAME}", handle_ipad_ipa)
    app.router.add_get("/api/info", handle_info)
    app.router.add_get("/api/boards", handle_boards)
    app.router.add_post("/api/debug", handle_debug)
    app.router.add_post("/api/recording", handle_recording)
    app.router.add_get("/api/thumb/{board_id}", handle_thumb_get)
    app.router.add_post("/api/thumb/{board_id}", handle_thumb_post)
    app.router.add_post("/api/doc", handle_doc_upload)
    app.router.add_get("/api/doc/{board_id}/{index}", handle_doc_page)
    app.router.add_get("/api/export/{board_id}", handle_doc_export)
    app.router.add_get("/sdk/inkpad.js", handle_sdk)
    app.router.add_get("/api/apps", handle_apps)
    app.router.add_get("/api/spaces/{name}/boards", handle_space_boards)
    app.router.add_get("/apps/{name}", handle_app_file)
    app.router.add_get("/apps/{name}/{tail:.*}", handle_app_file)
    app.router.add_static("/static/", WEB_DIR / "static", name="static")
    serve_sdk(app, prefix="/inksync/")

    async def _on_startup(_app: web.Application) -> None:
        spaces.get("")  # 用户空间一直开着

    app.on_startup.append(_on_startup)
    return app
