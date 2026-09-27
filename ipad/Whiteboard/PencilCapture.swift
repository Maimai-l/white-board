import UIKit
import UIKit.UIGestureRecognizerSubclass

/// 采集 Pencil 的全部采样，按 docs/ipad-shell.md 5.1 节的格式打成一批交出去。
///
/// 挂在 WKWebView 上，只收 Pencil，不取消、不延迟 WKWebView 自己收到的触摸，
/// 和所有其他手势识别器同时识别——网页那边的 pointer 事件照常到达（手指、以及
/// 外壳 active 时拿来比对坐标的 Safari pen 事件）。
///
/// 它自己永远不进入 began 状态：一直停在 possible 里就能一直收到触摸，又不会
/// 因为「已识别」去阻止 WKWebView 内部的识别器。这支笔抬起来之后置为 failed，
/// 等下一次落笔重新开始。
final class PencilCapture: UIGestureRecognizer, UIGestureRecognizerDelegate {
    /// 每次触摸回调结束时调用一次，参数是这一批的 JSON。
    var sink: ((String) -> Void)?

    /// 接口版本（docs/ipad-shell.md 第 6 节）。只在格式有不兼容的改动时加 1。
    static let bridge = 1

    private var ids: [ObjectIdentifier: Int] = [:]
    private var nextId = 1
    /// 正在书写的那一笔最近一次的预测。只带力度更新的那一批也要带上它：
    /// 网页每收到一批都会整体替换预测，发 null 等于把预测擦掉。
    private var lastPrediction: Any = NSNull()

    override init(target: Any?, action: Selector?) {
        super.init(target: target, action: action)
        allowedTouchTypes = [NSNumber(value: UITouch.TouchType.pencil.rawValue)]
        cancelsTouchesInView = false
        delaysTouchesBegan = false
        delaysTouchesEnded = false
        delegate = self
    }

    // MARK: - 不干扰别的识别器

    func gestureRecognizer(
        _ gestureRecognizer: UIGestureRecognizer,
        shouldRecognizeSimultaneouslyWith otherGestureRecognizer: UIGestureRecognizer
    ) -> Bool {
        true
    }

    override func canPrevent(_ preventedGestureRecognizer: UIGestureRecognizer) -> Bool {
        false
    }

    override func canBePrevented(by preventingGestureRecognizer: UIGestureRecognizer) -> Bool {
        false
    }

    // MARK: - 触摸

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent) {
        var samples: [[String: Any]] = []
        for touch in touches where touch.type == .pencil {
            let id = nextId
            nextId += 1
            ids[ObjectIdentifier(touch)] = id
            let list = event.coalescedTouches(for: touch) ?? [touch]
            for (index, sample) in list.enumerated() {
                samples.append(encode(sample, id: id, phase: index == 0 ? "down" : "move"))
            }
            lastPrediction = prediction(for: touch, id: id, event: event)
        }
        emit(samples: samples, updates: [])
    }

    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent) {
        var samples: [[String: Any]] = []
        for touch in touches where touch.type == .pencil {
            guard let id = ids[ObjectIdentifier(touch)] else { continue }
            for sample in event.coalescedTouches(for: touch) ?? [touch] {
                samples.append(encode(sample, id: id, phase: "move"))
            }
            lastPrediction = prediction(for: touch, id: id, event: event)
        }
        emit(samples: samples, updates: [])
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent) {
        finish(touches, event: event, phase: "up")
    }

    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent) {
        finish(touches, event: event, phase: "cancel")
    }

    /// 抬笔或被系统取消：这一次回调里前面的采样照常是 move，最后一个是 up / cancel。
    private func finish(_ touches: Set<UITouch>, event: UIEvent, phase: String) {
        var samples: [[String: Any]] = []
        for touch in touches where touch.type == .pencil {
            guard let id = ids.removeValue(forKey: ObjectIdentifier(touch)) else { continue }
            let list = event.coalescedTouches(for: touch) ?? [touch]
            for (index, sample) in list.enumerated() {
                samples.append(encode(sample, id: id, phase: index == list.count - 1 ? phase : "move"))
            }
        }
        if ids.isEmpty {
            lastPrediction = NSNull()
        }
        emit(samples: samples, updates: [])
        if ids.isEmpty {
            state = .failed
        }
    }

    /// 估计属性的更新（通常是力度），按 estimationUpdateIndex 对应到之前发过的采样。
    override func touchesEstimatedPropertiesUpdated(_ touches: Set<UITouch>) {
        guard let view = view else { return }
        var updates: [[String: Any]] = []
        for touch in touches {
            guard let index = touch.estimationUpdateIndex else { continue }
            updates.append([
                "ui": index.intValue,
                "f": number(touch.force),
                "alt": number(touch.altitudeAngle),
                "az": number(touch.azimuthAngle(in: view)),
            ])
        }
        emit(samples: [], updates: updates)
    }

    override func reset() {
        super.reset()
        // 被系统整体重置时（例如应用切到后台），没抬笔的触摸再也不会有结束回调
        if !ids.isEmpty {
            ids.removeAll()
            lastPrediction = NSNull()
        }
    }

    // MARK: - 编码

    private func encode(_ touch: UITouch, id: Int, phase: String) -> [String: Any] {
        var sample = fields(touch)
        sample["id"] = id
        sample["ph"] = phase
        sample["k"] = "real"
        let expecting = propertyNames(touch.estimatedPropertiesExpectingUpdates)
        sample["est"] = expecting
        if !expecting.isEmpty, let index = touch.estimationUpdateIndex {
            sample["ui"] = index.intValue
        } else {
            sample["ui"] = NSNull()
        }
        return sample
    }

    /// 一个采样的位置、时间、力度和角度。坐标一律用 preciseLocation(in: webView)，
    /// 页面缩放为 1 且不滚动时，数值就是网页的 clientX / clientY。
    private func fields(_ touch: UITouch) -> [String: Any] {
        guard let view = view else { return [:] }
        let point = touch.preciseLocation(in: view)
        return [
            "t": touch.timestamp,
            "x": number(point.x),
            "y": number(point.y),
            "f": number(touch.force),
            "fmax": number(touch.maximumPossibleForce),
            "alt": number(touch.altitudeAngle),
            "az": number(touch.azimuthAngle(in: view)),
        ]
    }

    private func prediction(for touch: UITouch, id: Int, event: UIEvent) -> Any {
        let predicted = event.predictedTouches(for: touch) ?? []
        if predicted.isEmpty {
            return NSNull()
        }
        return ["id": id, "samples": predicted.map { fields($0) }] as [String: Any]
    }

    private func propertyNames(_ properties: UITouch.Properties) -> [String] {
        var names: [String] = []
        if properties.contains(.force) { names.append("force") }
        if properties.contains(.azimuth) { names.append("azimuth") }
        if properties.contains(.altitude) { names.append("altitude") }
        if properties.contains(.location) { names.append("location") }
        return names
    }

    /// JSONSerialization 遇到 NaN 或无穷会直接抛异常，这里兜住。
    private func number(_ value: CGFloat) -> Double {
        value.isFinite ? Double(value) : 0
    }

    private func emit(samples: [[String: Any]], updates: [[String: Any]]) {
        if samples.isEmpty && updates.isEmpty {
            return
        }
        let batch: [String: Any] = [
            "bridge": PencilCapture.bridge,
            "samples": samples,
            "pred": lastPrediction,
            "updates": updates,
        ]
        guard
            let data = try? JSONSerialization.data(withJSONObject: batch),
            let json = String(data: data, encoding: .utf8)
        else {
            return
        }
        sink?(json)
    }
}
