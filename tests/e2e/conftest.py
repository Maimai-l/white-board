"""端到端测试的 fixture：真的开浏览器页面（Mac 端 + iPad 端）互相同步。

没装 Playwright 或找不到浏览器时整个目录自动跳过。
"""

from __future__ import annotations

import pytest

from e2e.support import ENGINE, chromium_path, start_server


@pytest.fixture(scope="module")
def browser():
    """每个测试文件起一个浏览器；每个用例自己开的页面在用例结束时关掉（见 close_pages）。

    不做成整个会话共用：test_extension.py 要另起一个带插件的 Chromium，同一个线程里
    不能同时开着两份 Playwright。
    """
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as pw:
        try:
            if ENGINE == "chromium":
                path = chromium_path()
                instance = pw.chromium.launch(executable_path=path) if path else pw.chromium.launch()
            else:
                instance = getattr(pw, ENGINE).launch()
        except Exception as exc:  # pragma: no cover - 环境没有浏览器
            pytest.skip(f"没有可用的 {ENGINE}：{exc}")
        yield instance
        instance.close()


@pytest.fixture(autouse=True)
def close_pages(request):
    """每个用例结束时把它开的页面都关掉，用例中途失败也一样，不留给下一个用例。

    所以用例末尾不用再写 ``page.close()``。
    """
    yield
    if "browser" in request.fixturenames:
        for context in list(request.getfixturevalue("browser").contexts):
            try:
                context.close()
            except Exception:  # noqa: BLE001 - 已经关掉的就算了
                pass


@pytest.fixture
def make_server(tmp_path):
    """起服务端的工厂：``make_server(prepare=..., port=...)``。用例结束时统一停掉。

    同一个用例里起第二个（比如模拟重启）时传同一个 port，数据目录是同一个。
    """
    started = []

    def make(prepare=None, port=None):
        thread = start_server(tmp_path, port=port, prepare=prepare)
        started.append(thread)
        return thread

    yield make
    for thread in reversed(started):
        thread.stop()


@pytest.fixture
def server(make_server):
    return make_server()
