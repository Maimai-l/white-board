import Foundation
import Network

/// 用 Bonjour 查找局域网内可以连接的服务（``_whiteboard._tcp``，docs/ipad-shell.md 8.4 节）。
///
/// 主机名和端口直接取自服务的 TXT 记录，不用再解析一次：Mac 端写进去的就是
/// ``xxx.local`` 和实际监听的端口（端口被占用顺延时是顺延后的值）。``source`` 和
/// ``path`` 是服务名和入口路径，白板之外的服务（例如刷题）靠它们区分。
final class MacDiscovery {
    static let serviceType = "_whiteboard._tcp"

    /// 每次结果变化时在主线程调用，按名称排好序。
    var onChange: (([MacAddress]) -> Void)?

    private var browser: NWBrowser?

    func start() {
        stop()
        let parameters = NWParameters()
        parameters.includePeerToPeer = false
        let browser = NWBrowser(
            for: .bonjourWithTXTRecord(type: MacDiscovery.serviceType, domain: nil),
            using: parameters
        )
        browser.browseResultsChangedHandler = { [weak self] results, _ in
            let macs = results.compactMap(MacDiscovery.address(of:))
                .sorted { ($0.name ?? $0.host) < ($1.name ?? $1.host) }
            DispatchQueue.main.async {
                self?.onChange?(macs)
            }
        }
        browser.stateUpdateHandler = { state in
            if case let .failed(error) = state {
                NSLog("白板外壳：Bonjour 查找失败 %@", String(describing: error))
            }
        }
        browser.start(queue: .main)
        self.browser = browser
    }

    func stop() {
        browser?.cancel()
        browser = nil
    }

    private static func address(of result: NWBrowser.Result) -> MacAddress? {
        guard case let .bonjour(txt) = result.metadata,
              let host = txt["host"],
              let portText = txt["port"],
              let port = Int(portText)
        else {
            return nil
        }
        var name = txt["name"]
        if name == nil || name?.isEmpty == true, case let .service(serviceName, _, _, _) = result.endpoint {
            name = serviceName
        }
        return MacAddress(host: host, port: port, name: name, source: txt["source"], path: txt["path"])
    }
}

/// 启动时和回到前台时问一下 Mac：它带的外壳是不是比自己新（docs/ipad-shell.md 8.3 节）。
final class UpdateChecker {
    /// 用户点了「以后再说」：本次运行期间不再提示。
    private var declined = false
    private var inFlight = false

    func check(_ mac: MacAddress, found: @escaping (String) -> Void) {
        guard !declined, !inFlight else { return }
        inFlight = true
        let request = URLRequest(
            url: mac.url("/ipad/version"),
            cachePolicy: .reloadIgnoringLocalCacheData,
            timeoutInterval: 5
        )
        URLSession.shared.dataTask(with: request) { [weak self] data, _, _ in
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.inFlight = false
                guard !self.declined,
                      let data = data,
                      let info = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                      info["ipa"] as? Bool == true,
                      let version = info["version"] as? String,
                      ShellVersion.isNewer(version, than: ShellVersion.current)
                else {
                    return
                }
                found(version)
            }
        }.resume()
    }

    func decline() {
        declined = true
    }
}
