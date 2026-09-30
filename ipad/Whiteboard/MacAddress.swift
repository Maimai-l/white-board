import Foundation

/// 局域网里一个可以连接的服务：主机名（``xxx.local``）、端口、名字和入口路径。
///
/// 白板应用是其中一个（名字 ``whiteboard``），其他项目（例如 ``qb``）注册同一种
/// Bonjour 服务后也能被外壳连上（docs/ipad-shell.md 8.4 节）。
struct MacAddress: Equatable {
    static let defaultSource = "whiteboard"
    /// ``role=ipad`` 让白板服务端返回 iPad 界面（服务端的 detect_role 已经支持）。
    static let defaultPath = "/?role=ipad"

    let host: String
    let port: Int
    var name: String?
    /// 服务的名字（TXT 记录的 ``source``）。旧版白板不带这一项，按 ``whiteboard`` 处理。
    let source: String
    /// 入口页面的路径（TXT 记录的 ``path``）。
    let path: String

    init?(host: String, port: Int, name: String? = nil, source: String? = nil, path: String? = nil) {
        // 主机名要拼进 URL，只收字母、数字、点和连字符，别让别的东西混进地址里
        let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-")
        guard !host.isEmpty, host.unicodeScalars.allSatisfy({ allowed.contains($0) }),
              (1...65535).contains(port),
              let source = MacAddress.cleanSource(source ?? MacAddress.defaultSource),
              let path = MacAddress.cleanPath(path ?? MacAddress.defaultPath)
        else {
            return nil
        }
        self.host = host
        self.port = port
        self.name = name
        self.source = source
        self.path = path
    }

    /// ``whiteboard-shell://connect?host=<主机名>.local&port=<端口>[&source=…][&path=…]``，
    /// 安装页的「打开外壳」按钮。
    init?(connectURL url: URL) {
        guard url.scheme == "whiteboard-shell", url.host == "connect",
              let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems,
              let host = items.first(where: { $0.name == "host" })?.value,
              let portText = items.first(where: { $0.name == "port" })?.value,
              let port = Int(portText)
        else {
            return nil
        }
        self.init(
            host: host,
            port: port,
            source: items.first(where: { $0.name == "source" })?.value,
            path: items.first(where: { $0.name == "path" })?.value
        )
    }

    /// 服务名：去掉开头的 ``@`` 和空白，转成小写；只收 ``[a-z0-9-]``，1 到 32 个字符。
    static func cleanSource(_ raw: String) -> String? {
        var text = raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if text.hasPrefix("@") {
            text.removeFirst()
        }
        let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyz0123456789-")
        guard (1...32).contains(text.count), text.unicodeScalars.allSatisfy({ allowed.contains($0) }) else {
            return nil
        }
        return text
    }

    /// 入口路径：以 ``/`` 开头、不含 ``//``，只收 URL 里常见的字符，最长 256 个字符。
    static func cleanPath(_ raw: String) -> String? {
        let allowed = CharacterSet(
            charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~/?=&%+"
        )
        guard raw.hasPrefix("/"), raw.count <= 256, !raw.contains("//"),
              raw.unicodeScalars.allSatisfy({ allowed.contains($0) })
        else {
            return nil
        }
        return raw
    }

    var label: String {
        let place = "\(host):\(port)"
        // 白板本身不加前缀，和以前显示的一样；其他服务在前面标出 @名字
        let tag = source == MacAddress.defaultSource ? "" : "@\(source) · "
        if let name = name, !name.isEmpty {
            return "\(tag)\(name)（\(place)）"
        }
        return "\(tag)\(place)"
    }

    func url(_ path: String) -> URL {
        URL(string: "http://\(host):\(port)\(path)")!
    }

    var pageURL: URL { url(path) }

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
    private static let sourceKey = "mac.source"
    private static let pathKey = "mac.path"
    // 这四个 key 与 Settings.bundle/Root.plist 里的一致
    private static let versionKey = "shell_version"
    private static let currentKey = "shell_current_mac"
    private static let rediscoverKey = "shell_rediscover"
    private static let wantedKey = "shell_source"

    static var defaults: UserDefaults { .standard }

    static func savedMac() -> MacAddress? {
        guard let host = defaults.string(forKey: hostKey) else { return nil }
        return MacAddress(
            host: host,
            port: defaults.integer(forKey: portKey),
            name: defaults.string(forKey: nameKey),
            source: defaults.string(forKey: sourceKey),
            path: defaults.string(forKey: pathKey)
        )
    }

    static func save(_ mac: MacAddress) {
        defaults.set(mac.host, forKey: hostKey)
        defaults.set(mac.port, forKey: portKey)
        defaults.set(mac.name, forKey: nameKey)
        defaults.set(mac.source, forKey: sourceKey)
        defaults.set(mac.path, forKey: pathKey)
        defaults.set(mac.label, forKey: currentKey)
    }

    static func forget() {
        defaults.removeObject(forKey: hostKey)
        defaults.removeObject(forKey: portKey)
        defaults.removeObject(forKey: nameKey)
        defaults.removeObject(forKey: sourceKey)
        defaults.removeObject(forKey: pathKey)
        defaults.set("未连接", forKey: currentKey)
    }

    /// 「设置」里「来源」一项：要连的服务名（``@qb`` 或 ``qb``）。留空或写得不对时为 nil，表示不限。
    static func wantedSource() -> String? {
        guard let raw = defaults.string(forKey: wantedKey) else { return nil }
        return MacAddress.cleanSource(raw)
    }

    static func setWantedSource(_ source: String?) {
        defaults.set(source.map { "@\($0)" } ?? "", forKey: wantedKey)
    }

    /// 这个服务是不是用户要连的那一个。
    static func accepts(_ mac: MacAddress) -> Bool {
        guard let wanted = wantedSource() else { return true }
        return mac.source == wanted
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
    /// 预发布后缀（``1.0.0-rc.1`` 的 ``-rc.1``）整个不算：外壳自己的版本号只有
    /// 数字（CFBundleShortVersionString），带着后缀比的话 1.0.0-rc.1 会被当成比
    /// 1.0.0 新，同一个版本每次打开都提示更新。
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
        let unprefixed = text.hasPrefix("v") || text.hasPrefix("V") ? String(text.dropFirst()) : text
        let trimmed = unprefixed.split(separator: "-", maxSplits: 1).first.map(String.init) ?? ""
        return trimmed.split(separator: ".").map { part in
            Int(part.prefix(while: { $0.isNumber })) ?? 0
        }
    }
}
