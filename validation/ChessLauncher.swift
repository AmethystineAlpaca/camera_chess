import AppKit
import Foundation

final class ChessPanel: NSPanel {
    override var canBecomeKey: Bool { true }
}
final class Launcher: NSObject, NSApplicationDelegate {
    var panel: ChessPanel!
    var status: NSTextField!
    var piece: NSButton!
    var timer: Timer?
    var heartbeatTimer: DispatchSourceTimer?
    var service: Process?
    var busy = false
    var ready = false
    var quitting = false
    var opening = false
    var failures = 0
    var started = Date()
    let root = URL(fileURLWithPath: Bundle.main.bundlePath).deletingLastPathComponent()
    let base = "http://127.0.0.1:8769"

    func trace(_ text: String) {
        let line = "\(Date()) \(text)\n"
        let path = root.appendingPathComponent("validation/results/lite-live/launcher-events.log")
        if !FileManager.default.fileExists(atPath: path.path) { FileManager.default.createFile(atPath: path.path, contents: nil) }
        if let log = try? FileHandle(forWritingTo: path) { log.seekToEndOfFile(); log.write(Data(line.utf8)); try? log.close() }
    }
    func api(_ path: String, data: [String: Any]? = nil, completion: @escaping ([String: Any]?) -> Void) {
        var request = URLRequest(url: URL(string: base + path)!)
        request.timeoutInterval = 3
        if let data = data {
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try? JSONSerialization.data(withJSONObject: data)
        }
        URLSession.shared.dataTask(with: request) { bytes, response, _ in
            let ok = (response as? HTTPURLResponse)?.statusCode == 200
            let result = ok ? bytes.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } : nil
            DispatchQueue.main.async { completion(result) }
        }.resume()
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1200, height: 800)
        panel = ChessPanel(contentRect: NSRect(x: screen.maxX-132, y: screen.midY, width: 112, height: 126), styleMask: [.borderless], backing: .buffered, defer: false)
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.isFloatingPanel = true
        panel.hidesOnDeactivate = false
        panel.isMovableByWindowBackground = true
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        let view = NSView(frame: panel.contentView!.bounds)
        view.wantsLayer = true
        view.layer?.backgroundColor = NSColor(calibratedRed: 0.09, green: 0.16, blue: 0.20, alpha: 0.97).cgColor
        view.layer?.cornerRadius = 18
        panel.contentView = view
        piece = NSButton(title: "♞", target: self, action: #selector(openGame))
        piece.frame = NSRect(x: 22, y: 38, width: 68, height: 68)
        piece.isBordered = false
        piece.font = NSFont.systemFont(ofSize: 54)
        piece.contentTintColor = NSColor(calibratedRed: 0.49, green: 0.88, blue: 0.74, alpha: 1)
        piece.toolTip = "Open Camera Chess. Drag the background to move."
        piece.setAccessibilityLabel("Open Camera Chess")
        piece.isEnabled = false
        view.addSubview(piece)
        let close = NSButton(title: "×", target: self, action: #selector(confirmQuit))
        close.frame = NSRect(x: 82, y: 96, width: 24, height: 24)
        close.isBordered = false
        close.font = NSFont.systemFont(ofSize: 20)
        close.contentTintColor = .white
        close.toolTip = "Quit Camera Chess and stop all background processes"
        close.setAccessibilityLabel("Quit Camera Chess")
        view.addSubview(close)
        status = NSTextField(labelWithString: "Starting…")
        status.frame = NSRect(x: 6, y: 12, width: 100, height: 20)
        status.alignment = .center
        status.textColor = .white
        status.font = .systemFont(ofSize: 12)
        view.addSubview(status)
        panel.orderFrontRegardless()
        // Keep the lease alive even while a modal confirmation is waiting for input.
        let leaseTimer = DispatchSource.makeTimerSource(queue: DispatchQueue.global(qos: .utility))
        leaseTimer.schedule(deadline: .now(), repeating: 2)
        let heartbeatURL = URL(string: base + "/api/launcher-heartbeat")!
        leaseTimer.setEventHandler {
            var request = URLRequest(url: heartbeatURL)
            request.httpMethod = "POST"
            request.timeoutInterval = 3
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = Data("{}".utf8)
            URLSession.shared.dataTask(with: request).resume()
        }
        heartbeatTimer = leaseTimer
        leaseTimer.resume()
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { _ in self.poll() }
        api("/api/app-status") { result in
            if result != nil { self.poll() }
            else { self.startService() }
        }
    }
    func startService() {
        let process = Process()
        process.executableURL = root.appendingPathComponent("research/edgetam-env/bin/python")
        process.arguments = [root.appendingPathComponent("validation/session_lock.py").path]
        process.currentDirectoryURL = root
        var env = ProcessInfo.processInfo.environment
        env["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
        env["CHESS_LAUNCHER"] = "1"
        process.environment = env
        let logURL = root.appendingPathComponent("validation/results/lite-live/launcher.log")
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        if let log = try? FileHandle(forWritingTo: logURL) {
            log.seekToEndOfFile()
            process.standardOutput = log
            process.standardError = log
        }
        do { try process.run(); service = process; poll() }
        catch { showError("Could not start Camera Chess", detail: error.localizedDescription); finish() }
    }
    func poll() {
        if busy || quitting { return }
        busy = true
        api("/api/app-status") { result in
            self.busy = false
            if let running = result?["running"] as? Bool {
                if !running { self.finish(); return }
                self.ready = true
                self.failures = 0
                self.piece.isEnabled = true
                self.status.stringValue = "Open chess"
            } else {
                self.failures += 1
                if self.ready && self.failures >= 2 { self.finish() }
                else if !self.ready && Date().timeIntervalSince(self.started) > 30 {
                    self.showError("Could not start Camera Chess", detail: "See validation/results/lite-live/launcher.log for details.")
                    self.finish()
                }
            }
        }
    }
    @objc func openGame() {
        guard ready && !quitting && !opening else { return }
        opening = true
        api("/api/browser-entry", data: [:]) { result in
            self.opening = false
            if result?["already_open"] as? Bool == true {
                // Ask the live page to focus itself; also bring its browser to the front.
                if let url = URL(string: self.base), let app = NSWorkspace.shared.urlForApplication(toOpen: url) {
                    if let browser = NSRunningApplication.runningApplications(withBundleIdentifier: Bundle(url: app)?.bundleIdentifier ?? "").first {
                        browser.activate(options: [])
                    }
                }
            } else {
                NSWorkspace.shared.open(URL(string: self.base + "/")!)
            }
        }
    }
    @objc func confirmQuit() {
        guard !quitting else { return }
        trace("confirm requested")
        quitting = true
        api("/api/game") { game in
            let alert = NSAlert()
            alert.messageText = "Quit Camera Chess?"
            alert.informativeText = "This closes the camera, sound and all chess background processes."
            let canSave = game?["can_save"] as? Bool == true
            if canSave {
                alert.addButton(withTitle: "Save and Quit")
                alert.addButton(withTitle: "Quit Without Saving")
                alert.addButton(withTitle: "Cancel")
            } else {
                alert.addButton(withTitle: "Quit Application")
                alert.addButton(withTitle: "Cancel")
            }
            NSApp.activate(ignoringOtherApps: true)
            let choice = alert.runModal()
            self.trace("confirmation choice \(choice.rawValue)")
            if choice == .alertThirdButtonReturn || (!canSave && choice == .alertSecondButtonReturn) {
                self.quitting = false
                return
            }
            var data: [String: Any] = ["save": canSave && choice == .alertFirstButtonReturn]
            if let session = game?["session"] { data["session"] = session }
            self.api("/api/quit-app", data: data) { result in
                if result != nil { self.waitForStop() }
                else {
                    self.quitting = false
                    self.showError("Could not quit", detail: "Saving or stopping the service failed. Please try again. Camera Chess is still open.")
                }
            }
        }
    }
    func waitForStop() {
        status.stringValue = "Closing…"
        piece.isEnabled = false
        api("/api/app-status") { result in
            if result == nil { self.finish() }
            else { DispatchQueue.main.asyncAfter(deadline: .now()+0.5) { self.waitForStop() } }
        }
    }
    func showError(_ title: String, detail: String) {
        let alert = NSAlert(); alert.messageText = title; alert.informativeText = detail
        alert.addButton(withTitle: "OK"); NSApp.activate(ignoringOtherApps: true); alert.runModal()
    }
    func applicationWillTerminate(_ notification: Notification) {
        if let process = service, process.isRunning { process.terminate() }
    }
    func finish() {
        trace("finish")
        timer?.invalidate()
        heartbeatTimer?.cancel()
        quitting = true
        NSApp.terminate(nil)
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        trace("applicationShouldTerminate quitting=\(quitting)")
        if quitting { return .terminateNow }
        confirmQuit()
        return .terminateCancel
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        panel.orderFrontRegardless(); openGame(); return true
    }
}
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = Launcher()
app.delegate = delegate
app.run()
