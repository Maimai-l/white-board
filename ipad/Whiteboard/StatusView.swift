import UIKit

/// 盖在网页上面的状态页：查找 Mac、列出多台 Mac、找不到时的说明、加载失败。
final class StatusView: UIView, UITableViewDataSource, UITableViewDelegate {
    private let title = UILabel()
    private let message = UILabel()
    private let spinner = UIActivityIndicatorView(style: .large)
    private let table = UITableView(frame: .zero, style: .insetGrouped)
    private let buttons = UIStackView()
    private var macs: [MacAddress] = []
    private var onPick: ((MacAddress) -> Void)?

    override init(frame: CGRect) {
        super.init(frame: frame)
        backgroundColor = .systemGroupedBackground

        title.font = .preferredFont(forTextStyle: .title1)
        title.textAlignment = .center
        title.numberOfLines = 0
        message.font = .preferredFont(forTextStyle: .body)
        message.textColor = .secondaryLabel
        message.textAlignment = .center
        message.numberOfLines = 0
        table.dataSource = self
        table.delegate = self
        table.register(UITableViewCell.self, forCellReuseIdentifier: "mac")
        table.isHidden = true
        table.backgroundColor = .clear
        buttons.axis = .horizontal
        buttons.spacing = 16
        buttons.alignment = .center

        let stack = UIStackView(arrangedSubviews: [spinner, title, message, table, buttons])
        stack.axis = .vertical
        stack.spacing = 16
        stack.alignment = .center
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: centerYAnchor),
            stack.widthAnchor.constraint(equalToConstant: 520),
            title.widthAnchor.constraint(equalTo: stack.widthAnchor),
            message.widthAnchor.constraint(equalTo: stack.widthAnchor),
            table.widthAnchor.constraint(equalTo: stack.widthAnchor),
            table.heightAnchor.constraint(equalToConstant: 280),
        ])
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    // MARK: - 几种状态

    func showSearching() {
        show(title: "正在查找 Mac", message: "请确认 Mac 上的白板已经打开，并且和这台 iPad 在同一个网络里。", busy: true)
    }

    func showConnecting(_ mac: MacAddress) {
        show(title: "正在连接", message: mac.label, busy: true)
    }

    /// 找到多台 Mac：列出名称，由用户点选。已经连上之后再来换一台的，多给一个取消。
    func showList(
        _ macs: [MacAddress],
        onPick: @escaping (MacAddress) -> Void,
        cancel: (() -> Void)? = nil
    ) {
        show(title: "选择一台 Mac", message: "局域网里有 \(macs.count) 台 Mac 在运行白板。", busy: false)
        self.macs = macs
        self.onPick = onPick
        table.isHidden = false
        table.reloadData()
        if let cancel = cancel {
            addButton("取消", action: cancel)
        }
    }

    /// 5 秒内一台都没找到：路由器可能屏蔽了 Bonjour，改走安装页的「打开外壳」。
    func showNotFound(retry: @escaping () -> Void) {
        show(
            title: "没有找到 Mac",
            message: "在 Mac 的白板窗口中打开「连接 iPad」，用这台 iPad 的 Safari 打开卡片上的外壳安装页地址，"
                + "点「打开外壳」即可进入白板。\n\n外壳会继续在后台查找。",
            busy: false
        )
        addButton("重新查找", action: retry)
    }

    func showError(_ text: String, retry: @escaping () -> Void, rediscover: @escaping () -> Void) {
        show(title: "连不上白板", message: text, busy: false)
        addButton("重试", action: retry)
        addButton("重新查找 Mac", action: rediscover)
    }

    private func show(title text: String, message detail: String, busy: Bool) {
        isHidden = false
        title.text = text
        message.text = detail
        if busy {
            spinner.startAnimating()
        } else {
            spinner.stopAnimating()
        }
        spinner.isHidden = !busy
        table.isHidden = true
        macs = []
        onPick = nil
        for view in buttons.arrangedSubviews {
            view.removeFromSuperview()
        }
    }

    private func addButton(_ text: String, action: @escaping () -> Void) {
        var config = UIButton.Configuration.filled()
        config.title = text
        config.cornerStyle = .large
        let button = UIButton(configuration: config, primaryAction: UIAction { _ in action() })
        buttons.addArrangedSubview(button)
    }

    // MARK: - 列表

    func tableView(_ tableView: UITableView, numberOfRowsInSection section: Int) -> Int {
        macs.count
    }

    func tableView(_ tableView: UITableView, cellForRowAt indexPath: IndexPath) -> UITableViewCell {
        let cell = tableView.dequeueReusableCell(withIdentifier: "mac", for: indexPath)
        let mac = macs[indexPath.row]
        var content = cell.defaultContentConfiguration()
        content.text = mac.name ?? mac.host
        content.secondaryText = "\(mac.host):\(mac.port)"
        content.image = UIImage(systemName: "desktopcomputer")
        cell.contentConfiguration = content
        cell.accessoryType = .disclosureIndicator
        return cell
    }

    func tableView(_ tableView: UITableView, didSelectRowAt indexPath: IndexPath) {
        tableView.deselectRow(at: indexPath, animated: true)
        guard indexPath.row < macs.count else { return }
        onPick?(macs[indexPath.row])
    }
}
