[English](sketch.md) | 简体中文

# 速写

在 iPad 上用 Apple Pencil 随手写，在 Mac 上把写的内容作为 PNG 图片复制或拖进其他网页，例如 Claude 的输入框。速写不保存：关闭速写页面后内容即被丢弃，iPad 回到原来的白板。

```
iPad 外壳（Apple Pencil 书写）
      │  白板的同步
Mac 上的白板应用（127.0.0.1:8848）
      │  页面 /sketch
Edge 侧边栏（插件 extensions/sketch）── 复制或拖出 PNG ──> 其他网页
```

## 使用

1. 在 Mac 上运行白板应用，iPad 照常打开白板（iPad 外壳或主屏图标）。
2. 在 Edge 中按 Alt+Shift+S，或点工具栏上的插件图标，侧边栏打开速写页面。iPad 和 Mac 上的白板窗口同时切换到一块空白的速写板。
3. 在 iPad 上书写，侧边栏实时显示。
4. 点「复制」（或在侧边栏中按 ⌘C），到 Claude 的输入框中按 ⌘V；也可以把右下角的预览图拖进网页。
5. 点「清空」开始下一张。关闭侧边栏约 3 秒后，速写板被丢弃，iPad 和 Mac 窗口回到原来的白板。

不安装插件也可以用：在任意浏览器中打开 `http://127.0.0.1:8848/sketch`，功能相同。

| 行为 | 说明 |
| --- | --- |
| 导出范围 | 笔迹所在的范围，四周留 64 个单位的边距，按 2 倍分辨率导出，边长不超过 4096 像素。 |
| 背景 | 默认白色。勾选「透明背景」后导出透明 PNG；深色界面中黑色笔迹在透明背景上可能看不清。 |
| 存储 | 速写板只在白板服务端的内存中，不写入存储目录，不生成缩略图，不出现在白板列表中。iPad 和浏览器也不在本地缓存它。服务端重启后速写板不存在。 |
| 刷新页面 | 3 秒内重新连接，速写内容保留。 |
| 速写中在 Mac 上选了别的白板 | iPad 跟随到那块白板，不再回到速写板；侧边栏中的速写不受影响。 |
| iPad 断线后重连 | 速写还在进行时，iPad 回到速写板；速写已结束时，iPad 打开原来的白板。断线期间写在速写板上的笔画被丢弃，不会写到其他白板上。 |
| 权限 | 开始速写需要「管理白板」权限（会让 iPad 切换白板），本机总是有这个权限。 |

## 安装插件（Edge）

1. 下载 Release 中的 `Whiteboard-sketch-extension-<版本>.zip` 并解压；从源码运行时直接使用仓库中的 `extensions/sketch/`。
2. 在 Edge 中打开 `edge://extensions`，打开「开发人员模式」。
3. 点「加载解压缩的扩展」，选择解压后的 `sketch` 文件夹。
4. 如需修改快捷键，打开 `edge://extensions/shortcuts`。

Chrome 中的步骤相同，地址为 `chrome://extensions`。

白板应用的端口不是 8848 时（例如 8848 被占用，自动顺延到了 8849），在侧边栏底部的「设置」中修改端口。

## 实现

| 部分 | 位置 |
| --- | --- |
| 临时白板 | inksync 的 `Hub.create_transient` / `drop_transient`：只在内存中，不写盘、不进索引、不被释放。快照带 `transient: true`，前端据此不缓存。 |
| 速写会话 | `whiteboard/hub.py` 的 `start_sketch` / `end_sketch`：跟随的连接切到速写板 `sketch`；速写页面的连接断开 `SKETCH_GRACE`（3 秒）后结束。当前白板的记录始终不变。 |
| HTTP | `GET /sketch`（页面）、`POST /api/sketch`（开始或继续）、`DELETE /api/sketch`（结束），见 [protocol.zh-CN.md](protocol.zh-CN.md#http-接口)。 |
| 页面 | `whiteboard/web/sketch.html`、`whiteboard/web/static/js/sketch.js` |
| 插件 | `extensions/sketch/`：Manifest V3 侧边栏，只嵌入白板应用的 `/sketch` 页面，不包含手写板代码，因此白板升级后不需要更新插件。插件的请求带插件自己的来源，不受[来源检查](protocol.zh-CN.md#请求来源)影响：嵌入的页面与白板服务端同源。 |
| 测试 | `tests/test_sketch.py`（协议、并发、断线、权限、不留文件）、`tests/test_browser.py` 中的速写用例（真实浏览器中的跟随、复制 PNG、拖出、断网）、`tests/test_extension.py`（在 Chromium 中加载插件） |
