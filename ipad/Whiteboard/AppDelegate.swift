import UIKit

@main
final class AppDelegate: UIResponder, UIApplicationDelegate {
    var window: UIWindow?
    private let shell = ShellViewController()

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
    ) -> Bool {
        // 窗口在分发之前先把每个触摸事件交给外壳的 Pencil 采集
        let window = ShellWindow(frame: UIScreen.main.bounds)
        window.tracker = shell.tracker
        window.rootViewController = shell
        window.makeKeyAndVisible()
        self.window = window
        ShellSettings.publishVersion()
        // 由安装页的「打开外壳」启动时，系统随后会调 application(_:open:options:)，
        // 在那里连接；这里不再先去连保存的地址
        if launchOptions?[.url] == nil {
            shell.start()
        }
        return true
    }

    /// ``whiteboard-shell://connect?host=<主机名>.local&port=<端口>``：保存地址并连接。
    func application(
        _ app: UIApplication,
        open url: URL,
        options: [UIApplication.OpenURLOptionsKey: Any] = [:]
    ) -> Bool {
        guard let mac = MacAddress(connectURL: url) else {
            shell.startIfIdle()
            return false
        }
        shell.connect(to: mac)
        return true
    }

    func applicationWillEnterForeground(_ application: UIApplication) {
        shell.didEnterForeground()
    }
}
