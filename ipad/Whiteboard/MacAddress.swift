import Foundation

/// 一台运行白板的 Mac：主机名（``xxx.local``）和端口。
struct MacAddress: Equatable {
    let host: String
    let port: Int
    var name: String?

    init?(host: String, port: Int, name: String? = nil) {
        // 主机名要拼进 URL，只收字母、数字、点和连字符，别让别的东西混进地址里
        let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-")
        guard !host.isEmpty, host.unicodeScalars.allSatisfy({ allowed.contains($0) }),
              (1...65535).contains(port)
        else {
            return nil
        }
        self.host = host
        self.port = port
        self.name = name
    }

    /// ``whiteboard-shell://connect?host=<主机名>.local&port=<端口>``，安装页的「打开外壳」按钮。
    init?(connectURL url: URL) {
        guard url.scheme == "whiteboard-shell", url.host == "connect",
              let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems,
              let host = items.first(where: { $0.name == "host" })?.value,
              let portText = items.first(where: { $0.name == "port" })?.value,
              let port = Int(portText)
        else {
            return nil
        }
        self.init(host: host, port: port)
    }

    var label: String {
        if let name = name, !name.isEmpty {
            return "\(name)（\(host):\(port)）"
        }
        return "\(host):\(port)"
    }

    func url(_ path: String) -> URL {
        URL(string: "http://\(host):\(port)\(path)")!
    }

    /// ``role=ipad`` 让服务端返回 iPad 界面（服务端的 detect_role 已经支持）。
    var pageURL: URL { url("/?role=ipad") }

    /// TrollStore 1.3 起提供的 URL 安装接口。
    var installURL: URL {
        URL(string: "apple-magnifier://install?url=http://\(host):\(port)/ipad/Whiteboard.ipa")!
    }
}

/// 本机保存的东西：上次连上的 Mac，以及「设置」App 里外壳那一页的几项。
enum ShellSettings {
    private static let hostKey = "mac.host"
    private static let portKey = "mac.port"
    private static let nameKey = "mac.name"
    // 这三个 key 与 Settings.bundle/Root.plist 里的一致
    private static let versionKey = "shell_version"
    private static let currentKey = "shell_current_mac"
    private static let rediscoverKey = "shell_rediscover"

    static var defaults: UserDefaults { .standard }

    static func savedMac() -> MacAddress? {
        guard let host = defaults.string(forKey: hostKey) else { return nil }
        return MacAddress(host: host, port: defaults.integer(forKey: portKey), name: defaults.string(forKey: nameKey))
    }

    static func save(_ mac: MacAddress) {
        defaults.set(mac.host, forKey: hostKey)
        defaults.set(mac.port, forKey: portKey)
        defaults.set(mac.name, forKey: nameKey)
        defaults.set(mac.label, forKey: currentKey)
    }

    static func forget() {
        defaults.removeObject(forKey: hostKey)
        defaults.removeObject(forKey: portKey)
        defaults.removeObject(forKey: nameKey)
        defaults.set("未连接", forKey: currentKey)
    }

    /// 把版本号写给「设置」App 显示。每次启动都写，更新之后显示的就是新版本号。
    static func publishVersion() {
        defaults.set(ShellVersion.current, forKey: versionKey)
    }

    /// 用户在「设置」里打开了「重新查找 Mac」：取出来并复位开关。
    static func takeRediscover() -> Bool {
        guard defaults.bool(forKey: rediscoverKey) else { return false }
        defaults.set(false, forKey: rediscoverKey)
        return true
    }
}

/// 外壳自己的版本号，与 Mac 端相同，由同一个 git tag 决定。
enum ShellVersion {
    static var current: String {
        (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? "0.0.0"
    }

    /// 按数字逐段比较，``1.10.0`` 比 ``1.9.3`` 新；段里的非数字后缀忽略。
    static func isNewer(_ candidate: String, than current: String) -> Bool {
        let a = parts(candidate)
        let b = parts(current)
        for index in 0..<max(a.count, b.count) {
            let x = index < a.count ? a[index] : 0
            let y = index < b.count ? b[index] : 0
            if x != y {
                return x > y
            }
        }
        return false
    }

    private static func parts(_ text: String) -> [Int] {
        let trimmed = text.hasPrefix("v") || text.hasPrefix("V") ? String(text.dropFirst()) : text
        return trimmed.split(separator: ".").map { part in
            Int(part.prefix(while: { $0.isNumber })) ?? 0
        }
    }
}
