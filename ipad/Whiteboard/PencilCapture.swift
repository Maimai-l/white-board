import UIKit
import UIKit.UIGestureRecognizerSubclass

/// 采集 Pencil 的全部采样，按 docs/ipad-shell.md 5.1 节的格式打成一批交出去。
///
/// 采样取自窗口的 ``sendEvent``（见 ``ShellWindow``），不取自挂在 WKWebView 上的
/// 手势识别器。文档 4.2 节原来的做法实测不成立（12 节 Q2）：0.9.43 的录像
/// 20260927-211825 里，三笔都只收到落笔之后约 30 ms 的采样，之后再没有 move，
/// 也没有 up。网页调用 preventDefault 之后，WKWebView 内部用来推迟其他手势的
/// 识别器会让挂在它里面的识别器失败，从此收不到这一笔的触摸。窗口的 sendEvent
/// 在任何手势识别之前拿到每一个触摸事件，不受这套机制影响。
///
/// 估计属性的更新（通常是力度）只会送给手势识别器和响应者，不经过 sendEvent，
/// 所以另外挂一个只接收更新的识别器（``EstimateRecognizer``），挂在 WKWebView 的
/// 父视图上。它失败了只影响力度修正，不影响书写。
final class PencilTracker {
    /// 每次触摸事件结束时调用一次，参数是这一批的 JSON。
    var sink: ((String) -> Void)?
    /// 坐标以它为准：页面不滚动、不缩放时，数值就是网页的 clientX / clientY。
    weak var view: UIView?

    /// 接口版本（docs/ipad-shell.md 第 6 节）。只在格式有不兼容的改动时加 1。
    static let bridge = 1

    private var ids: [ObjectIdentifier: Int] = [:]
    private var nextId = 1
    /// 正在书写的那一笔最近一次的预测。只带力度更新的那一批也要带上它：
    /// 网页每收到一批都会整体替换预测，发 null 等于把预测擦掉。
    private var lastPrediction: Any = NSNull()

    /// 窗口收到的每一个触摸事件都先经过这里。
    func handle(_ event: UIEvent) {
        guard event.type == .touches, let view = view, let touches = event.allTouches else { return }
        var samples: [[String: Any]] = []
        var active = false
        for touch in touches where touch.type == .pencil {
            let key = ObjectIdentifier(touch)
            switch touch.phase {
            case .began:
                // 只跟踪落在网页上的笔：落在提示框、状态页上的不管
                guard let hit = touch.view, hit.isDescendant(of: view) else { continue }
                let id = nextId
                nextId += 1
                ids[key] = id
                let list = event.coalescedTouches(for: touch) ?? [touch]
                for (index, sample) in list.enumerated() {
                    samples.append(encode(sample, id: id, phase: index == 0 ? "down" : "move", in: view))
                }
                lastPrediction = prediction(for: touch, id: id, event: event, in: view)
                active = true
            case .moved:
                guard let id = ids[key] else { continue }
                for sample in event.coalescedTouches(for: touch) ?? [touch] {
                    samples.append(encode(sample, id: id, phase: "move", in: view))
                }
                lastPrediction = prediction(for: touch, id: id, event: event, in: view)
                active = true
            case .ended, .cancelled:
                guard let id = ids.removeValue(forKey: key) else { continue }
                let last = touch.phase == .ended ? "up" : "cancel"
                let list = event.coalescedTouches(for: touch) ?? [touch]
                for (index, sample) in list.enumerated() {
                    samples.append(encode(sample, id: id, phase: index == list.count - 1 ? last : "move", in: view))
                }
            default:
                // stationary：这一帧没动，没有新采样
                if ids[key] != nil {
                    active = true
                }
            }
        }
        if !active && ids.isEmpty {
            lastPrediction = NSNull()
        }
        emit(samples: samples, updates: [])
    }

    /// 估计属性的更新，按 estimationUpdateIndex 对应到之前发过的采样。
    func estimatesUpdated(_ touches: Set<UITouch>) {
        guard let view = view else { return }
        var updates: [[String: Any]] = []
        for touch in touches where touch.type == .pencil {
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

    // MARK: - 编码

    private func encode(_ touch: UITouch, id: Int, phase: String, in view: UIView) -> [String: Any] {
        var sample = fields(touch, in: view)
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

    private func fields(_ touch: UITouch, in view: UIView) -> [String: Any] {
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

    private func prediction(for touch: UITouch, id: Int, event: UIEvent, in view: UIView) -> Any {
        let predicted = event.predictedTouches(for: touch) ?? []
        if predicted.isEmpty {
            return NSNull()
        }
        return ["id": id, "samples": predicted.map { fields($0, in: view) }] as [String: Any]
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
            "bridge": PencilTracker.bridge,
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

/// 窗口：每个触摸事件在分发给视图和手势识别器之前，先交给 PencilTracker。
final class ShellWindow: UIWindow {
    var tracker: PencilTracker?

    override func sendEvent(_ event: UIEvent) {
        tracker?.handle(event)
        super.sendEvent(event)
    }
}

/// 只接收估计属性更新的识别器：只收 Pencil，不取消、不延迟任何触摸，永远不识别。
final class EstimateRecognizer: UIGestureRecognizer, UIGestureRecognizerDelegate {
    var onUpdate: ((Set<UITouch>) -> Void)?

    override init(target: Any?, action: Selector?) {
        super.init(target: target, action: action)
        allowedTouchTypes = [NSNumber(value: UITouch.TouchType.pencil.rawValue)]
        cancelsTouchesInView = false
        delaysTouchesBegan = false
        delaysTouchesEnded = false
        delegate = self
    }

    func gestureRecognizer(
        _ gestureRecognizer: UIGestureRecognizer,
        shouldRecognizeSimultaneouslyWith otherGestureRecognizer: UIGestureRecognizer
    ) -> Bool {
        true
    }

    func gestureRecognizer(
        _ gestureRecognizer: UIGestureRecognizer,
        shouldRequireFailureOf otherGestureRecognizer: UIGestureRecognizer
    ) -> Bool {
        false
    }

    override func canPrevent(_ preventedGestureRecognizer: UIGestureRecognizer) -> Bool {
        false
    }

    override func canBePrevented(by preventingGestureRecognizer: UIGestureRecognizer) -> Bool {
        false
    }

    override func touchesEstimatedPropertiesUpdated(_ touches: Set<UITouch>) {
        onUpdate?(touches)
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent) {
        if numberOfTouches == touches.count {
            state = .failed
        }
    }

    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent) {
        if numberOfTouches == touches.count {
            state = .failed
        }
    }
}
