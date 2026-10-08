import Foundation
import MobileHealKit
import Observation
import UserNotifications

/// Abstraction so the view model stays unit-testable.
@MainActor
protocol HealAlerts: AnyObject {
    func show(missing: [String], reasons: [String: String])
    func clear()
}

/// Local notifications + an in-app history shown on the Alerts tab.
@MainActor
@Observable
final class HealNotifier: HealAlerts {
    struct Entry: Identifiable, Equatable {
        let id = UUID()
        let date: Date
        let title: String
        let body: String
    }
    private(set) var history: [Entry] = []
    private let center = UNUserNotificationCenter.current()

    func requestPermission() {
        center.requestAuthorization(options: [.alert, .sound, .badge]) { _, _ in }
    }

    func show(missing: [String], reasons: [String: String]) {
        let body = missing.map { reasons[$0] ?? "Add your \($0.titleCased.lowercased())" }.joined(separator: "\n")
        history.insert(Entry(date: .now, title: "Action needed on your profile", body: body), at: 0)
        let content = UNMutableNotificationContent()
        content.title = reasons.isEmpty ? "Complete your profile" : "Action needed on your profile"
        content.body = body
        content.sound = .default
        center.add(UNNotificationRequest(identifier: "heal", content: content, trigger: nil))
    }

    func clear() {
        history.insert(Entry(date: .now, title: "Profile complete", body: "All required details are in place."), at: 0)
        center.removeDeliveredNotifications(withIdentifiers: ["heal"])
    }
}

/// Uploads crash reports to MobileHeal (`POST /api/crashes`) so the healer can open a defect and a fix PR.
/// Uncaught Objective-C exceptions are stored and sent on next launch; Swift errors can be reported directly.
enum CrashReporter {
    private static let key = "mh.pendingCrash"
    static var baseURL = URL(string: "http://localhost:8000")!

    static func install(baseURL: URL) {
        self.baseURL = baseURL
        NSSetUncaughtExceptionHandler { exception in
            let report: [String: Any] = [
                "exception": exception.name.rawValue, "message": exception.reason ?? "",
                "stack": exception.callStackSymbols.joined(separator: "\n"),
            ]
            UserDefaults.standard.set(report, forKey: key)
        }
        if let pending = UserDefaults.standard.dictionary(forKey: key) {
            UserDefaults.standard.removeObject(forKey: key)
            send(pending)
        }
    }

    static func report(_ error: Error, file: String = #fileID, line: Int = #line, function: String = #function) {
        send(["exception": String(describing: type(of: error)), "message": error.localizedDescription,
              "stack": "at \(function) (\(file.split(separator: "/").last ?? ""):\(line))"])
    }

    private static func send(_ report: [String: Any]) {
        var body = report
        body["platform"] = "ios"
        body["device"] = ProcessInfo.processInfo.hostName
        body["app_version"] = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "dev"
        body["screen"] = "Profile"
        var req = URLRequest(url: baseURL.appendingPathComponent("api/crashes"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        URLSession.shared.dataTask(with: req).resume()
    }
}
