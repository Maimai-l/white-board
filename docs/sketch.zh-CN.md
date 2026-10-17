[English](sketch.md) | 简体中文

# 速写

在 iPad 上用 Apple Pencil 随手写，在 Mac 上从浏览器工具栏弹出的速写里把写的内容复制为 PNG 图片，粘贴到其他网页，例如 Claude 的输入框。速写不保存：关闭速写页面后内容即被丢弃，iPad 回到原来的白板。

```
iPad 外壳（Apple Pencil 书写）
      │  白板的同步
Mac 上的白板应用（127.0.0.1:8848）
      │  页面 /sketch
工具栏弹出的速写（插件 extensions/sketch）── 复制 PNG ──> 其他网页
```

## 使用

1. 在 Mac 上运行白板应用，iPad 照常打开白板（iPad 外壳或主屏图标）。
2. 在 Edge 中按 Alt+Shift+S，或点工具栏上的插件图标，弹出速写（与其他插件的弹出页相同）。iPad 和 Mac 上的白板窗口同时切换到一块空白的速写板。
3. 在 iPad 上书写，弹出的速写实时显示。画面总是缩放到能看见全部笔迹，iPad 上写在任何位置都看得到（最多放大到 1 倍）。
4. 点「复制」（或按 ⌘C），到 Claude 的输入框中按 ⌘V。
5. 点「清空」开始下一张。点到别处时弹出页自动关闭，约 3 秒后速写板被丢弃，iPad 和 Mac 窗口回到原来的白板。

弹出页上只有两个按钮：「清空」和「复制」，左下角的圆点是连接状态（绿色为已连接）。白板应用的端口不需要设置：弹出页依次尝试 8848～8857，记住找到的那个。

不安装插件也可以用：在任意浏览器中打开 `http://127.0.0.1:8848/sketch`，功能相同。

| 行为 | 说明 |
| --- | --- |
| 导出 | 透明背景的 PNG。范围为笔迹所在的范围，四周留 64 个单位的边距，按 2 倍分辨率导出，边长不超过 4096 像素。 |
| 存储 | 速写板只在白板服务端的内存中，不写入存储目录，不生成缩略图，不出现在白板列表中。iPad 和浏览器也不在本地缓存它。服务端重启后速写板不存在。 |
| 刷新页面 | 3 秒内重新连接，速写内容保留。 |
| 速写中在 Mac 上选了别的白板 | iPad 跟随到那块白板，不再回到速写板；弹出页中的速写不受影响。 |
| iPad 断线后重连 | 速写还在进行时，iPad 回到速写板；速写已结束时，iPad 打开原来的白板。断线期间写在速写板上的笔画被丢弃，不会写到其他白板上。 |
| 权限 | 开始速写需要「管理白板」权限（会让 iPad 切换白板），本机总是有这个权限。 |

## 安装插件（Edge）

1. 下载 Release 中的 `Whiteboard-sketch-extension-<版本>.zip`（手动构建时在构建页面的产物 `Whiteboard-sketch-extension` 中）并解压；从源码运行时直接使用仓库中的 `extensions/sketch/`。
2. 在 Edge 中打开 `edge://extensions`，打开「开发人员模式」。
3. 点「加载解压缩的扩展」，选择解压后的 `sketch` 文件夹。
4. 如需修改快捷键，打开 `edge://extensions/shortcuts`。

Chrome 中的步骤相同，地址为 `chrome://extensions`。


## 实现

| 部分 | 位置 |
| --- | --- |
| 临时白板 | inksync 的 `Hub.create_transient` / `drop_transient`：只在内存中，不写盘、不进索引、不被释放。快照带 `transient: true`，前端据此不缓存。 |
| 速写会话 | `whiteboard/hub.py` 的 `start_sketch` / `end_sketch`：跟随的连接切到速写板 `sketch`；速写页面的连接断开 `SKETCH_GRACE`（3 秒）后结束。当前白板的记录始终不变。 |
| HTTP | `GET /sketch`（页面）、`POST /api/sketch`（开始或继续）、`DELETE /api/sketch`（结束），见 [protocol.zh-CN.md](protocol.zh-CN.md#http-接口)。 |
| 页面 | `whiteboard/web/sketch.html`、`whiteboard/web/static/js/sketch.js`。视图在每次笔迹变化、窗口大小变化后缩放到全部笔迹（`fit({maxScale: 1})`）； |
| 插件 | `extensions/sketch/`：Manifest V3 的工具栏弹出页（`popup.html`），只嵌入白板应用的 `/sketch` 页面，不包含手写板代码，因此白板升级后不需要更新插件。清单中的 `key` 固定了插件的 id。嵌入的页面与白板服务端同源，不受[来源检查](protocol.zh-CN.md#请求来源)影响。 |
| 测试 | `tests/test_sketch.py`（协议、并发、断线、权限、不留文件）、`tests/test_browser.py` 中的速写用例（真实浏览器中的跟随、复制透明 PNG、视图跟随笔迹、断网）、`tests/test_extension.py`（在 Chromium 中加载插件：嵌入、复制、自动查找端口） |
