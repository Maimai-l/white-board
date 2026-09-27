import QuartzCore
import UIKit
import WebKit

/// 外壳的全部界面：一个铺满屏幕的 WKWebView，上面按需盖一层状态页。
///
/// 外壳只提供输入和平台服务，不包含白板逻辑（docs/ipad-shell.md 1.2 节）：笔迹、
/// 同步、橡皮、界面都在 Mac 提供的网页里，Mac 端更新之后 iPad 下次打开就是新代码。
final class ShellViewController: UIViewController, WKNavigationDelegate, WKUIDelegate,
    WKScriptMessageHandler, UIScribbleInteractionDelegate
{
    private var webView: WKWebView!
    private let status = StatusView()
    /// Pencil 采样，由窗口的 sendEvent 送进来（见 PencilCapture.swift）
    let tracker = PencilTracker()
    private let estimates = EstimateRecognizer(target: nil, action: nil)
    private let discovery = MacDiscovery()
    private let updates = UpdateChecker()

    /// 正在加载或已经加载的那台 Mac。
    private var current: MacAddress?
    /// 这次加载用的是本机保存的地址：失败时自动转去 Bonjour 查找，而不是报错。
    private var fromSaved = false
    private var loaded = false
    /// 网页握手成功、接口版本对得上：此后才发送采样。
    private var bridgeActive = false
    private var warnedIncompatible = false

    private var found: [MacAddress] = []
    private var listing = false
    private var searchTimeout: DispatchWorkItem?
    private var settle: DispatchWorkItem?

    private var evalStats = EvalStats()

    // MARK: - 视图

    override func loadView() {
        let configuration = WKWebViewConfiguration()
        configuration.userContentController.add(WeakScriptHandler(self), name: "whiteboard")
        configuration.dataDetectorTypes = []
        configuration.allowsInlineMediaPlayback = true

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        // 长按菜单与链接预览
        webView.allowsLinkPreview = false
        webView.allowsBackForwardNavigationGestures = false
        // 页面不滚动、不缩放，坐标原点就是 WKWebView 的左上角：
        // preciseLocation(in: webView) 与网页的 clientX / clientY 数值相同
        let scroll = webView.scrollView
        scroll.isScrollEnabled = false
        scroll.bounces = false
        scroll.minimumZoomScale = 1
        scroll.maximumZoomScale = 1
        scroll.contentInsetAdjustmentBehavior = .never
        // 随手写（Scribble）：Pencil 在可编辑区域书写会被识别成文字输入
        webView.addInteraction(UIScribbleInteraction(delegate: self))
        if #available(iOS 16.4, *) {
            webView.isInspectable = true
        }

        tracker.view = webView
        tracker.sink = { [weak self] json in
            self?.send(json)
        }
        estimates.onUpdate = { [weak self] touches in
            self?.tracker.estimatesUpdated(touches)
        }

        let root = UIView()
        // 挂在 WKWebView 外面：挂在它里面的识别器会被网页的 preventDefault 连带弄失败
        root.addGestureRecognizer(estimates)
        root.backgroundColor = .systemBackground
        webView.frame = root.bounds
        webView.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        root.addSubview(webView)
        status.frame = root.bounds
        status.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        root.addSubview(status)
        status.isHidden = true
        view = root
        self.webView = webView
    }

    override var prefersStatusBarHidden: Bool { true }
    override var prefersHomeIndicatorAutoHidden: Bool { true }
    // 屏幕边缘的系统手势（下拉通知中心、上滑回主屏）要先拉一下才生效，写到边上不会被抢走
    override var preferredScreenEdgesDeferringSystemGestures: UIRectEdge { .all }

    // MARK: - 连接（docs/ipad-shell.md 4.1 节）

    /// 启动之后还什么都没做（例如由一条认不出的链接启动）：照常启动。
    func startIfIdle() {
        if current == nil && discovery.onChange == nil {
            start()
        }
    }

    /// 启动：有保存的地址就直接加载，否则用 Bonjour 查找。
    func start() {
        loadViewIfNeeded()
        ShellSettings.publishVersion()
        if ShellSettings.takeRediscover() {
            ShellSettings.forget()
        }
        if let saved = ShellSettings.savedMac() {
            connect(to: saved, saved: true)
        } else {
            startDiscovery()
        }
    }

    /// 回到前台：检查「设置」里的「重新查找 Mac」，以及外壳有没有更新。
    func didEnterForeground() {
        guard isViewLoaded else { return }
        if ShellSettings.takeRediscover() {
            ShellSettings.forget()
            startDiscovery()
            return
        }
        if loaded, let mac = current {
            checkUpdate(mac)
        }
    }

    func connect(to mac: MacAddress, saved: Bool = false) {
        loadViewIfNeeded()
        stopDiscovery()
        current = mac
        fromSaved = saved
        loaded = false
        bridgeActive = false
        status.showConnecting(mac)
        webView.load(URLRequest(url: mac.pageURL, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 8))
    }

    func startDiscovery() {
        loadViewIfNeeded()
        stopDiscovery()
        found = []
        listing = false
        status.showSearching()
        discovery.onChange = { [weak self] macs in
            self?.discovered(macs)
        }
        discovery.start()
        let timeout = DispatchWorkItem { [weak self] in
            guard let self = self, self.found.isEmpty else { return }
            self.status.showNotFound(retry: { [weak self] in self?.startDiscovery() })
        }
        searchTimeout = timeout
        DispatchQueue.main.asyncAfter(deadline: .now() + 5, execute: timeout)
    }

    private func stopDiscovery() {
        discovery.stop()
        discovery.onChange = nil
        searchTimeout?.cancel()
        searchTimeout = nil
        settle?.cancel()
        settle = nil
    }

    /// 找到一台就直接连；多台就列出来让用户点选。第一台出现之后稍等一下，
    /// 看还有没有别的 Mac 跟着出现，免得有两台时直接连上了先报到的那一台。
    private func discovered(_ macs: [MacAddress]) {
        found = macs
        if macs.isEmpty {
            return
        }
        if listing || macs.count > 1 {
            showList()
            return
        }
        if settle == nil {
            let work = DispatchWorkItem { [weak self] in
                guard let self = self else { return }
                self.settle = nil
                if self.found.count == 1 {
                    self.connect(to: self.found[0])
                } else if self.found.count > 1 {
                    self.showList()
                }
            }
            settle = work
            DispatchQueue.main.asyncAfter(deadline: .now() + 1, execute: work)
        }
    }

    private func showList() {
        listing = true
        settle?.cancel()
        settle = nil
        status.showList(found) { [weak self] mac in
            self?.connect(to: mac)
        }
    }

    // MARK: - 页面加载

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        // 新页面要重新握手，握手之前不发采样
        bridgeActive = false
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard let mac = current else { return }
        loaded = true
        fromSaved = false
        ShellSettings.save(mac)
        status.isHidden = true
        checkUpdate(mac)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        failed(error)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        failed(error)
    }

    private func failed(_ error: Error) {
        let nsError = error as NSError
        if nsError.domain == NSURLErrorDomain && nsError.code == NSURLErrorCancelled {
            return
        }
        loaded = false
        // 保存的地址连不上（例如 Mac 的端口因为被占用而顺延了）：转去 Bonjour 查找
        if fromSaved {
            fromSaved = false
            startDiscovery()
            return
        }
        status.showError(
            error.localizedDescription,
            retry: { [weak self] in
                guard let self = self, let mac = self.current else { return }
                self.connect(to: mac)
            },
            rediscover: { [weak self] in
                self?.startDiscovery()
            }
        )
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        bridgeActive = false
        webView.reload()
    }

    /// 页面里点到非 http 的链接（例如安装页）交给系统打开。
    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
    ) {
        guard let url = navigationAction.request.url, let scheme = url.scheme?.lowercased() else {
            decisionHandler(.allow)
            return
        }
        if ["http", "https", "about", "blob", "data"].contains(scheme) {
            decisionHandler(.allow)
            return
        }
        UIApplication.shared.open(url)
        decisionHandler(.cancel)
    }

    /// ``window.open``（例如「前往 Release 页面」）：交给 Safari。
    func webView(
        _ webView: WKWebView,
        createWebViewWith configuration: WKWebViewConfiguration,
        for navigationAction: WKNavigationAction,
        windowFeatures: WKWindowFeatures
    ) -> WKWebView? {
        if let url = navigationAction.request.url {
            UIApplication.shared.open(url)
        }
        return nil
    }

    // MARK: - 与网页的接口（docs/ipad-shell.md 第 5、6 节）

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], body["type"] as? String == "hello" else { return }
        let range = (body["bridge"] as? [NSNumber])?.map { $0.intValue } ?? []
        let bridge = PencilTracker.bridge
        let supported = range.count == 2 && range[0] <= bridge && bridge <= range[1]
        bridgeActive = supported
        let reply: [String: Any] = ["shellVersion": ShellVersion.current, "bridge": bridge, "active": supported]
        if let data = try? JSONSerialization.data(withJSONObject: reply),
           let json = String(data: data, encoding: .utf8)
        {
            webView.evaluateJavaScript("window.whiteboardShell && window.whiteboardShell.hello(\(json))")
        }
        if !supported {
            warnIncompatible()
        }
    }

    private func send(_ json: String) {
        guard bridgeActive else { return }
        let started = CACurrentMediaTime()
        webView.evaluateJavaScript("window.whiteboardShell && window.whiteboardShell.receive(\(json))") { [weak self] _, _ in
            // 文档 12 节 Q3：每次调用的耗时。在 Mac 的「控制台」App 里看这台 iPad 的日志
            self?.evalStats.note((CACurrentMediaTime() - started) * 1000)
        }
    }

    private func warnIncompatible() {
        guard !warnedIncompatible else { return }
        warnedIncompatible = true
        present(
            alert(title: "外壳版本与白板版本不兼容，请更新外壳", message: "在更新之前，Pencil 按 Safari 的方式输入。"),
            animated: true
        )
        if let mac = current {
            checkUpdate(mac)
        }
    }

    // MARK: - 更新（docs/ipad-shell.md 8.3 节）

    private func checkUpdate(_ mac: MacAddress) {
        updates.check(mac) { [weak self] version in
            self?.offerUpdate(version, from: mac)
        }
    }

    private func offerUpdate(_ version: String, from mac: MacAddress) {
        guard presentedViewController == nil else { return }
        let dialog = UIAlertController(title: "有新版本 \(version)，是否更新？", message: nil, preferredStyle: .alert)
        dialog.addAction(UIAlertAction(title: "以后再说", style: .cancel) { [weak self] _ in
            self?.updates.decline()
        })
        dialog.addAction(UIAlertAction(title: "更新", style: .default) { _ in
            // 交给 TrollStore 下载并安装，需要在 TrollStore 的设置里打开 URL Scheme
            UIApplication.shared.open(mac.installURL) { opened in
                if !opened {
                    NSLog("白板外壳：打不开 TrollStore 的安装链接")
                }
            }
        })
        present(dialog, animated: true)
    }

    private func alert(title: String, message: String) -> UIAlertController {
        let dialog = UIAlertController(title: title, message: message, preferredStyle: .alert)
        dialog.addAction(UIAlertAction(title: "好", style: .default))
        return dialog
    }

    // MARK: - Scribble

    func scribbleInteraction(_ interaction: UIScribbleInteraction, shouldBeginAt location: CGPoint) -> Bool {
        false
    }
}

/// WKUserContentController 会强引用消息处理者，中间隔一层弱引用，免得视图控制器永远释放不掉。
@MainActor
final class WeakScriptHandler: NSObject, WKScriptMessageHandler {
    private weak var target: WKScriptMessageHandler?

    init(_ target: WKScriptMessageHandler) {
        self.target = target
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        target?.userContentController(userContentController, didReceive: message)
    }
}

/// evaluateJavaScript 的耗时统计，每 5 秒写一行日志。
struct EvalStats {
    private var count = 0
    private var total = 0.0
    private var worst = 0.0
    private var since = CACurrentMediaTime()

    mutating func note(_ ms: Double) {
        count += 1
        total += ms
        worst = max(worst, ms)
        let now = CACurrentMediaTime()
        if now - since >= 5 {
            NSLog("白板外壳：evaluateJavaScript %d 次，平均 %.2f ms，最长 %.2f ms", count, total / Double(count), worst)
            count = 0
            total = 0
            worst = 0
            since = now
        }
    }
}
