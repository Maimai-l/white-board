"""inksync 单独使用：不依赖白板应用，挂到任意 aiohttp 服务上就能同步。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]


def test_inksync_does_not_import_the_whiteboard_app():
    for path in (PACKAGE / "inksync").rglob("*.py"):
        source = path.read_text("utf-8")
        assert "import whiteboard" not in source and "from whiteboard" not in source, path.name

    # 只把 packages/inksync 放进路径，白板应用完全不在：照样能导入并建起服务
    code = (
        "import sys; sys.path[:0] = [sys.argv[1]];"
        "import inksync, aiohttp.web as web;"
        "from inksync import FileStorage, Hub, mount, serve_sdk;"
        "app = web.Application(); mount(app, Hub(FileStorage(sys.argv[2]))); serve_sdk(app);"
        "assert 'whiteboard' not in sys.modules; print('ok')"
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, "-c", code, str(PACKAGE), "/tmp/inksync-standalone"],
        cwd="/", capture_output=True, text=True, env=env, timeout=60,
    )
    assert result.stdout.strip() == "ok", result.stderr


def test_public_names():
    import inksync

    assert set(inksync.__all__) == {
        "BoardFileError", "DefaultPolicy", "FileStorage", "Hub", "Policy", "Principal", "Spaces",
        "mount", "serve_sdk", "__version__",
    }
    assert inksync.__version__ == "2.0.0"
