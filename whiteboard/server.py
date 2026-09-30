"""aiohttp 服务端：静态页面、描述文件下载与 WebSocket 同步通道。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional

from aiohttp import WSMsgType, web

from . import __version__, ipadshell, models, netinfo, profile, resources, updater
from .config import REMOTE_PERMISSIONS, Config
from .hub import Hub
from .store import BoardStore

log = logging.getLogger(__name__)

WEB_DIR = resources.web_dir()
MAX_WS_MESSAGE = 8 * 1024 * 1024
MAX_THUMB_BYTES = 512 * 1024
# 一次录制几千条事件，一条一百来字节；给到 32 MB 足够长时间连续录
MAX_RECORDING_BYTES = 32 * 1024 * 1024
MAX_DOC_BYTES = 256 * 1024 * 1024
# 同时最多渲染两页：渲染走线程池，再多也只是互相抢 CPU。
RENDER_LIMIT = 2
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")

# aiohttp 的类型安全键，避免字符串键的命名冲突。
CONFIG_KEY: "web.AppKey[Config]" = web.AppKey("config")
HUB_KEY: "web.AppKey[Hub]" = web.AppKey("hub")
RENDER_KEY: "web.AppKey[asyncio.Semaphore]" = web.AppKey("render_lock")


def detect_role(user_agent: str, override: Optional[str] = None) -> str:
    """区分访问设备：Mac 端拿到完整 GUI，iPad 端只有书写界面。"""
    if override in ("mac", "ipad"):
        return override
    ua = (user_agent or "").lower()
    if "ipad" in ua or "iphone" in ua or "ipod" in ua:
        return "ipad"
    return "mac"


def permissions(request: web.Request) -> FrozenSet[str]:
    """这个请求被允许做哪几件事。

    本机全给；局域网上的别的设备默认什么都不给，只能写字——换白板、改设置、
    导出都走不通。判断看的是 TCP 对端地址，不是 ``?role=`` 或者握手里那个
    role：那两样客户端想填什么填什么。要放开哪一项，在 Mac 的白板设置里
    一项一项开，见 ``config.REMOTE_PERMISSIONS``。

    检查更新和选存储目录不在这里：它们走 pywebview 的本地接口，别的设备本来
    就够不着。
    """
    if netinfo.is_own_address(request.remote):
        return frozenset(REMOTE_PERMISSIONS)
    config: Config = request.app[CONFIG_KEY]
    return frozenset(name for name, on in config.remote_permissions.items() if on)


def require(request: web.Request, permission: str) -> None:
    if permission not in permissions(request):
        raise web.HTTPForbidden(text="这台设备没有这个权限")


# --------------------------------------------------------------------- 页面

async def handle_index(request: web.Request) -> web.Response:
    role = detect_role(request.headers.get("User-Agent", ""), request.query.get("role"))
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
                {"id": c.id, "role": c.role, "since": c.connected_at}
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
    log.warning("[录制] %s，%d 条事件", path, len(payload["events"]))
    return web.json_response({"ok": True, "path": str(path), "events": len(payload["events"])})


async def handle_boards(request: web.Request) -> web.Response:
    require(request, "manage")
    hub: Hub = request.app[HUB_KEY]
    return web.json_response(
        {"boards": hub.store.list_metas(), "folders": hub.store.folders(), "current": hub.current_id}
    )


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
    if not meta or meta.get("kind") != "doc":
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
    if not hub.store.get_meta(board_id):
        raise web.HTTPNotFound()
    body = await _read_body(request, MAX_THUMB_BYTES)
    try:
        hub.store.save_thumb(board_id, body)
    except (ValueError, OSError) as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc
    return web.json_response({"ok": True})


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
        meta = hub.add_doc(prepared)
    except docs.DocError as exc:
        log.warning("导入文档失败：%s", exc)
        raise OpenFileError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - 坏文件不该把服务端带崩
        log.exception("导入文档出错")
        raise OpenFileError("文件读不出来") from exc
    return await _file_and_switch(hub, meta, folder)


async def _file_and_switch(hub: Hub, meta: Dict[str, Any], folder: str) -> Dict[str, Any]:
    if folder and folder in hub.store.folders():
        hub.move_board(meta["id"], folder)
        meta = hub.store.get_meta(meta["id"]) or meta
    await hub.broadcast({"t": "switch", **hub.snapshot()})
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
            hub.select_board(own)
            await hub.broadcast({"t": "switch", **hub.snapshot()})
            return hub.store.get_meta(own) or {}
        try:
            # 要改索引和 hub 的状态，留在事件循环线程上；文件通常只有几百 KB
            meta = hub.import_board_file(path)
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
    meta = hub.store.get_meta(board_id)
    if not meta or meta.get("kind") != "doc":
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
    meta = hub.store.get_meta(board_id)
    if not meta or meta.get("kind") != "doc":
        raise web.HTTPNotFound()
    path = hub.store.doc_path(board_id)
    if path is None:
        raise web.HTTPNotFound(text="原件已丢失")

    strokes = hub.strokes_of(board_id)
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


# ---------------------------------------------------------------- WebSocket

async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=20.0, max_msg_size=MAX_WS_MESSAGE)
    await ws.prepare(request)

    hub: Hub = request.app[HUB_KEY]
    # 权限在握手之前就按对端地址定下来，之后客户端说什么都改不了它。
    session = _Session(request.app, ws, allowed=permissions(request))
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                payload = json.loads(msg.data)
            except ValueError:
                continue
            if isinstance(payload, dict):
                await session.safe_dispatch(payload)
    except asyncio.CancelledError:
        raise
    finally:
        await session.close()
        log.info("连接断开：%s（在线 %d）", session.client_id, len(hub.clients))
    return ws


class _Session:
    """一条 WebSocket 连接的协议处理。"""

    def __init__(
        self,
        app: web.Application,
        ws: web.WebSocketResponse,
        allowed: FrozenSet[str] = frozenset(REMOTE_PERMISSIONS),
    ):
        self.app = app
        self.hub: Hub = app[HUB_KEY]
        self.ws = ws
        self.allowed = allowed
        self.client = None
        self.client_id = "-"

    def _may(self, permission: str) -> bool:
        """能不能做这件事，只看这条连接拿到的权限。

        权限在连接建立时按 TCP 对端地址定死（见 ``permissions``），握手里报的
        role 不参与判断——那个客户端想填什么填什么。以前这里对「管理白板」和
        「设置白板」多一道 ``role == "mac"``，因为那两项当时只有 Mac 那套界面里
        有入口；现在触摸设备的右上角也有这一组，再拦着就是 Mac 放开了权限、
        iPad 上点了却没反应。
        """
        return permission in self.client.allowed

    async def safe_dispatch(self, msg: Dict[str, Any]) -> None:
        """处理一条消息；出了任何没想到的错只记日志，连接不断。

        op 出错时照样回执：客户端的待发队列存在 IndexedDB 里，收不到回执就会在
        重连后原样重发。要是这条消息每次都让连接断开，客户端就会一直断、一直重发，
        刷新页面也出不来。
        """
        try:
            await self.dispatch(msg)
        except Exception:  # noqa: BLE001 - 一条坏消息不能断开连接
            log.exception("处理消息出错（%s）：%.200s", self.client_id, json.dumps(msg, default=str))
            if msg.get("t") == "op" and self.client is not None:
                try:
                    seq = self.hub.board(self.hub.board_of(self.client)).seq
                except Exception:  # noqa: BLE001 - 出错的可能正是读白板这一步
                    seq = 0
                await self.client.send({"t": "ack", "cid": msg.get("cid"), "seq": seq})

    async def dispatch(self, msg: Dict[str, Any]) -> None:
        kind = msg.get("t")
        if kind == "hello":
            await self._hello(msg)
            return
        if self.client is None:
            return  # 未握手前只接受 hello
        handler = {
            "op": self._op,
            "live": self._live,
            "sel": self._select,
            "newboard": self._new_board,
            "delboard": self._del_board,
            "rename": self._rename,
            "folder": self._folder,
            "order": self._order,
            "newfolder": self._new_folder,
            "delfolder": self._del_folder,
            "renamefolder": self._rename_folder,
            "unlock": self._unlock,
            "ping": self._ping,
        }.get(kind)
        if handler is not None:
            await handler(msg)

    # ------------------------------------------------------------- 握手

    async def _hello(self, msg: Dict[str, Any]) -> None:
        if self.client is not None:
            return
        from .hub import Client  # 局部导入避免循环

        client_id = msg.get("client")
        if not isinstance(client_id, str) or not _CLIENT_ID_RE.match(client_id):
            client_id = models.new_id()
        role = detect_role("", msg.get("role"))
        pinned = None
        if msg.get("pin") is not None:
            pinned = self._pin(msg.get("pin"))
            if pinned is None:
                await self.ws.send_json({"t": "error", "reason": "pin"})
                await self.ws.close()
                return
        self.client = Client(client_id, self.ws, role, allowed=self.allowed, pinned=pinned)
        self.client_id = client_id
        self.hub.clients[client_id] = self.client
        log.info(
            "连接建立：%s（%s%s%s，在线 %d）",
            client_id,
            role,
            "" if self.client.allowed else "，只能写字",
            f"，固定在 {pinned}" if pinned else "",
            len(self.hub.clients),
        )

        board_id = msg.get("board")
        since = msg.get("since")
        current = self.hub.board_of(self.client)
        runtime = self.hub.board(current)
        config: Config = self.app[CONFIG_KEY]
        common = {
            "role": role,
            "client": client_id,
            "info": {
                # 版本号由服务端给，不靠 pywebview 那条本地接口：那边取到的只在
                # Mac 窗口里有，而且每来一条 switch / sync 都要重新取一次。
                "version": __version__,
                "hostname": netinfo.local_hostname(),
                "port": config.port,
                "urls": netinfo.candidate_urls(config.port),
                "data_dir": str(config.data_dir),
            },
            "boards": self.hub.store.list_metas(),
            "folders": self.hub.store.folders(),
            "board": dict(runtime.meta),
            "seq": runtime.seq,
            "epoch": runtime.epoch,
            # 读不全的白板以只读方式打开，客户端据此提示并停止书写
            "locked": runtime.problem,
        }

        # 断线重连：白板没换、epoch 一致且历史够长时只补差量，避免整块白板重传。
        same_epoch = msg.get("epoch") == runtime.epoch
        if board_id == current and same_epoch and isinstance(since, int) and since >= 0:
            ops = runtime.ops_since(since)
            if ops is not None:
                await self.client.send({"t": "sync", "ops": ops, **common})
                return

        await self.client.send({"t": "init", "strokes": runtime.stroke_list(), **common})

    # ------------------------------------------------------------- 操作

    def _pin(self, raw: Any) -> Optional[str]:
        """握手里的 ``pin``：找到或新建指定的白板，返回它的 id；不允许时返回 None。

        应用自己建的白板（meta 里有 ``app``）只要能写字就能打开；用户自己的白板
        要有「管理白板」权限，和在界面上切换白板相同。
        """
        if not isinstance(raw, dict):
            return None
        board_id, app = raw.get("board"), raw.get("app")
        if not isinstance(board_id, str) or not models.PINNED_ID_RE.match(board_id):
            return None
        if not isinstance(app, str) or not models.APP_RE.match(app):
            return None
        existing = self.hub.store.get_meta(board_id)
        if existing is not None and not existing.get("app") and "manage" not in self.allowed:
            log.warning("拒绝固定到白板 %s：不是应用建立的白板，且没有管理权限", board_id)
            return None
        name = raw.get("name") if isinstance(raw.get("name"), str) else ""
        folder = raw.get("folder") if isinstance(raw.get("folder"), str) else ""
        kind = raw.get("kind") if isinstance(raw.get("kind"), str) else "board"
        self.hub.pin_board(board_id, app, name=name, kind=kind, folder=folder)
        return board_id

    def _board_id(self) -> Optional[str]:
        """这条连接所在的白板；固定的白板已被删除时返回 None。"""
        board_id = self.hub.board_of(self.client)
        if self.client.pinned and self.hub.store.get_meta(board_id) is None:
            return None
        return board_id

    async def _op(self, msg: Dict[str, Any]) -> None:
        raw = msg.get("op")
        board_id = self._board_id()
        if board_id is None:
            # 固定的白板已被删除：回执但不应用，否则会把一块已删除的白板重新写回磁盘
            await self.client.send({"t": "ack", "cid": msg.get("cid"), "seq": 0})
            return
        runtime = self.hub.board(board_id)
        if isinstance(raw, dict):
            kind = raw.get("op")
            if kind == "meta" and not self._may("settings"):
                raw = None  # 背景这些属于白板设置
            elif kind == "clear" and not self._may("clear"):
                raw = None  # 一下把整块白板抹掉，单独一项权限
        op = runtime.apply(raw) if raw is not None else None
        cid = msg.get("cid")
        if op is None:
            # 重复或无效操作：仍然回执，客户端好把它从待发队列里移除。
            await self.client.send({"t": "ack", "cid": cid, "seq": runtime.seq})
            return
        await self.client.send({"t": "ack", "cid": cid, "seq": op["seq"], "op": op})
        await self.hub.broadcast_board(
            board_id,
            {"t": "op", "op": op, "src": self.client.id, "board": board_id},
            exclude=self.client.id,
        )

    async def _live(self, msg: Dict[str, Any]) -> None:
        """书写过程中的实时点，不落盘，只转发给看着同一块白板的连接。"""
        board_id = self._board_id()
        if board_id is None:
            return
        msg = dict(msg)
        msg["src"] = self.client.id
        await self.hub.broadcast_board(board_id, msg, exclude=self.client.id)

    # --------------------------------------------------------- 白板管理

    async def _broadcast_switch(self) -> None:
        snapshot = self.hub.snapshot()
        await self.hub.broadcast({"t": "switch", **snapshot})

    async def _select(self, msg: Dict[str, Any]) -> None:
        if not self._may("manage"):
            return
        board_id = msg.get("board")
        if isinstance(board_id, str) and self.hub.select_board(board_id):
            await self._broadcast_switch()

    async def _new_board(self, msg: Dict[str, Any]) -> None:
        if not self._may("manage"):
            return
        kind = msg.get("kind")
        # 在某个文件夹里按的「新建」，新白板就落在那个文件夹里
        folder = models.sanitize_folder(msg.get("folder"))
        meta = self.hub.create_board(kind if kind in models.KINDS else "board")
        if folder and folder in self.hub.store.folders():
            self.hub.move_board(meta["id"], folder)
        await self._broadcast_switch()

    async def _rename(self, msg: Dict[str, Any]) -> None:
        """给白板改名。改的可能不是当前这块，所以只广播列表，不走整块 switch。"""
        if not self._may("manage"):
            return
        board_id = msg.get("board")
        name = msg.get("name")
        if not isinstance(board_id, str) or not isinstance(name, str):
            return
        if not self.hub.rename_board(board_id, name):
            return
        await self._broadcast_boards()

    async def _folder(self, msg: Dict[str, Any]) -> None:
        """把白板放进文件夹，或者移出来（``folder`` 是空串）。同样只广播列表。"""
        if not self._may("manage"):
            return
        board_id = msg.get("board")
        folder = msg.get("folder")
        if not isinstance(board_id, str) or not isinstance(folder, str):
            return
        if not self.hub.move_board(board_id, folder):
            return
        await self._broadcast_boards()

    async def _order(self, msg: Dict[str, Any]) -> None:
        """拖动排序：界面送来的是当前这一层看到的顺序。"""
        if not self._may("manage"):
            return
        if not self.hub.reorder_boards(msg.get("ids")):
            return
        await self._broadcast_boards()

    async def _new_folder(self, msg: Dict[str, Any]) -> None:
        """新建一个空文件夹。空文件夹只有索引里有记录，所以必须单独有这一条。"""
        if not self._may("manage"):
            return
        if not self.hub.create_folder(msg.get("name")):
            return
        await self._broadcast_boards()

    async def _del_folder(self, msg: Dict[str, Any]) -> None:
        """删文件夹不删白板：里面的白板移到没归类那一段。"""
        if not self._may("manage"):
            return
        if not self.hub.delete_folder(msg.get("name")):
            return
        await self._broadcast_boards()

    async def _rename_folder(self, msg: Dict[str, Any]) -> None:
        if not self._may("manage"):
            return
        if not self.hub.rename_folder(msg.get("name"), msg.get("to")):
            return
        await self._broadcast_boards()

    async def _broadcast_boards(self) -> None:
        """只刷白板列表：改的可能不是当前这块，没必要让所有人重载笔画。"""
        await self.hub.broadcast(
            {
                "t": "boards",
                "boards": self.hub.store.list_metas(),
                "folders": self.hub.store.folders(),
                "board": dict(self.hub.board().meta),
            }
        )

    async def _del_board(self, msg: Dict[str, Any]) -> None:
        if not self._may("manage"):
            return
        board_id = msg.get("board")
        if isinstance(board_id, str) and self.hub.delete_board(board_id):
            await self._broadcast_switch()
            # 固定在这块白板上的连接不跟随切换，单独告诉它们白板已被删除
            pinned = [c for c in list(self.hub.clients.values()) if c.pinned == board_id]
            for client in pinned:
                await client.send({"t": "deleted", "board": board_id})

    async def _unlock(self, msg: Dict[str, Any]) -> None:
        """用户看过提示、确认要编辑一块读不全的白板。只对当前这块，要管理权限。"""
        if not self._may("manage"):
            return
        board_id = self._board_id()
        if board_id is None or not self.hub.unlock(board_id):
            return
        if board_id == self.hub.current_id:
            # 所有设备一起解除只读，发整块 switch：白板没换，客户端不会丢视角和撤销栈
            await self._broadcast_switch()
        pinned = [c for c in list(self.hub.clients.values()) if c.pinned == board_id]
        snapshot = self.hub.snapshot(board_id)
        for client in pinned:
            await client.send({"t": "switch", **snapshot})

    async def _ping(self, msg: Dict[str, Any]) -> None:
        await self.client.send({"t": "pong", "ts": msg.get("ts")})

    async def close(self) -> None:
        if self.client is not None:
            # 同一个客户端 id 可能已经用新连接重新登记（iOS 唤醒后旧 socket 才断开），
            # 只有登记的还是自己时才注销，否则会把新连接从广播名单里摘掉。
            if self.hub.clients.get(self.client.id) is self.client:
                self.hub.clients.pop(self.client.id, None)
            self.client = None


# --------------------------------------------------------------------- 应用

@web.middleware
async def revalidate_static(request: web.Request, handler):
    """前端文件必须每次回源确认。

    否则 Safari 会按启发式规则把 js / css 缓存住，Mac 上更新了代码，
    iPad 的主屏图标点开还是旧的。局域网里多一次 304 的开销可以忽略。
    """
    response = await handler(request)
    if request.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


def create_app(config: Config, store: Optional[BoardStore] = None) -> web.Application:
    store = store or BoardStore(config.data_dir)
    app = web.Application(
        client_max_size=MAX_THUMB_BYTES + 4096, middlewares=[revalidate_static]
    )
    app[CONFIG_KEY] = config
    app[HUB_KEY] = Hub(store)
    app[RENDER_KEY] = asyncio.Semaphore(RENDER_LIMIT)

    app.router.add_get("/", handle_index)
    app.router.add_get("/ws", handle_ws)
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
    app.router.add_static("/static/", WEB_DIR / "static", name="static")

    async def _on_startup(_app: web.Application) -> None:
        _app[HUB_KEY].start_autosave()

    async def _on_shutdown(_app: web.Application) -> None:
        # 必须在等待处理协程之前断开长连接，否则关服务要干等到超时
        await _app[HUB_KEY].close_clients()

    async def _on_cleanup(_app: web.Application) -> None:
        await _app[HUB_KEY].stop()

    app.on_startup.append(_on_startup)
    app.on_shutdown.append(_on_shutdown)
    app.on_cleanup.append(_on_cleanup)
    return app
