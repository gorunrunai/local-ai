// GoRunRun Local AI: a native Mac window around the local web app on http://127.0.0.1:8000.
//
// The backend runs as a per-user LaunchAgent (ai.gorunrun.local, installed by install.sh or
// `make desktop-install`). Opening the app starts it; quitting stops it unless "Keep Running in
// Background" is on, so the ~20 GB chat model isn't kept in memory while the app is closed.
// The same UI keeps working in any browser at the same address while the backend runs.

import AppKit
import WebKit

let appURL = URL(string: "http://127.0.0.1:8000/")!
let healthURL = URL(string: "http://127.0.0.1:8000/api/health")!
let agentLabel = "ai.gorunrun.local"
let keepRunningKey = "keepBackendRunning"

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate,
                         WKScriptMessageHandler {
    var window: NSWindow!
    var webView: WKWebView!
    var startedBackend = false
    var pollTimer: Timer?
    var waitedSeconds = 0

    func applicationDidFinishLaunching(_ note: Notification) {
        buildMenu()
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()          // keeps the login cookie and settings
        config.preferences.isElementFullscreenEnabled = true
        config.mediaTypesRequiringUserActionForPlayback = []
        config.userContentController.add(self, name: "share")   // Settings → Phone access → Share
        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = true
        webView.setValue(false, forKey: "drawsBackground")

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 860),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                          backing: .buffered, defer: false)
        window.title = "GoRunRun Local AI"
        window.minSize = NSSize(width: 420, height: 520)
        window.contentView = webView
        window.setFrameAutosaveName("MainWindow")
        if !window.setFrameUsingName("MainWindow") { window.center() }
        window.makeKeyAndOrderFront(nil)

        showStatus("Starting GoRunRun Local AI…", detail: "Loading the AI model. The first start after installing can take a minute.")
        checkHealth { up in
            if up { self.loadApp() } else { self.startBackend(); self.waitForBackend() }
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ note: Notification) {
        if !UserDefaults.standard.bool(forKey: keepRunningKey) {
            launchctl(["kill", "TERM", "gui/\(getuid())/\(agentLabel)"])
        }
    }

    // MARK: backend lifecycle

    @discardableResult
    func launchctl(_ args: [String]) -> Int32 {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        p.arguments = args
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        do { try p.run(); p.waitUntilExit(); return p.terminationStatus } catch { return -1 }
    }

    func startBackend() {
        let status = launchctl(["kickstart", "gui/\(getuid())/\(agentLabel)"])
        if status != 0 {
            let plist = FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("Library/LaunchAgents/\(agentLabel).plist").path
            launchctl(["bootstrap", "gui/\(getuid())", plist])
            launchctl(["kickstart", "gui/\(getuid())/\(agentLabel)"])
        }
        startedBackend = true
    }

    func checkHealth(_ done: @escaping (Bool) -> Void) {
        var req = URLRequest(url: healthURL)
        req.timeoutInterval = 2
        URLSession.shared.dataTask(with: req) { _, resp, _ in
            let ok = (resp as? HTTPURLResponse)?.statusCode == 200
            DispatchQueue.main.async { done(ok) }
        }.resume()
    }

    func waitForBackend() {
        waitedSeconds = 0
        pollTimer?.invalidate()
        pollTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] t in
            guard let self else { return }
            self.waitedSeconds += 1
            self.checkHealth { up in
                if up { t.invalidate(); self.loadApp(); return }
                if self.waitedSeconds == 180 {
                    t.invalidate()
                    self.showStatus("GoRunRun Local AI didn't start",
                                    detail: "Check the log at ~/.gorunrun/local/data/logs/backend.log, then choose View → Retry.",
                                    error: true)
                }
            }
        }
    }

    func loadApp() {
        webView.load(URLRequest(url: appURL))
    }

    func showStatus(_ title: String, detail: String, error: Bool = false) {
        let spinner = error ? "" : "<div class=s></div>"
        let html = """
        <!doctype html><meta charset=utf-8><style>
        :root{color-scheme:light dark} body{margin:0;height:100vh;display:grid;place-items:center;
        font:15px -apple-system,sans-serif;background:#f4f1ea;color:#17171a}
        @media (prefers-color-scheme:dark){body{background:#17171a;color:#f4f1ea}.s{border-color:#f4f1ea22;border-top-color:#f4f1ea}}
        main{text-align:center;max-width:26rem;padding:1rem} h1{font-size:19px;font-weight:600;margin:1rem 0 .4rem}
        p{opacity:.7;margin:0;line-height:1.5} .s{width:28px;height:28px;margin:auto;border-radius:50%;
        border:3px solid #17171a22;border-top-color:#17171a;animation:r 1s linear infinite}@keyframes r{to{transform:rotate(1turn)}}
        </style><main>\(spinner)<h1>\(title)</h1><p>\(detail)</p></main>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    // MARK: menus

    func buildMenu() {
        let main = NSMenu()
        func add(_ title: String, _ items: [NSMenuItem]) {
            let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
            let sub = NSMenu(title: title)
            items.forEach(sub.addItem)
            item.submenu = sub
            main.addItem(item)
        }
        func mi(_ t: String, _ a: Selector?, _ k: String, _ mods: NSEvent.ModifierFlags = .command) -> NSMenuItem {
            let i = NSMenuItem(title: t, action: a, keyEquivalent: k)
            i.keyEquivalentModifierMask = mods
            return i
        }
        let keep = mi("Keep Running in Background", #selector(toggleKeepRunning(_:)), "")
        keep.state = UserDefaults.standard.bool(forKey: keepRunningKey) ? .on : .off
        add("GoRunRun Local AI", [
            mi("About GoRunRun Local AI", #selector(NSApplication.orderFrontStandardAboutPanel(_:)), ""),
            .separator(), keep, .separator(),
            mi("Hide GoRunRun Local AI", #selector(NSApplication.hide(_:)), "h"),
            mi("Quit GoRunRun Local AI", #selector(NSApplication.terminate(_:)), "q"),
        ])
        // Standard edit actions: without these, ⌘C / ⌘V / ⌘A don't reach the web view.
        add("Edit", [
            mi("Undo", Selector(("undo:")), "z"), mi("Redo", Selector(("redo:")), "Z"), .separator(),
            mi("Cut", #selector(NSText.cut(_:)), "x"), mi("Copy", #selector(NSText.copy(_:)), "c"),
            mi("Paste", #selector(NSText.paste(_:)), "v"), mi("Select All", #selector(NSText.selectAll(_:)), "a"),
        ])
        add("View", [
            mi("Reload", #selector(reload), "r"),
            mi("Retry Starting", #selector(retry), ""),
            mi("Open in Browser", #selector(openInBrowser), "b", [.command, .shift]),
            .separator(),
            mi("Enter Full Screen", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control]),
        ])
        add("Window", [
            mi("Minimize", #selector(NSWindow.performMiniaturize(_:)), "m"),
            mi("Close", #selector(NSWindow.performClose(_:)), "w"),
        ])
        add("Help", [
            mi("GoRunRun Local AI Website", #selector(openWebsite), ""),
            mi("Show Logs", #selector(showLogs), ""),
        ])
        NSApp.mainMenu = main
    }

    @objc func toggleKeepRunning(_ sender: NSMenuItem) {
        let on = !UserDefaults.standard.bool(forKey: keepRunningKey)
        UserDefaults.standard.set(on, forKey: keepRunningKey)
        sender.state = on ? .on : .off
    }
    @objc func reload() {
        if webView.url?.host == "127.0.0.1" { webView.reload() } else { loadApp() }
    }
    @objc func retry() {
        showStatus("Starting GoRunRun Local AI…", detail: "Loading the AI model.")
        startBackend(); waitForBackend()
    }
    @objc func openInBrowser() { NSWorkspace.shared.open(appURL) }
    @objc func openWebsite() { NSWorkspace.shared.open(URL(string: "https://local.gorunrun.ai")!) }
    @objc func showLogs() {
        let logs = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".gorunrun/local/data/logs")
        NSWorkspace.shared.open(logs)
    }

    // MARK: share menu

    /// The page asks to share a link (the phone sign-in link): show the Mac's share menu
    /// (Messages, AirDrop, Mail…) next to the button that asked. Only the app's own page may ask.
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "share", message.frameInfo.isMainFrame,
              message.frameInfo.securityOrigin.host == appURL.host,
              let body = message.body as? [String: Any],
              let text = body["url"] as? String, let url = URL(string: text), url.scheme == "https" else { return }
        var rect = NSRect(x: webView.bounds.midX, y: webView.bounds.midY, width: 1, height: 1)
        if let r = body["rect"] as? [Double], r.count == 4 {
            let y = webView.isFlipped ? r[1] : webView.bounds.height - r[1] - r[3]
            rect = NSRect(x: r[0], y: y, width: r[2], height: r[3])
        }
        NSSharingServicePicker(items: [url]).show(relativeTo: rect, of: webView,
                                                  preferredEdge: webView.isFlipped ? .maxY : .minY)
    }

    // MARK: web view behavior

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if action.shouldPerformDownload { decisionHandler(.download); return }
        if let url = action.request.url, let scheme = url.scheme, ["http", "https"].contains(scheme),
           url.host != "127.0.0.1", url.host != "localhost" {
            // External links (sources, docs) open in the default browser.
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let disposition = (response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition") ?? ""
        decisionHandler(disposition.lowercased().hasPrefix("attachment") || !response.canShowMIMEType ? .download : .allow)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let downloads = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
        var dest = downloads.appendingPathComponent(suggestedFilename)
        let base = dest.deletingPathExtension().lastPathComponent, ext = dest.pathExtension
        var n = 1
        while FileManager.default.fileExists(atPath: dest.path) {
            dest = downloads.appendingPathComponent("\(base) (\(n)).\(ext)")
            n += 1
        }
        completionHandler(dest)
    }
    func downloadDidFinish(_ download: WKDownload) {
        NSSound(named: "Glass")?.play()
    }

    // target=_blank links and window.open
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    // <input type=file>
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.beginSheetModal(for: window) { completionHandler($0 == .OK ? panel.urls : nil) }
    }

    // Microphone and camera for voice mode and photo capture: only for the local app itself.
    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        decisionHandler(origin.host == "127.0.0.1" || origin.host == "localhost" ? .grant : .deny)
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let a = NSAlert(); a.messageText = message; a.runModal(); completionHandler()
    }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let a = NSAlert(); a.messageText = message
        a.addButton(withTitle: "OK"); a.addButton(withTitle: "Cancel")
        completionHandler(a.runModal() == .alertFirstButtonReturn)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { recover() }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        recover()
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { loadApp() }

    /// The backend went away (e.g. restarted): show the status screen and wait for it again.
    func recover() {
        checkHealth { up in
            if up { return }
            self.showStatus("Reconnecting…", detail: "Waiting for GoRunRun Local AI to start.")
            self.startBackend()
            self.waitForBackend()
        }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.activate(ignoringOtherApps: true)
app.run()
