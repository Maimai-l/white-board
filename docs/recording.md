# 输入录制

有些问题只有真笔能触发：压感沿笔画变化、倾角一直在动、iPad 上一帧能来二十几个
合并采样点、抬笔那一刻的时序。这些在开发机上拿鼠标敲不出来，靠人反复手动复现
又太慢。录一次带回来，剩下的都在开发机上跑。

## 怎么录

连点左上角那个连接状态的小圆点三下，诊断面板出来的同时，左下角会多出一格
「录制输入」。点它开始，把出问题的操作做一遍，再点「停止录制」。

录像直接 POST 到 Mac 上，存在数据目录的 `recordings/` 里，文件名是时间戳，
那一格上会显示存到了哪。服务端存不下（比如页面是从别处打开的）就退回浏览器下载。

入口和卡顿诊断共用一个开关，是因为 iPad 上白板是靠配置描述文件装的 Web Clip
打开的，没有地址栏，改不了查询参数，所以入口只能是屏幕上点得到的东西；而这个
开关本来就在用。在 Mac 的浏览器里也可以直接用 `?debug=1`。

录着的时候再连点三下把诊断关掉，会先把录像停下来存好，不会白录。

## 录的是什么

录的是**原始指针事件**，不是笔画。笔画是输入走完整条链路之后的结果，拿结果
回放就只能复现渲染，复现不了判定、平滑、合并采样和抬笔那一刻的时序。

每条事件记下 `pointerdown` / `pointermove` / `pointerup` / `pointercancel`、
相对录制开始的时间、指针 id 和类型、`clientX/Y`、压力、`tiltX/tiltY`、
`altitudeAngle`、`azimuthAngle`、`twist`，以及 `getCoalescedEvents()` 里那一帧的
全部采样点。落笔那一条还带当时的工具和视口。

一律记原值，不做四舍五入。用途是逐字复现，回放出来的笔画要和录制时一模一样才能
当基准；坐标少留三位小数，笔画就对不上了，差异是真是假分不清。

除了事件，还记下录制开始时板上的全部笔画（`before`）、结束时的全部笔画
（`after`）、视口、设备像素比、画布位置和尺寸、是否开了手指书写。

## 怎么回放

```js
await whiteboard.recorder.replay(data)          // 按原速
await whiteboard.recorder.replay(data, { wait: false })  // 不等时间，一口气喂完
```

回放会先把板恢复成 `before` 的样子、视口摆回录制时的位置，再按记录的时间把事件
喂回去。喂的是普通对象而不是合成 `PointerEvent`——后者的构造函数不收
`altitudeAngle` / `azimuthAngle`，也造不出 `getCoalescedEvents`，而这两样恰好是
橡皮粗细和笔宽最依赖的。直接调 `InputController` 的处理函数，读到的字段一模一样。

回放的返回值是板上剩下的笔画，和录像里的 `after` 比就知道复现得对不对。
`tests/test_browser.py::test_recorder_replays_an_erase_exactly` 就是这么验的。

在开发机上跑一份录像：

```python
data = json.load(open("recordings/20260924-203011.json"))
page.evaluate("async ([d]) => whiteboard.recorder.replay(d, { wait: false })", [data])
```
