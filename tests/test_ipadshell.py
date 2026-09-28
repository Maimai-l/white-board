"""iPad 外壳的分发与发现：安装页、版本接口、IPA 下载、Bonjour 注册。"""

import asyncio
import re
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from tests.test_server import make_client, run
from whiteboard import __version__, ipadshell, netinfo
from whiteboard.config import Config
from whiteboard.runner import ServerThread
from whiteboard.server import CONFIG_KEY

ROOT = Path(__file__).resolve().parents[1]


def test_bridge_range_matches_the_web_page():
    """Mac 端报给外壳的接口版本范围，必须和网页自己声明的一致。"""
    source = (ROOT / "whiteboard/web/static/js/input.js").read_text("utf-8")
    match = re.search(r"export const SHELL_BRIDGE = \[(\d+), (\d+)\];", source)
    assert match, "input.js 里找不到 SHELL_BRIDGE"
    assert tuple(int(v) for v in match.groups()) == ipadshell.BRIDGE


def test_exporting_is_a_download_not_a_navigation():
    """导出不能给 location.href 赋值。

    外壳是一个 WKWebView，赋值 location.href 是一次导航：白板页面被换走，
    WebSocket 跟着断，界面上看到的就是一句连接断开，文件也没存下来。带 download
    的链接外壳会当成下载接住（见 ShellViewController 的 WKDownloadDelegate）。
    """
    app_js = (ROOT / "whiteboard/web/static/js/app.js").read_text("utf-8")
    exporter_js = (ROOT / "whiteboard/web/static/js/exporter.js").read_text("utf-8")
    assert "downloadURL(`/api/export/${boardId}`" in app_js
    assert "location.href = `/api/export" not in app_js
    assert "link.download" in exporter_js
    # PNG 也不能直接下 data: 链接，WKWebView 下不了，要先转成 blob:
    assert "URL.createObjectURL" in exporter_js

    swift = (ROOT / "ipad/Whiteboard/ShellViewController.swift").read_text("utf-8")
    assert "WKDownloadDelegate" in swift
    assert "navigationAction.shouldPerformDownload" in swift
    assert "UIActivityViewController" in swift  # 下完了交给系统的分享面板


def test_the_page_can_ask_the_shell_to_switch_macs():
    """局域网里有好几台 Mac 时，从页面的设置里换一台；两边的命令名要对得上。"""
    shell_js = (ROOT / "whiteboard/web/static/js/shell.js").read_text("utf-8")
    ui_js = (ROOT / "whiteboard/web/static/js/ui.js").read_text("utf-8")
    swift = (ROOT / "ipad/Whiteboard/ShellViewController.swift").read_text("utf-8")
    assert "export function shellCommand(" in shell_js
    assert 'shellCommand("rediscover")' in ui_js
    assert 'body["type"] as? String == "rediscover"' in swift
    # 只找到一台也要列出来，否则「换一台」会直接又连回原来那台
    assert "startDiscovery(switching: true)" in swift


def test_version_needs_no_permission_and_reports_no_ipa_from_source(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, _app):
            response = await client.get("/ipad/version")
            assert response.status == 200
            assert await response.json() == {"version": __version__, "ipa": False, "bridge": [1, 1]}
            missing = await client.get(f"/ipad/{ipadshell.IPA_NAME}")
            assert missing.status == 404

    run(main())


def test_install_page_without_ipa_points_to_the_release(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, app):
            port = app[CONFIG_KEY].port
            response = await client.get("/ipad", params={"host": "studio.local"})
            body = await response.text()
            assert response.status == 200
            assert "这个版本没有附带外壳" in body
            assert "https://github.com/Maimai-l/white-board/releases" in body
            assert "apple-magnifier://" not in body
            # 「打开外壳」把这台 Mac 的地址交给外壳，全程不用输入文字
            assert f'href="whiteboard-shell://connect?host=studio.local&amp;port={port}"' in body

    run(main())


def test_install_page_and_download_with_a_bundled_ipa(tmp_path, monkeypatch):
    ipa = tmp_path / "Whiteboard.ipa"
    ipa.write_bytes(b"PK\x03\x04 fake ipa")
    monkeypatch.setattr(ipadshell, "ipa_path", lambda: ipa)

    async def main():
        async with make_client(tmp_path) as (client, app):
            port = app[CONFIG_KEY].port
            body = await (await client.get("/ipad", params={"host": "studio.local"})).text()
            assert "安装白板外壳" in body
            assert (
                f'href="apple-magnifier://install?url=http://studio.local:{port}/ipad/Whiteboard.ipa"'
                in body
            )
            info = await (await client.get("/ipad/version")).json()
            assert info["ipa"] is True
            response = await client.get("/ipad/Whiteboard.ipa")
            assert response.status == 200
            assert await response.read() == ipa.read_bytes()

    run(main())


def test_install_page_uses_the_local_hostname_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(netinfo, "local_hostname", lambda: "mac-mini.local")

    async def main():
        async with make_client(tmp_path) as (client, _app):
            body = await (await client.get("/ipad")).text()
            assert "whiteboard-shell://connect?host=mac-mini.local&amp;port=" in body

    run(main())


def test_ipa_is_looked_up_in_the_resource_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(ipadshell.resources, "resource_root", lambda: tmp_path)
    assert ipadshell.ipa_path() is None
    (tmp_path / "ipad").mkdir()
    (tmp_path / "ipad" / "Whiteboard.ipa").write_bytes(b"x")
    assert ipadshell.ipa_path() == tmp_path / "ipad" / "Whiteboard.ipa"


# ------------------------------------------------------------------ Bonjour


def test_txt_record_is_length_prefixed():
    assert netinfo.txt_record({"host": "a.local", "port": "8848"}) == b"\x0chost=a.local\x09port=8848"
    long = netinfo.txt_record({"name": "x" * 400})
    assert long[0] == 255 and len(long) == 256


def test_bonjour_fields_carry_host_port_version_and_name(monkeypatch):
    monkeypatch.setattr(netinfo, "local_hostname", lambda: "studio.local")
    monkeypatch.setattr(netinfo, "computer_name", lambda: "工作室的 Mac")
    assert netinfo.bonjour_fields(8849, "1.3.0") == {
        "host": "studio.local",
        "port": "8849",
        "version": "1.3.0",
        "name": "工作室的 Mac",
    }


def test_bonjour_uses_the_system_responder_on_macos(monkeypatch):
    """macOS 上走 DNSServiceRegister，不再起第二个 mDNS 响应程序。"""
    calls = []

    class FakeDNSSD:
        def register(self, name, regtype, port, txt):
            calls.append(("register", name, regtype, port, txt))
            return "ref"

        def deallocate(self, ref):
            calls.append(("deallocate", ref))

    def no_zeroconf(*args, **kwargs):
        raise AssertionError("macOS 上不能用 zeroconf")

    monkeypatch.setattr(netinfo.sys, "platform", "darwin")
    monkeypatch.setattr(netinfo, "_DNSSD", FakeDNSSD)
    monkeypatch.setattr(netinfo, "MDNSAdvertiser", no_zeroconf)
    monkeypatch.setattr(netinfo, "local_hostname", lambda: "studio.local")
    monkeypatch.setattr(netinfo, "computer_name", lambda: "Studio")

    async def main():
        service = netinfo.BonjourService(8849, "1.3.0")
        assert await service.start() is True
        await service.stop()

    asyncio.run(main())
    fields = {"host": "studio.local", "port": "8849", "version": "1.3.0", "name": "Studio"}
    assert calls == [
        ("register", "Studio", "_whiteboard._tcp", 8849, netinfo.txt_record(fields)),
        ("deallocate", "ref"),
    ]


def test_bonjour_failure_is_only_logged(monkeypatch):
    class Broken:
        def register(self, *args):
            raise OSError("DNSServiceRegister 返回 -65537")

        def deallocate(self, ref):
            raise AssertionError("没注册成功就不该注销")

    monkeypatch.setattr(netinfo.sys, "platform", "darwin")
    monkeypatch.setattr(netinfo, "_DNSSD", Broken)
    monkeypatch.setattr(netinfo, "computer_name", lambda: "Studio")

    async def main():
        service = netinfo.BonjourService(8848, "1.3.0")
        assert await service.start() is False
        await service.stop()

    asyncio.run(main())


def test_bonjour_uses_zeroconf_elsewhere(monkeypatch):
    seen = {}

    class FakeAdvertiser:
        def __init__(self, port, name, service_type, properties):
            seen.update(port=port, name=name, type=service_type, props=properties)

        async def start(self):
            return True

        async def stop(self):
            seen["stopped"] = True

    monkeypatch.setattr(netinfo.sys, "platform", "linux")
    monkeypatch.setattr(netinfo, "MDNSAdvertiser", FakeAdvertiser)
    monkeypatch.setattr(netinfo, "computer_name", lambda: "box")

    async def main():
        service = netinfo.BonjourService(8850, "1.3.0")
        assert await service.start() is True
        await service.stop()

    asyncio.run(main())
    assert seen["type"] == "_whiteboard._tcp"
    assert seen["props"]["port"] == "8850"
    assert seen["stopped"] is True


def make_config(tmp_path, port):
    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"
    config.port = port
    return config


def test_bonjour_registers_the_port_actually_in_use(tmp_path, monkeypatch):
    """端口被占用顺延之后，TXT 里写的是顺延后的端口：外壳下次启动就能连到新端口。"""
    ports = []

    class Recording:
        def __init__(self, port, version):
            ports.append(port)

        async def start(self):
            return True

        async def stop(self):
            pass

    import whiteboard.runner as runner

    monkeypatch.setattr(runner, "BonjourService", Recording)
    first = ServerThread(make_config(tmp_path, 8481), advertise=False, bonjour=False)
    first.start()
    second = ServerThread(make_config(tmp_path / "other", 8481), advertise=False, bonjour=True)
    try:
        port = second.start()
        assert port == first.port + 1
        for _ in range(50):
            if ports:
                break
            time.sleep(0.02)
        assert ports == [port]
    finally:
        second.stop()
        first.stop()


def test_bonjour_failure_does_not_break_startup(tmp_path, monkeypatch):
    async def boom(self):
        raise RuntimeError("mDNSResponder 不在")

    monkeypatch.setattr(netinfo.BonjourService, "start", boom)
    server = ServerThread(make_config(tmp_path, 8485), advertise=False, bonjour=True)
    port = server.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/ipad/version", timeout=5) as response:
            assert response.status == 200
    finally:
        server.stop()


def test_bonjour_is_on_by_default_only_on_macos(monkeypatch):
    monkeypatch.setattr(netinfo.sys, "platform", "darwin")
    assert netinfo.bonjour_default() is True
    monkeypatch.setattr(netinfo.sys, "platform", "linux")
    assert netinfo.bonjour_default() is False


@pytest.mark.skipif(sys.platform != "darwin", reason="DNSServiceRegister 只在 macOS 上有")
def test_dnssd_binding_registers_on_macos():  # pragma: no cover - 只在 Mac 上跑
    dnssd = netinfo._DNSSD()
    ref = dnssd.register("白板测试", "_whiteboard._tcp", 65000, netinfo.txt_record({"port": "65000"}))
    dnssd.deallocate(ref)
