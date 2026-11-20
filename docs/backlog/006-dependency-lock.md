# 依赖锁定

状态：完成　　来源：流程改进

## 为什么

`requirements.txt` 全是 `>=`，没有锁文件。依赖出新版本时，打包可能无故失败，或者和测试用的版本不一样。

## 要做什么

- 用 uv 或 pip-tools 生成锁文件（应用和测试各一份），打包和 CI 都从锁文件安装。
- 升级依赖是一个单独的提交，CI 全绿再合入。

## 验收

- 同一个提交两次打包，装进去的依赖版本一致。

## 进展

- 2026-11-20 `claude/dependency-lock`：
  - 输入文件照旧由人改：`requirements.txt`（应用）、新增的 `requirements-package.txt`（`-r requirements.txt`
    加 PyInstaller、cryptography）、`requirements-test.txt`。生成的锁文件：`requirements.lock`（打包、发布）、
    `requirements-test.lock`（测试，以 `requirements.lock` 为约束，共有的包版本相同）。
  - 用 `uv pip compile --universal --python-version 3.12 --generate-hashes`（`tools/lock_deps.py`，`make lock`）：
    一份锁覆盖 macOS arm64 和 Linux x86_64，pyobjc 等只在 Mac 上装的包靠环境标记区分。每个包都带哈希，
    安装用 `pip install --require-hashes`。只有 sdist 的 `proxy-tools`（pywebview 的依赖）在哈希模式下照样能装。
  - 从锁文件安装的地方：`make deps`、`.github/actions/setup-python-deps`（checks、unit 等）、
    `.github/actions/playwright` 容器里的 venv、package job（原来的 `pip install -r requirements.txt pyinstaller`
    和演练前单独装 cryptography 合成一步）、updates job。pip 缓存的键改成锁文件。
    Playwright 镜像版本改从 `requirements-test.lock` 读；`requirements-test.txt` 里仍用 `==` 固定，测试核对两者一致。
  - 锁文件第一行记着输入的指纹（输入文件内容和生成参数的 sha256）。`tests/test_dependency_lock.py`
    放进 `make checks`，每次都跑、不联网：输入改了没重新生成就失败（把 `requirements.txt` 里的 pillow 下限改成 11
    之后，`lock_deps.py --check` 报两份锁都过期，测试失败）；还查每个包都固定了版本和哈希、两份锁共有的包版本相同、
    Playwright 版本一致。
  - package job 把 `pip freeze --all` 和锁文件的 sha256 写进运行摘要：同一个提交两次打包，对比两次摘要即可。
  - 本机核对：用 `requirements-test.lock` 新建的 Python 3.12 venv（pip 24.0，和 Playwright 镜像里的一样）跑
    `make fast` 通过；`requirements.lock` 在 Linux 上完整安装通过；`uv pip install --dry-run --python-platform
    aarch64-apple-darwin --python-version 3.12 --only-binary :all: --no-binary proxy-tools` 解析出 34 个包，
    除 proxy-tools 外全是 macOS arm64 可用的 wheel。macOS 上真正安装要看这个分支 CI 的 package job。
  - 没改：`start-whiteboard.command` 从源码运行仍装 `requirements.txt`（用户机器上的 Python 可能是 3.10、3.11，
    锁文件按 3.12 生成）；inksync job 照旧按 `pyproject.toml` 的范围装最新版，测的是这个库对外声明的兼容范围。
